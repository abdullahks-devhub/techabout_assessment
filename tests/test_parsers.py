"""
Offline unit tests for parsers.py.

These test the pure parsing functions directly -- no network, no files.
Run with: pytest -q
"""

import datetime
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from parsers import (
    parse_date,
    parse_amount,
    parse_domain,
    parse_billing_cycle,
    parse_status,
)


# ---------------------------------------------------------------------------
# parse_date
# ---------------------------------------------------------------------------

def test_date_unambiguous_dmy():
    # day=25 can't be a month -> must be DD/MM/YYYY
    d, problem = parse_date("25/12/2025")
    assert d == datetime.date(2025, 12, 25)
    assert problem is None


def test_date_unambiguous_mdy():
    # day-position 25 can't be a month -> must be MM/DD/YYYY
    d, problem = parse_date("12/25/2025")
    assert d == datetime.date(2025, 12, 25)
    assert problem is None


def test_date_genuinely_ambiguous_defaults_to_dmy():
    d, problem = parse_date("05/06/2026")
    assert d == datetime.date(2026, 6, 5)
    assert problem is not None and "ambiguous" in problem


def test_date_impossible_calendar_date_is_dropped():
    # Feb 31st doesn't exist under either reading
    d, problem = parse_date("31/02/2026")
    assert d is None
    assert "invalid" in problem


def test_date_both_segments_out_of_range_is_dropped():
    d, problem = parse_date("32/13/2026")
    assert d is None
    assert problem is not None


def test_date_blank_is_flagged():
    d, problem = parse_date("", "renews_on")
    assert d is None
    assert "blank" in problem


def test_date_leap_year_edge():
    d, problem = parse_date("29/02/2024")  # 2024 is a leap year
    assert d == datetime.date(2024, 2, 29)


# ---------------------------------------------------------------------------
# parse_amount
# ---------------------------------------------------------------------------

def test_amount_plain_pkr_with_commas():
    amt, cur, problem = parse_amount("PKR 3,500")
    assert amt == 3500.0
    assert cur == "PKR"


def test_amount_rs_abbreviation_with_period_does_not_corrupt_value():
    # Regression test: "Rs." previously left a stray '.' that turned
    # 12,000 into 0.12.
    amt, cur, problem = parse_amount("Rs. 12,000")
    assert amt == 12000.0
    assert cur == "PKR"


def test_amount_parentheses_means_negative():
    amt, cur, problem = parse_amount("(1500)")
    assert amt == -1500.0


def test_amount_bare_negative():
    amt, cur, problem = parse_amount("-800")
    assert amt == -800.0


def test_amount_trailing_slash_dash_is_not_a_negative_sign():
    amt, cur, problem = parse_amount("Rs 2500 /-")
    assert amt == 2500.0
    assert amt > 0


def test_amount_usd_marker_detected():
    amt, cur, problem = parse_amount("$45.00")
    assert amt == 45.0
    assert cur == "USD"


def test_amount_blank_is_unrecoverable():
    amt, cur, problem = parse_amount("")
    assert amt is None
    assert "blank" in problem


def test_amount_no_currency_marker_assumes_pkr_and_flags_it():
    amt, cur, problem = parse_amount("2400")
    assert amt == 2400.0
    assert cur == "PKR"
    assert problem is not None and "assumed PKR" in problem


def test_amount_garbage_is_unrecoverable():
    amt, cur, problem = parse_amount("N/A")
    assert amt is None


# ---------------------------------------------------------------------------
# parse_domain
# ---------------------------------------------------------------------------

def test_domain_strips_protocol_and_www_and_lowercases():
    d, problem = parse_domain("HTTPS://WWW.Brightweb.PK/")
    assert d == "brightweb.pk"


def test_domain_strips_internal_whitespace():
    d, problem = parse_domain(" skynet .pk ")
    assert d == "skynet.pk"


def test_domain_double_dot_is_invalid():
    d, problem = parse_domain("example..pk")
    assert d is None
    assert "double dot" in problem


def test_domain_non_domain_text_is_invalid():
    d, problem = parse_domain("  VPS Hosting  ")
    assert d is None


def test_domain_blank_is_invalid():
    d, problem = parse_domain("")
    assert d is None


def test_domain_already_clean_has_no_problem_reported():
    d, problem = parse_domain("hostking.pk")
    assert d == "hostking.pk"
    assert problem is None


# ---------------------------------------------------------------------------
# parse_billing_cycle
# ---------------------------------------------------------------------------

def test_cycle_plain_integer():
    months, problem = parse_billing_cycle("12")
    assert months == 12


def test_cycle_with_months_word():
    months, problem = parse_billing_cycle("12 months")
    assert months == 12


def test_cycle_1yr_shorthand():
    months, problem = parse_billing_cycle("1yr")
    assert months == 12


def test_cycle_monthly_word():
    months, problem = parse_billing_cycle("Monthly")
    assert months == 1


def test_cycle_blank_is_unrecoverable():
    months, problem = parse_billing_cycle("")
    assert months is None


def test_cycle_nonsense_is_unrecoverable():
    months, problem = parse_billing_cycle("ad-hoc")
    assert months is None


# ---------------------------------------------------------------------------
# parse_status
# ---------------------------------------------------------------------------

def test_status_normalizes_case():
    s, problem = parse_status("ACTIVE")
    assert s == "active"


def test_status_pending_renewal_variant():
    s, problem = parse_status("pending renewal")
    assert s == "pending"


def test_status_unrecognized_is_flagged():
    s, problem = parse_status("archived")
    assert s is None
