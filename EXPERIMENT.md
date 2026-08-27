# Evaluation design

Written before any subscription existed and before any failure was induced.
No cohort reassignment or metric redefinition after halts start. If I break
my own protocol somewhere, it gets published as a deviation in RESULTS.md
rather than quietly fixed.

## Hypothesis

For test-mode subscriptions that reach `halted`, running the Vaapsi pipeline
(diagnose, score, act within hard caps) recovers a larger share of
subscriptions to charged/paid within 7 days than Razorpay's default retries
alone.

## Design

One plan for everything: Vaapsi Recovery Demo, ₹499/month, 6 cycles, created
through the API with `customer_notify=0` so Razorpay itself sends no email.
The only difference between cohorts is the agent.

60 subscriptions, 30 treatment and 30 control, assigned alternately in
creation order (slot 0 treatment, slot 1 control, and so on) so time-of-day
or creation-order drift can't pool in one group. The assignment is written
into `data/cohort_manifest.csv` and the SQLite cohorts table at creation,
keyed by Razorpay subscription id.

Failures are induced identically for every subscription: the dashboard's
"Charge this now" with Charge as Failure four times, which is Razorpay's
documented test-mode path to `halted`.

Control gets Razorpay's default retries and nothing else from me; the action
layer checks the cohort and skips, and the skip is visible in the ledger.
Treatment gets the full pipeline with every rail on: clamp to invoice, human
approval above ₹500, one outreach per 48 hours, at most 3 attempts per halt,
quiet hours 21:00–09:00 IST, 6 hours cooling after the halt, stop on charge
or cancellation, kill switch available.

## Metrics

| # | Metric | Definition | Source |
|---|---|---|---|
| M1 | Recovery rate (primary) | halted subs reaching charged (subscription) or paid (link/invoice) within 7 days of halt, divided by total halted, per cohort | ledger + webhook events |
| M2 | Rupees recovered | sum of successful recovery amounts, per cohort | ledger |
| M3 | Time to recover | median hours from halted event to recovery event | ledger timestamps |
| M4 | Outreach efficiency | recoveries divided by outreach sends | ledger |
| M5 | False outreach | any outreach to a subscription that charged, cancelled, or completed before the action fired; must be 0, provable from ledger rows | stop-on-charge void rows |

## Reporting

RESULTS.md is generated from the ledger, not written by hand: raw counts, no
rounding above the percentage, per-cohort N published. Subscriptions that
don't recover are counted and classified (expired link, attempts exhausted,
still open) rather than dropped. If a control subscription ever receives an
action, that's a protocol violation I publish, not a row I delete.

## Known limits

Test mode end to end; failures are dashboard-simulated, not real bank
declines. Outreach is logged rather than delivered, since test mode sends no
real SMS or email. One plan, one merchant, one operator, seven days. This
measures the mechanism, not the market.

## Addendum, written before any halt was induced

The first batch of subscriptions was created without customer objects. With
no customer attached, Razorpay's "Charge this now" runs an
invoice-notification flow instead of the subscription charge simulation. I
observed this live: 6 simulated attempts produced standalone created-status
payments and zero subscription charge cycles or webhooks.

Fix, applied before any experiment data existed: 60 test customers created
through the API, attached to fresh subscriptions on the same plan with the
same interleaved assignment and customer_notify=0, notes stamped substrate=v2.
All v1 subscriptions were cancelled and archived to
cohort_manifest_v1_cancelled.csv. Since none of them was ever charged as
failure, there was no halt data to discard and this addendum discards nothing.
