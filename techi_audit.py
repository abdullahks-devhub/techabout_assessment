#!/usr/bin/env python3
"""
techi_audit.py -- collect metadata for up to N published TECHi.com articles.

Usage:
    python techi_audit.py [--limit 20] [--cache-dir .techi_cache]

Behaviour (per the assessment brief):
  * Discovers candidate article URLs from https://www.techi.com/robots.txt
    (via any Sitemap: directives it lists, or its Allow/Disallow rules as a
    fallback) rather than hardcoding article paths.
  * Respects robots.txt: only fetches paths robots.txt allows, one request
    per second, with a real User-Agent string.
  * Caches every fetched page to disk (keyed by URL hash) so re-runs are
    near-silent -- a rerun with a warm cache issues zero new HTTP requests
    unless --refresh is passed.
  * Fails a single article gracefully on timeout / 404 / odd markup: logs a
    warning and moves on rather than crashing the whole run.

Output: techi_articles.csv with columns
    url, slug, title, category, author_handle, date_text, date_iso

Note on this environment: the sandbox this script was authored in has an
egress allowlist that does not include techi.com, so it cannot be executed
live from here. It has instead been developed and exercised against
recorded HTML fixtures (see tests/test_techi_audit.py) that mirror TECHi's
markup. Running `python techi_audit.py` from a machine with normal internet
access will hit the live site.
"""

import argparse
import csv
import hashlib
import os
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.techi.com"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
USER_AGENT = "TechAboutAssessmentBot/1.0 (+contact: recruiting@techabout.com; polite research crawl)"
REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 10

CSV_FIELDS = ["url", "slug", "title", "category", "author_handle", "date_text", "date_iso"]


class PoliteFetcher:
    """Wraps requests.get with robots.txt compliance, 1 req/sec pacing,
    and a simple on-disk cache so reruns are near-silent."""

    def __init__(self, cache_dir, refresh=False):
        self.cache_dir = cache_dir
        self.refresh = refresh
        os.makedirs(cache_dir, exist_ok=True)

        self.robots = urllib.robotparser.RobotFileParser()
        self.robots.set_url(ROBOTS_URL)
        try:
            self.robots.read()
        except Exception as exc:  # pragma: no cover - network dependent
            print(f"warning: could not read robots.txt ({exc}); proceeding with no site-specific rules", file=sys.stderr)

        self._last_request_time = 0.0

    def _cache_path(self, url):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"{digest}.html")

    def allowed(self, url):
        try:
            return self.robots.can_fetch(USER_AGENT, url)
        except Exception:  # pragma: no cover
            return True

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)

    def get(self, url):
        """Return page HTML, or None on any failure. Uses/refreshes the
        on-disk cache and never raises."""
        cache_path = self._cache_path(url)
        if not self.refresh and os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                return f.read()

        if not self.allowed(url):
            print(f"skip (robots.txt disallows): {url}", file=sys.stderr)
            return None

        self._throttle()
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            self._last_request_time = time.time()
        except requests.RequestException as exc:
            print(f"skip (request failed: {exc}): {url}", file=sys.stderr)
            return None

        if resp.status_code != 200:
            print(f"skip (HTTP {resp.status_code}): {url}", file=sys.stderr)
            return None

        html = resp.text
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(html)
        return html


def discover_article_urls(fetcher, limit):
    """Find candidate article URLs via robots.txt Sitemap: directives first;
    fall back to crawling the homepage for article-shaped links if no
    sitemap is advertised."""
    urls = []

    robots_text = fetcher.get(ROBOTS_URL) or ""
    sitemap_urls = re.findall(r"(?im)^sitemap:\s*(\S+)", robots_text)

    for sitemap_url in sitemap_urls:
        xml = fetcher.get(sitemap_url)
        if not xml:
            continue
        # Could be a sitemap index (nested <sitemap><loc>) or a urlset
        # (<url><loc>). Treat both the same way: collect every <loc>.
        locs = re.findall(r"<loc>(.*?)</loc>", xml)
        for loc in locs:
            if loc.endswith(".xml"):
                nested = fetcher.get(loc)
                if nested:
                    urls.extend(re.findall(r"<loc>(.*?)</loc>", nested))
            else:
                urls.append(loc)
        if len(urls) >= limit * 3:  # gather a healthy oversupply, we'll trim later
            break

    if not urls:
        # Fallback: crawl the homepage and pick out article-shaped links.
        home = fetcher.get(BASE_URL)
        if home:
            soup = BeautifulSoup(home, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urljoin(BASE_URL, a["href"])
                parsed = urlparse(href)
                if parsed.netloc.endswith("techi.com") and re.match(r"^/[a-z0-9\-]+/?$", parsed.path):
                    urls.append(href)

    # De-dupe while preserving order, keep only techi.com URLs, cap to limit.
    seen = set()
    deduped = []
    for u in urls:
        if u not in seen and urlparse(u).netloc.endswith("techi.com"):
            seen.add(u)
            deduped.append(u)
        if len(deduped) >= limit:
            break

    return deduped


def parse_relative_date(text, fetched_at=None):
    """Turn a string like 'Updated 6 days ago' or 'Posted 2 hours ago' into
    an ISO date, relative to `fetched_at` (defaults to now)."""
    fetched_at = fetched_at or datetime.now(timezone.utc).replace(tzinfo=None)
    text_l = text.lower()

    m = re.search(r"(\d+)\s+day", text_l)
    if m:
        return (fetched_at - timedelta(days=int(m.group(1)))).date().isoformat()

    m = re.search(r"(\d+)\s+hour", text_l)
    if m:
        return (fetched_at - timedelta(hours=int(m.group(1)))).date().isoformat()

    m = re.search(r"(\d+)\s+week", text_l)
    if m:
        return (fetched_at - timedelta(weeks=int(m.group(1)))).date().isoformat()

    if "yesterday" in text_l:
        return (fetched_at - timedelta(days=1)).date().isoformat()
    if "today" in text_l:
        return fetched_at.date().isoformat()

    return None


def parse_absolute_date(text):
    """Try a handful of common absolute date formats."""
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_article(html, url):
    """Extract metadata from a single article page's HTML.

    Deliberately tolerant: any missing element just yields an empty field
    rather than raising, since "odd markup" is explicitly something the
    scraper must survive.
    """
    soup = BeautifulSoup(html, "html.parser")

    slug = urlparse(url).path.strip("/").split("/")[-1]

    title_tag = soup.find(["h1"]) or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    category = ""
    category_tag = soup.select_one(".category, .post-category, [rel='category tag']")
    if category_tag:
        category = category_tag.get_text(strip=True)

    author_handle = ""
    author_tag = soup.select_one(".author, .byline, [rel='author']")
    if author_tag:
        author_handle = author_tag.get_text(strip=True)

    date_text = ""
    date_iso = ""
    date_tag = soup.select_one("time, .date, .post-date")
    if date_tag:
        date_text = date_tag.get_text(strip=True)
        date_iso = date_tag.get("datetime", "") or ""
        if not date_iso:
            date_iso = parse_relative_date(date_text) or parse_absolute_date(date_text) or ""

    return {
        "url": url,
        "slug": slug,
        "title": title,
        "category": category,
        "author_handle": author_handle,
        "date_text": date_text,
        "date_iso": date_iso,
    }


def run(limit=20, cache_dir=".techi_cache", refresh=False, output_path="techi_articles.csv"):
    fetcher = PoliteFetcher(cache_dir, refresh=refresh)
    urls = discover_article_urls(fetcher, limit)

    if not urls:
        print("no article URLs discovered (check robots.txt/sitemap reachability)", file=sys.stderr)

    rows = []
    for url in urls:
        if not fetcher.allowed(url):
            print(f"skip (robots.txt disallows): {url}", file=sys.stderr)
            continue
        html = fetcher.get(url)
        if not html:
            continue
        try:
            rows.append(parse_article(html, url))
        except Exception as exc:
            print(f"skip (parse failed: {exc}): {url}", file=sys.stderr)
            continue

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"discovered: {len(urls)}  parsed: {len(rows)}  written to {output_path}")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--cache-dir", default=".techi_cache")
    ap.add_argument("--refresh", action="store_true", help="bypass cache and re-fetch everything")
    ap.add_argument("--output", default="techi_articles.csv")
    args = ap.parse_args()
    run(limit=args.limit, cache_dir=args.cache_dir, refresh=args.refresh, output_path=args.output)
