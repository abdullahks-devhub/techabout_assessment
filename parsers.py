"""
Parsing helpers for the PKHosting renewals cleanup task.

Each parser is deliberately small and pure (no I/O) so it can be unit
tested in isolation and reused by clean.py. Every parser returns a
(value, problem) tuple: `problem` is None on success, or a short
human-readable string describing what was wrong / what was assumed.
This lets clean.py log every judgement call into issues.csv without
re-deriving the reasoning at the call site.
"""

import re
from datetime import date


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

DATE_RULE = (
    "Ambiguous numeric dates (both segments <= 12) are assumed DD/MM/YYYY, "
    "since the source business (PKHosting) is Pakistan-based and DD/MM is the "
    "locally standard format. Dates that are only valid one way (e.g. "
    "12/25/2025, where 25 cannot be a month) are parsed the way that makes "
    "them valid, regardless of the default. Dates that are invalid both ways "
    "(e.g. 32/13/2026, or a real-looking but impossible date like 31/02/2026) "
    "are dropped and logged, never guessed."
)


def parse_date(raw: str, field_name: str = "date"):
    """Parse a DD/MM/YYYY-or-MM/DD/YYYY date string.

    Returns (date_or_None, problem_or_None).
    """
    if raw is None or not str(raw).strip():
        return None, f"{field_name} is blank"

    raw = raw.strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
    if not m:
        return None, f"{field_name} '{raw}' does not match D/M/YYYY pattern"

    a, b, year = (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    a_valid_as_day = 1 <= a <= 31
    b_valid_as_month = 1 <= b <= 12
    b_valid_as_day = 1 <= b <= 31
    a_valid_as_month = 1 <= a <= 12

    dmy_ok = a_valid_as_day and b_valid_as_month
    mdy_ok = b_valid_as_day and a_valid_as_month

    def _try_build(day, month):
        try:
            return date(year, month, day)
        except ValueError:
            return None

    if dmy_ok and not mdy_ok:
        d = _try_build(day=a, month=b)
        if d:
            return d, None
        return None, f"{field_name} '{raw}' looked like DD/MM/YYYY but day {a} is invalid for month {b}"

    if mdy_ok and not dmy_ok:
        d = _try_build(day=b, month=a)
        if d:
            return d, None
        return None, f"{field_name} '{raw}' looked like MM/DD/YYYY but day {b} is invalid for month {a}"

    if dmy_ok and mdy_ok:
        # Genuinely ambiguous -- apply the documented default (DD/MM/YYYY).
        d = _try_build(day=a, month=b)
        if d:
            return d, f"{field_name} '{raw}' ambiguous D/M vs M/D; assumed DD/MM/YYYY per project default"
        # a/b passed the range check but the calendar rejected it (e.g. 30/02) -- try the other reading.
        d2 = _try_build(day=b, month=a)
        if d2:
            return d2, f"{field_name} '{raw}' ambiguous; DD/MM/YYYY invalid, used MM/DD/YYYY instead"
        return None, f"{field_name} '{raw}' invalid under both DD/MM and MM/DD readings"

    return None, f"{field_name} '{raw}' invalid under both DD/MM and MM/DD readings (out-of-range day/month)"


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

_CURRENCY_WORD_USD = re.compile(r"\$|USD", re.IGNORECASE)
_CURRENCY_WORD_PKR = re.compile(r"PKR|Rs\.?", re.IGNORECASE)


def parse_amount(raw: str):
    """Parse a messy currency string.

    Returns (amount_float_or_None, currency_str_or_None, problem_or_None).
    amount_float is signed (parentheses => negative). currency is 'PKR' or
    'USD'; if no currency marker is present PKR is assumed (the export is a
    PKR-billing system) and that assumption is logged.
    """
    if raw is None or not str(raw).strip():
        return None, None, "amount is blank"

    text = str(raw).strip()
    problem = None

    is_negative = False
    if text.startswith("(") and text.endswith(")"):
        is_negative = True
        text = text[1:-1].strip()

    # South-Asian invoice convention: a trailing "/-" (e.g. "Rs 2500/-") is
    # decorative, not a minus sign -- strip it before checking for a sign.
    text = text.replace("/-", "").strip()

    if re.match(r"^-\s*\d", text):
        is_negative = True

    currency = None
    if _CURRENCY_WORD_USD.search(text):
        currency = "USD"
        text = _CURRENCY_WORD_USD.sub("", text)
    elif _CURRENCY_WORD_PKR.search(text):
        currency = "PKR"
        text = _CURRENCY_WORD_PKR.sub("", text)
    else:
        currency = "PKR"
        problem = "no currency marker found; assumed PKR"

    # Strip everything except digits and the decimal point; the sign is
    # already captured in is_negative above. Currency abbreviations are
    # removed first (above) so a stray "Rs." doesn't leave a bogus decimal
    # point behind.
    numeric_text = re.sub(r"[^\d.]", "", text)
    numeric_text = numeric_text.strip()
    if numeric_text in ("", "."):
        return None, currency, "amount had a currency/format marker but no readable number"

    try:
        amount = float(numeric_text)
    except ValueError:
        return None, currency, f"amount '{raw}' could not be parsed as a number"

    if is_negative:
        amount = -abs(amount)

    return amount, currency, problem


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------

_DOMAIN_RE = re.compile(
    r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)


def parse_domain(raw: str):
    """Canonicalize a messy domain string.

    Returns (canonical_domain_or_None, problem_or_None).
    Lowercases, strips whitespace, strips a leading protocol/www, strips a
    trailing slash/path, and validates the remaining label structure.
    """
    if raw is None or not str(raw).strip():
        return None, "domain is blank"

    original = raw
    text = raw.strip().lower()

    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    text = text.split("/")[0]  # drop any path
    text = re.sub(r"\s+", "", text)  # export artifacts sometimes inject spaces

    if ".." in text:
        return None, f"domain '{original}' has a malformed double dot"

    if not _DOMAIN_RE.match(text):
        return None, f"domain '{original}' does not look like a valid domain after cleanup ('{text}')"

    problem = None
    if text != original.strip().lower():
        problem = f"domain normalized from '{original.strip()}' to '{text}'"

    return text, problem


# ---------------------------------------------------------------------------
# Billing cycle
# ---------------------------------------------------------------------------

def parse_billing_cycle(raw: str):
    """Normalize a billing cycle string to an integer number of months.

    Returns (months_int_or_None, problem_or_None).
    """
    if raw is None or not str(raw).strip():
        return None, "billing_cycle is blank"

    text = str(raw).strip().lower()

    if text in ("monthly", "month", "1 month", "1mo"):
        return 1, None
    if text in ("yearly", "annual", "annually", "1yr", "1 yr", "1 year"):
        return 12, None

    m = re.match(r"^(\d+)\s*(month|months)?$", text)
    if m:
        return int(m.group(1)), None

    m = re.match(r"^(\d+)\s*(yr|yrs|year|years)$", text)
    if m:
        return int(m.group(1)) * 12, None

    return None, f"billing_cycle '{raw}' not recognised"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

STATUS_MAP = {
    "active": "active",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "suspended": "suspended",
    "expired": "expired",
    "pending renewal": "pending",
    "pending": "pending",
}

VALID_STATUSES = {"active", "cancelled", "suspended", "expired", "pending"}


def parse_status(raw: str):
    """Normalize a status string into the fixed set VALID_STATUSES.

    Returns (status_or_None, problem_or_None).
    """
    if raw is None or not str(raw).strip():
        return None, "status is blank"

    text = str(raw).strip().lower()
    if text in STATUS_MAP:
        return STATUS_MAP[text], None

    return None, f"status '{raw}' not recognised (fixed set: {sorted(VALID_STATUSES)})"
