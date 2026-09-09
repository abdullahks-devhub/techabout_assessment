#!/usr/bin/env python3
"""
clean.py -- PKHosting renewals cleanup.

Usage:
    python clean.py renewals_raw.csv

Reads a messy renewals export and writes:
    clean.csv  -- one row per kept, validated record
    issues.csv -- one row per problem found (dropped, changed, merged, flagged)

and prints a summary to stdout.

Design notes (see README.md for the fuller writeup):
  * Every parser lives in parsers.py and is pure / independently testable.
  * Nothing is silently discarded -- every row that is dropped, every field
    that is changed or assumed, and every duplicate that is merged gets a
    line in issues.csv with the reasoning.
  * A row is only DROPPED from clean.csv if a required field cannot be
    recovered at all (unparseable dates on both readings, an unrecoverable
    amount, or an invalid domain). Everything else is kept but flagged.
"""

import csv
import re
import sys
from collections import defaultdict

from parsers import (
    parse_date,
    parse_amount,
    parse_domain,
    parse_billing_cycle,
    parse_status,
)

CLEAN_FIELDS = [
    "record_id",
    "customer_id",
    "domain",
    "service",
    "billing_cycle_months",
    "amount_pkr",
    "registered_on",
    "renews_on",
    "status",
    "contact_email",
]

ISSUE_FIELDS = ["record_id", "field", "raw_value", "problem", "action_taken"]

# Rough, fixed rate used ONLY to report a USD renewal in the same PKR total
# for Part A's window analysis. Flagged loudly rather than silently applied.
USD_TO_PKR_RATE = 278.0


def clean_file(input_path):
    with open(input_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    clean_rows = []
    issues = []

    def log_issue(record_id, field, raw_value, problem, action_taken):
        issues.append(
            {
                "record_id": record_id,
                "field": field,
                "raw_value": raw_value,
                "problem": problem,
                "action_taken": action_taken,
            }
        )

    # ---- pass 1: parse every row independently -----------------------
    parsed = []
    for row in rows:
        rid = row.get("record_id", "").strip()
        drop = False

        customer_id = (row.get("customer_id") or "").strip()
        if not customer_id:
            log_issue(rid, "customer_id", row.get("customer_id", ""),
                      "customer_id is blank", "flagged; kept record (not enough grounds to drop)")
            customer_id = None

        domain, dom_problem = parse_domain(row.get("domain", ""))
        if dom_problem and domain is None:
            log_issue(rid, "domain", row.get("domain", ""), dom_problem, "dropped row; domain unrecoverable")
            drop = True
        elif dom_problem:
            log_issue(rid, "domain", row.get("domain", ""), dom_problem, "normalized")

        service_raw = row.get("service") or ""
        service = " ".join(service_raw.split())  # collapse stray whitespace
        if not service:
            log_issue(rid, "service", service_raw, "service is blank", "flagged; kept record")
        elif service != service_raw:
            log_issue(rid, "service", service_raw, "service had leading/trailing/internal extra whitespace",
                      f"normalized to '{service}'")

        cycle_months, cycle_problem = parse_billing_cycle(row.get("billing_cycle", ""))
        if cycle_problem:
            log_issue(rid, "billing_cycle", row.get("billing_cycle", ""), cycle_problem,
                      "dropped row; cycle unrecoverable and required for renewal math" if cycle_months is None else "normalized")
            if cycle_months is None:
                drop = True

        amount, currency, amount_problem = parse_amount(row.get("amount", ""))
        if amount is None:
            log_issue(rid, "amount", row.get("amount", ""), amount_problem or "unparseable amount",
                      "dropped row; amount unrecoverable")
            drop = True
        elif amount_problem:
            log_issue(rid, "amount", row.get("amount", ""), amount_problem, "parsed with assumption noted")

        registered_on, reg_problem = parse_date(row.get("registered_on", ""), "registered_on")
        if registered_on is None:
            log_issue(rid, "registered_on", row.get("registered_on", ""), reg_problem, "dropped row; date unrecoverable")
            drop = True
        elif reg_problem:
            log_issue(rid, "registered_on", row.get("registered_on", ""), reg_problem, "parsed with assumption noted")

        renews_on, ren_problem = parse_date(row.get("renews_on", ""), "renews_on")
        if renews_on is None:
            log_issue(rid, "renews_on", row.get("renews_on", ""), ren_problem, "dropped row; date unrecoverable")
            drop = True
        elif ren_problem:
            log_issue(rid, "renews_on", row.get("renews_on", ""), ren_problem, "parsed with assumption noted")

        status, status_problem = parse_status(row.get("status", ""))
        if status is None:
            log_issue(rid, "status", row.get("status", ""), status_problem, "dropped row; status unrecoverable")
            drop = True

        email = (row.get("contact_email") or "").strip()
        if email and "@" not in email:
            log_issue(rid, "contact_email", email, "does not look like a valid email", "flagged; kept as-is")

        parsed.append(
            {
                "record_id": rid,
                "customer_id": customer_id,
                "domain": domain,
                "service": service,
                "billing_cycle_months": cycle_months,
                "amount": amount,
                "currency": currency,
                "registered_on": registered_on,
                "renews_on": renews_on,
                "status": status,
                "contact_email": email,
                "drop": drop,
            }
        )

    # ---- pass 2: dedupe ------------------------------------------------
    # A "duplicate" is the same customer + domain + service (that's the
    # same subscription re-exported by a different billing system). Same
    # customer/domain with a DIFFERENT service is a distinct subscription
    # and is NOT deduped (see R030 in the sample data).
    groups = defaultdict(list)
    for p in parsed:
        if p["drop"] or not p["customer_id"] or not p["domain"]:
            continue
        key = (p["customer_id"], p["domain"], p["service"].strip().lower())
        groups[key].append(p)

    dup_drop_ids = set()
    for key, group in groups.items():
        if len(group) < 2:
            continue
        # Tie-break rule: keep the record with the higher amount (assume the
        # more recent/most-corrected billing-system export supersedes an
        # earlier one), drop the other(s), and log both raw amounts so the
        # decision is auditable.
        group_sorted = sorted(group, key=lambda p: p["amount"], reverse=True)
        keeper = group_sorted[0]
        for loser in group_sorted[1:]:
            dup_drop_ids.add(loser["record_id"])
            log_issue(
                loser["record_id"],
                "amount",
                loser["amount"],
                f"duplicate of {keeper['record_id']} (same customer/domain/service) with conflicting amount "
                f"({loser['amount']} vs {keeper['amount']})",
                f"dropped as duplicate; kept {keeper['record_id']} (higher amount assumed to be the corrected value)",
            )

    # ---- pass 3: assemble clean rows -----------------------------------
    kept = 0
    dropped = 0
    flagged_count = len([i for i in issues])
    status_totals = defaultdict(int)

    for p in parsed:
        if p["drop"] or p["record_id"] in dup_drop_ids:
            dropped += 1
            continue
        kept += 1
        status_totals[p["status"]] += 1
        clean_rows.append(
            {
                "record_id": p["record_id"],
                "customer_id": p["customer_id"] or "",
                "domain": p["domain"],
                "service": p["service"],
                "billing_cycle_months": p["billing_cycle_months"],
                "amount_pkr": round(p["amount"] * USD_TO_PKR_RATE, 2) if p["currency"] == "USD" else p["amount"],
                "registered_on": p["registered_on"].isoformat(),
                "renews_on": p["renews_on"].isoformat(),
                "status": p["status"],
                "contact_email": p["contact_email"],
            }
        )
        if p["currency"] == "USD":
            log_issue(
                p["record_id"], "amount", p["amount"],
                f"billed in USD ({p['amount']} USD); converted to PKR at a flat {USD_TO_PKR_RATE} rate for reporting consistency",
                f"converted to {round(p['amount'] * USD_TO_PKR_RATE, 2)} PKR (flag: rate is illustrative, not a live FX rate)",
            )

    with open("clean.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CLEAN_FIELDS)
        writer.writeheader()
        writer.writerows(clean_rows)

    with open("issues.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ISSUE_FIELDS)
        writer.writeheader()
        writer.writerows(issues)

    print(f"rows in:     {len(rows)}")
    print(f"rows kept:   {kept}")
    print(f"rows dropped:{dropped}")
    print(f"issues logged: {len(issues)}")
    print("totals by status (kept rows only):")
    for status in sorted(status_totals):
        print(f"  {status:10s} {status_totals[status]}")

    return clean_rows, issues


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python clean.py renewals_raw.csv", file=sys.stderr)
        sys.exit(1)
    clean_file(sys.argv[1])
