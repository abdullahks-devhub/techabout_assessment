# TechAbout assessment — PKHosting renewals cleanup & TECHi metadata reader

## Setup

Python 3.10+. No paid tools, no credentials.

```bash
pip install requests beautifulsoup4 pytest
python clean.py renewals_raw.csv       # Part A -> clean.csv, issues.csv, stdout summary
python techi_audit.py --limit 20       # Part B -> techi_articles.csv (needs live internet)
pytest -q                              # all offline tests
```

## What's in here

```
renewals_raw.csv     synthetic input data (see "About the input data" below)
parsers.py           pure parsing functions: date, amount, domain, billing_cycle, status
clean.py             Part A pipeline: reads raw csv, applies parsers.py, writes clean.csv + issues.csv
NOTES.md             the 2026-08-03 -> 2026-09-02 renewal total, with every judgment call spelled out
techi_audit.py        Part B: robots.txt/sitemap-driven scraper for TECHi.com article metadata
tests/test_parsers.py         31 offline unit tests for the Part A parsers
tests/test_techi_audit.py     11 offline tests for Part B's parsing logic, using fixture HTML
```

## About the input data

The task brief describes `renewals_raw.csv` as a 34-row export but the
actual file wasn't attached to the brief I received. Rather than guess at
one or two edge cases, I built a 34-row synthetic dataset that deliberately
exercises every issue category the brief calls out by name: both ambiguous
and unambiguous date-format collisions, a calendar-invalid date, a
completely out-of-range date, amounts with commas/parentheses/currency
words/a trailing "/-"/no currency marker at all, a USD line, a blank
amount, malformed and whitespace-mangled domains, a double-dot domain, a
true duplicate with conflicting amounts, a same-customer-different-service
non-duplicate (to make sure the dedupe logic isn't overzealous), every
status value in the fixed set, a missing customer_id, a malformed email,
and window-boundary dates on both edges of the Aug 3 - Sep 2 range. This
was the fastest way to get a dataset that actually stresses the code the
way the brief implies the real one would, and it means `clean.py` and its
tests are demonstrably exercised end-to-end rather than just written on
faith. If a real `renewals_raw.csv` shows up, `python clean.py
renewals_raw.csv` runs against it unchanged — nothing here is hardcoded
to this specific dataset.

## Part A — key decisions (see NOTES.md for the full renewal-total writeup)

- **Ambiguous dates** (`D/M/YYYY` valid both ways, e.g. `05/06/2026`):
  default to **DD/MM/YYYY** (Pakistan-based business). When only one
  reading is calendar-valid, that reading is used regardless of the
  default. Both-invalid dates are dropped and logged, never guessed.
- **Duplicates**: same `(customer_id, domain, service)` more than once ->
  keep the higher amount, log the loser with both raw values. Same
  customer+domain but a *different* service is not a duplicate.
- **A row is only dropped** when a required field is truly unrecoverable
  (unparseable date, amount, domain, cycle, or status). Everything else —
  blank customer_id, a malformed email, a normalized domain, an assumed
  currency — is kept and flagged in `issues.csv`, never silently altered.
- **USD line**: converted to PKR at a flat, clearly-labeled illustrative
  rate for reporting purposes only; flagged as not a live FX rate.
- **Refund / negative amounts**: kept as real transactions, but treated as
  money going out, not a renewal coming in, for the purposes of NOTES.md's
  total.

## Part B — TECHi.com scraper

`techi_audit.py` discovers article URLs from `robots.txt`'s `Sitemap:`
directive(s) (following a sitemap index down to the actual post sitemap if
needed) rather than hardcoding article paths, with a homepage-link fallback
if no sitemap is advertised. It respects `robots.txt` via
`urllib.robotparser`, paces requests to 1/second, sends a real, honest
User-Agent string, and caches every fetched page to disk keyed by a hash of
the URL — a warm rerun makes zero new HTTP requests unless `--refresh` is
passed. Article-level failures (timeout, non-200, missing elements) are
caught and logged per-URL; the run continues rather than aborting.

**Important limitation, stated plainly:** the sandbox this was written in
enforces an egress allowlist that does not include `techi.com` — a live
request from here returns a proxy-level 403 (`host_not_allowed`), not a
result from the real site. I verified this directly rather than assuming
it. Because of that, `techi_audit.py` could not be run end-to-end against
the live site in this environment. It was instead developed and verified
against recorded HTML fixtures that mirror TECHi's expected markup shapes
(`tests/test_techi_audit.py`) — clean markup, a relative-date variant, and
a deliberately structure-less page to confirm it degrades to blanks
instead of raising. Running `python techi_audit.py` from a machine with
normal internet access should work as written; I'd want to confirm actual
selector names against TECHi's live markup in a follow-up, since I
couldn't inspect it here.

## Tests

`pytest -q` runs 42 tests, all offline:
- **Part A (31 tests)**: date parsing (unambiguous both ways, genuinely
  ambiguous, calendar-invalid, both-segments-invalid, leap years), amount
  parsing (commas, `Rs.` regression — see below, parentheses, bare
  negative, the trailing `/-` convention, USD detection, blank/garbage
  inputs), domain canonicalization (protocol/www stripping, internal
  whitespace, double dots, non-domain text), billing-cycle normalization,
  and status normalization.
- **Part B (11 tests)**: article metadata extraction on clean markup, a
  relative-date variant, and deliberately broken markup; relative/absolute
  date math; sitemap-index-following URL discovery; and the discovery
  limit being respected.

One test (`test_amount_rs_abbreviation_with_period_does_not_corrupt_value`)
is a regression test for a real bug I hit while building this: `"Rs. 12,000"`
was initially parsing to `0.12` because the period in the "Rs." abbreviation
was surviving the digit/decimal-point filter and being read as the decimal
separator. Currency markers are now stripped from the string *before*
numeric extraction, not after.

## What I'd improve with more time

- Part B's CSS selectors (`.category`, `.byline`, `.post-date`, etc.) are
  reasonable guesses at common WordPress-theme markup, not verified
  against TECHi's actual HTML, given the network restriction above. First
  thing I'd do with real access is inspect a live article and tighten
  these.
- The USD->PKR rate is hardcoded; a real version should pull a dated rate
  (even a simple daily-cached one) rather than a fixed constant.
- `clean.py`'s dedupe currently only compares exact `(customer_id, domain,
  service)` triples; a fuzzier match (e.g. domain typos across the two
  duplicate rows) would catch more real-world near-duplicates, at the cost
  of more false-positive risk.
- I'd add a `--strict` flag to `clean.py` that turns every "kept but
  flagged" row (blank customer_id, malformed email, etc.) into a drop, for
  teams that want a stricter default.
