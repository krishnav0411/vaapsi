# SAFETY.md — what this agent may and may not do

Vaapsi is a bounded agent: it acts on money-adjacent state only inside rules
that live in code, reviewed here with the test that proves each bound. Every
row below names the rule, where it fires, what the agent does when the rule
trips, and the test (or gauntlet attack) that pins it.

The one-line threat stance: **the rules engine decides; the LLM picks words;
the ledger remembers; the human approves above ₹500.** Nothing else ships.

## Hard bounds

| # | Bound | Value | Where it fires | On violation | Proven by |
|---|---|---|---|---|---|
| 1 | Max outreach attempts per episode | 3 | `app/policy/engine.py` — per-episode attempt counter read before every action | Action refused; episode stays open for human review | `tests/test_policy.py`; gauntlet A13 `cap_overflow` (3rd attempt refused) |
| 2 | Cooling after halt | 6 h | engine, before first action per episode | Action refused until the window elapses | `tests/test_policy.py`; live ledger rows show the wait |
| 3 | Minimum interval between outreaches | 48 h | engine, per subscription | Second outreach inside the window refused | `tests/test_policy.py` |
| 4 | Quiet hours | 21:00–09:00 IST | engine, wall-clock check against IST | Action refused; retried after 09:00 | `tests/test_policy.py`; gauntlet A12 `quiet_hours_probe` (21:00 exactly closes the window) |
| 5 | Human gate | > ₹500 | `app/gates/human_gate.py` — episode routed to GATED state, approval row written atomically with the ledger row | Nothing sends. The queue entry, the state change and the audit row commit or roll back together | `tests/test_human_gate.py`; gauntlet A11 `human_gate_bypass_attempt` (the model's recommendation never outranks the gate) |
| 6 | Stop on charge / cancellation | immediate | halt/verify consumers check stop events before acting | Episode voided on the spot; void recorded in the ledger | `tests/test_verify_consumer.py`; M5 false-outreach metric = 0 |
| 7 | Kill switch | one-way | `app/main.py` kill endpoint; engine refuses all outbound while set | All actions denied engine-side, not just UI-side | `tests/test_day0.py`; live-fired during the build |
| 8 | LLM output allowlist | 2 fields | `app/llm/base.py` — model returns `{channel, message_variant}`; both validated against code-level allowlists | Anything outside the allowlist is discarded; run continues on fixed templates (DEGRADED) | `tests/test_llm_client.py`; gauntlet A06/A07 (prompt injection in customer name and error text never left the raw event store) |
| 9 | Stale state fencing | two-transaction pattern | `app/policy/fencing.py` — re-fetch subscription state immediately before dispatch; content hash compared | Fingerprint mismatch → episode marked `DISCARDED_STALE`, no action | `tests/test_fencing.py`; gauntlet A14 `stale_fingerprint_race` |
| 10 | Idempotent webhook intake | per-event idempotency key | `app/ingest/receiver.py` | Duplicate delivery stored once, processed once | `tests/test_chaos_replay.py`; gauntlet A01/A02 (exact and reshuffled replay storms) |
| 11 | Ledger immutability | hash chain | `app/audit/ledger.py` — each row's hash covers the previous row's hash | Any edit breaks verification; `make verify-chain` fails loudly | `tests/test_audit_ledger.py`; gauntlet A08 `amount_tamper` + A09 `ledger_surgery` (tampered copies fail at exactly the edited row) |
| 12 | Dispatch failures never vanish | dead-letter queue | `app/actions/execute.py` — 3 attempts with backoff, then DLQ row + ledger record | Failure is visible and replayable, never silent | `tests/test_actions.py`; the real reference_id-400 story rendered on the episode timeline |

## What the agent cannot do, by construction

- **It cannot contact a customer outside the action layer.** There is no email
  or SMS code path at all in test mode; "outreach" is a payment link created
  through Razorpay's API, logged in the ledger.
- **It cannot spend money.** It creates collection links for amounts a
  merchant is already owed. No payment is initiated from any wallet; nothing
  is charged.
- **It cannot act after a customer charges, cancels, or completes.** Stop
  events void the episode before any queued action can fire (bound 6).
- **It cannot let the model negotiate.** The LLM's entire influence is two
  allowlisted fields that shape wording (bound 8).
- **It cannot hide a mistake.** Every refusal, failure, void and drain is a
  ledger row with the reason attached.
- **In public demo mode it cannot write at all.** `VAAPSI_PUBLIC_DEMO=1`
  refuses boot with any provider credential present, never mounts the webhook
  receiver, and answers 404 on every write route
  (`tests/test_demo_mode.py`, container-verified).

## Cohort discipline

The experiment assigns 30/30 treatment/control interleaved at creation, and
the action layer skips CONTROL subscriptions with the skip visible in the
ledger. Gauntlet A16 `cohort_leakage` attacks this twice per run; the holdout
held both times (`results/gauntlet_scorecard.json`).

## Where the bounds were found the hard way

Several of these rows exist because something broke first — the dispatch
error that landed in the dead-letter queue, the webhook dispatcher stall, the
receiver crash on a malformed-but-signed body (fixed to reject 400-class and
added as gauntlet attack A05 permanently). `WHAT_BROKE.md` carries the full
list; the tests above are the regression proof that each lesson stuck.
