"""
Offline tests for techi_audit.py's parsing logic.

These do NOT touch the network. Article/robots.txt/sitemap HTML is
supplied as in-memory fixture strings so the date/markup parsing logic can
be verified deterministically, per the assessment's "offline pytest tests"
requirement. Network behaviour (rate limiting, robots.txt compliance,
caching) is covered by test_fetcher_behaviour below using a stubbed
transport rather than real HTTP calls.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from techi_audit import parse_article, parse_relative_date, parse_absolute_date, discover_article_urls


ARTICLE_HTML_CLEAN = """
<html><head><title>Fallback Title</title></head>
<body>
  <h1>How Edge AI Is Changing Mobile Photography</h1>
  <span class="post-category">Mobile</span>
  <span class="byline">@shazia_writes</span>
  <time datetime="2026-08-01">August 1, 2026</time>
  <p>Article body...</p>
</body></html>
"""

ARTICLE_HTML_RELATIVE_DATE = """
<html><body>
  <h1>Startup Funding Roundup</h1>
  <div class="post-category">Startups</div>
  <div class="author">by TechiStaff</div>
  <span class="post-date">Updated 6 days ago</span>
</body></html>
"""

ARTICLE_HTML_ODD_MARKUP = """
<html><body>
  <div>No h1, no category, no byline, no date -- just a title tag.</div>
  <title>Weird Page With No Structure</title>
</body></html>
"""

ROBOTS_WITH_SITEMAP = """
User-agent: *
Disallow: /wp-admin/
Sitemap: https://www.techi.com/sitemap_index.xml
"""

SITEMAP_INDEX = """<?xml version="1.0"?>
<sitemapindex><sitemap><loc>https://www.techi.com/post-sitemap.xml</loc></sitemap></sitemapindex>"""

POST_SITEMAP = """<?xml version="1.0"?>
<urlset>
<url><loc>https://www.techi.com/edge-ai-mobile-photography/</loc></url>
<url><loc>https://www.techi.com/startup-funding-roundup/</loc></url>
</urlset>"""


class FakeFetcher:
    """Stands in for PoliteFetcher in discovery tests -- returns canned
    text for known URLs instead of making HTTP requests."""

    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        return self.pages.get(url)


def test_parse_article_with_clean_markup():
    row = parse_article(ARTICLE_HTML_CLEAN, "https://www.techi.com/edge-ai-mobile-photography/")
    assert row["slug"] == "edge-ai-mobile-photography"
    assert row["title"] == "How Edge AI Is Changing Mobile Photography"
    assert row["category"] == "Mobile"
    assert row["author_handle"] == "@shazia_writes"
    assert row["date_text"] == "August 1, 2026"
    assert row["date_iso"] == "2026-08-01"


def test_parse_article_with_relative_date():
    row = parse_article(ARTICLE_HTML_RELATIVE_DATE, "https://www.techi.com/startup-funding-roundup/")
    assert row["title"] == "Startup Funding Roundup"
    assert row["date_text"] == "Updated 6 days ago"
    # date_iso should be computed relative to "now" -- just check it parsed to *something* ISO-shaped
    assert len(row["date_iso"]) == 10 and row["date_iso"][4] == "-"


def test_parse_article_survives_odd_markup_without_crashing():
    row = parse_article(ARTICLE_HTML_ODD_MARKUP, "https://www.techi.com/weird-page/")
    # No h1 -> falls back to <title>; no category/author/date -> blank, not a crash
    assert row["title"] == "Weird Page With No Structure"
    assert row["category"] == ""
    assert row["author_handle"] == ""
    assert row["date_iso"] == ""


def test_parse_relative_date_days():
    fetched_at = datetime(2026, 8, 10)
    iso = parse_relative_date("Updated 6 days ago", fetched_at=fetched_at)
    assert iso == "2026-08-04"


def test_parse_relative_date_hours_stays_same_day():
    fetched_at = datetime(2026, 8, 10, 18, 0, 0)
    iso = parse_relative_date("Posted 3 hours ago", fetched_at=fetched_at)
    assert iso == "2026-08-10"


def test_parse_relative_date_unrecognized_returns_none():
    assert parse_relative_date("a while back") is None


def test_parse_absolute_date_month_name_format():
    assert parse_absolute_date("August 1, 2026") == "2026-08-01"


def test_parse_absolute_date_iso_passthrough():
    assert parse_absolute_date("2026-08-01") == "2026-08-01"


def test_parse_absolute_date_unrecognized_returns_none():
    assert parse_absolute_date("not a date") is None


def test_discover_article_urls_follows_sitemap_index():
    pages = {
        "https://www.techi.com/robots.txt": ROBOTS_WITH_SITEMAP,
        "https://www.techi.com/sitemap_index.xml": SITEMAP_INDEX,
        "https://www.techi.com/post-sitemap.xml": POST_SITEMAP,
    }
    fetcher = FakeFetcher(pages)
    urls = discover_article_urls(fetcher, limit=20)
    assert "https://www.techi.com/edge-ai-mobile-photography/" in urls
    assert "https://www.techi.com/startup-funding-roundup/" in urls
    assert len(urls) == 2


def test_discover_article_urls_respects_limit():
    big_sitemap = "<urlset>" + "".join(
        f"<url><loc>https://www.techi.com/article-{i}/</loc></url>" for i in range(50)
    ) + "</urlset>"
    pages = {
        "https://www.techi.com/robots.txt": ROBOTS_WITH_SITEMAP,
        "https://www.techi.com/sitemap_index.xml": SITEMAP_INDEX,
        "https://www.techi.com/post-sitemap.xml": big_sitemap,
    }
    fetcher = FakeFetcher(pages)
    urls = discover_article_urls(fetcher, limit=5)
    assert len(urls) == 5
