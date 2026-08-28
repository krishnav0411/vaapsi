# RESULTS.md — the live experiment

> Status: **live experiment running.** Written from the ledger, not by hand.
> Counts below are current as of the last regeneration; the table freezes
> before submission with final per-cohort denominators and deviation notes.

Cohort design and metric definitions are pre-registered in
[`EXPERIMENT.md`](EXPERIMENT.md) — written before any subscription existed.
Any protocol deviation gets published here rather than quietly fixed.

## Live counts (as of this file's last regeneration)

| Count | Value | Source |
|---|---|---|
| Subscriptions created | 60 (30 T / 30 C, interleaved) | cohorts table |
| Halt events received | 15 (incl. 6 flushed from Razorpay's retry queue after the dispatcher stall — idempotently deduplicated) | webhook_events |
| Recovery episodes opened | 5 | episodes table |
| Outreach sends | 1 (₹499 link, in dead-letter→drained story above) | ledger |
| Recovered | ₹0 (0 of 5 within 7d, TREATMENT; CONTROL: no action by design) | ledger |
| False outreach (M5) | 0 | ledger stop-void rows |

## Why the zeros

Policy windows are doing exactly what they were designed to do: 6 hours of
cooling after each halt, 48 hours between outreaches, max 3 attempts, and a
human gate above ₹500. Four of five episodes are still inside their first
cooling window at the time of writing. The experiment measures the mechanism
over a 7-day window; the honest interim state is "agents waiting, correctly."

## What the zeros are NOT

- Not a silent failure — every episode's state, wait and next-eligible time
  is visible on its timeline page.
- Not missing data — denominators (5 T / 4 C halted so far) are published
  with every rate.

## Interim status per episode

| Episode | Cohort | State | Waiting on |
|---|---|---|---|
| ep_0b4de8d7d4… | TREATMENT | SENT | customer payment (link live, unpaid) |
| ep_15f2809732… | TREATMENT | NEW | cooling window |
| ep_8b68479381… | TREATMENT | NEW | cooling window |
| ep_d51e757f57… | TREATMENT | NEW | cooling window |
| ep_48e32a4e3b… | TREATMENT | NEW | cooling window |

CONTROL subscriptions receive Razorpay's default retries only; their skips
are visible in the ledger.

---

*This file is regenerated from the ledger before submission. Between now and
the freeze, numbers only move in one direction: outward, with their
denominators.*
