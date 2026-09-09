# NOTES — Renewal window analysis (2026-08-03 to 2026-09-02)

Today is treated as **2026-08-03**. The window is inclusive on both ends:
`2026-08-03 <= renews_on <= 2026-09-02`.

All figures below are computed from `clean.csv` (i.e. after the dedupe,
drop, and normalization rules already applied and logged in `issues.csv`).
Re-run: `python clean.py renewals_raw.csv` regenerates both files.

## Rules used upstream (repeated here since they affect this total)

**Ambiguous dates:** when a `D/M/YYYY`-shaped date is valid under *both*
DD/MM and MM/DD (e.g. `05/06/2026`), it is assumed **DD/MM/YYYY**, since
PKHosting is a Pakistan-based business and that's the locally standard
format. When only one reading is calendar-valid (e.g. `12/25/2025`, where
25 can't be a month), that reading wins regardless of the default. Dates
invalid under both readings (`31/02/2026`, `32/13/2026`) are dropped, not
guessed — see `issues.csv` for each case.

**Duplicates:** a duplicate is the same `(customer_id, domain, service)`
appearing more than once — i.e. the same subscription re-exported by a
different billing system. The record with the **higher amount** is kept,
on the assumption that a corrected/later export is more likely to be the
right one than a coincidentally-lower earlier one; the loser is logged
with both raw amounts so the call is auditable, not silent. Same
customer + same domain but a **different service** (R030: `sunrise.pk`,
VPS vs. R009/R010's Shared Hosting) is *not* treated as a duplicate — it's
a second, distinct subscription.

## Records whose renewal date falls in the window

| record_id | renews_on | status | amount_pkr | note |
|---|---|---|---|---|
| R022 | 2026-08-03 | active | 3,300 | exact window start |
| R018 | 2026-08-15 | active | 5,000 | |
| R016 | 2026-08-14 | expired | 1,000 | already lapsed before today |
| R017 | 2026-08-19 | pending | 1,800 | "pending renewal" |
| R005 | 2026-08-20 | active | 12,510 | billed in USD, converted (see below) |
| R019 | 2026-08-20 | cancelled | 2,600 | customer cancelled |
| R023 | 2026-09-02 | active | 3,300 | exact window end |

Boundary checks that came out **excluded**, confirming the inclusive-range
logic: R020 (renews 2026-08-02, one day too early) and R021 (renews
2026-09-03, one day too late). No `suspended` record happens to fall in
this particular window, but the same reasoning below would apply if one did.

## Status treatment — the actual judgment calls

- **active** → counted. This is the uncontroversial core of "renewals due."
- **cancelled** (R019) → **excluded**. A cancelled service will not
  actually renew or be billed; counting it would overstate expected
  revenue. Kept visible in the table above for transparency, but not in
  the total.
- **expired** (R016) → **excluded** from the total, with a caveat. "Expired"
  reads as *already lapsed*, not *about to renew* — arguably it needed
  action before the window even started. It's a real ambiguity in the
  data (does the business's "expired" mean "auto-cancelled" or "grace
  period, still recoverable"?), so I've flagged it rather than assumed.
- **pending** / "pending renewal" (R017) → **included**. This status
  reads as the service is actively mid-renewal, which is exactly what
  "renewals due" should capture.
- **suspended** → would be **excluded** by the same logic as cancelled
  (suspended-for-non-payment services aren't reliably going to renew),
  if one existed in this window.

## The refund (R003) and the stray negative (R029)

R003 is a **refund** (`(1500)`, noted "customer refund issued") and R029
is a **negative adjustment** (`-800`, unparenthesized). Neither's
`renews_on` falls in the 08-03 → 09-02 window, so neither affects this
particular total — but the rule for if one had: a negative/refund line
represents money going *out*, not a renewal coming *in*, so it would be
excluded from a "renewals due" total rather than netted against it. It's
kept in `clean.csv` (not dropped) because it's a real, explainable
transaction — just not the kind this specific total is asking about.

## The USD line (R005)

R005 is billed in USD (`$45.00`). It **is** in the window
(renews 2026-08-20), so it has to be dealt with rather than set aside.
I converted it to PKR at a flat illustrative rate (**278 PKR/USD**) purely
for the purposes of this total — this is explicitly *not* a live FX rate,
and `issues.csv` flags the conversion so nobody mistakes it for billing
data. $45.00 → 12,510 PKR.

## The duplicate (R009 / R010)

Same customer, domain, and service, conflicting amounts (4,000 vs 4,200).
R010 (4,200) was kept per the dedupe rule above. Its `renews_on`
(2027-01-01) is outside this window regardless, so the dedupe choice
doesn't move this particular total — but it would for a Jan 2027 window,
which is why the rule is spelled out rather than left implicit.

## Totals

| Scope | Records | PKR |
|---|---|---|
| **Active-only (the conservative, "money actually expected" number)** | R022, R018, R005, R023 | **24,110** |
| Active + pending (also counts services still mid-renewal) | adds R017 | **25,910** |
| Every in-window record regardless of status (upper bound; includes cancelled + expired) | adds R016, R019 | **29,510** |

**Headline number: 25,910 PKR** (active + pending — i.e. everything
genuinely still on track to renew, excluding cancelled and expired). I'd
present this one to the business with the caveat on "expired" above,
since reasonable people could argue an expired-but-not-yet-cancelled
service belongs in the total too (which would make it 26,910).

## What I'd flag to a human before trusting this number

1. Whether "expired" in this system means "will auto-cancel" or "still
   in a grace/recovery period" — materially changes the total.
2. The 278 PKR/USD rate is illustrative only; a real answer needs the
   business's actual billing-day FX source.
3. The DD/MM default for ambiguous dates should be confirmed against
   PKHosting's actual billing system locale rather than assumed from the
   company's home country.
