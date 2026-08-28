# THREAT-MODEL.md

What Vaapsi protects, what it refuses to be, and why each dangerous capability
has no code path. Written as a non-capabilities table first: the strongest
security property of this system is everything it *cannot* do.

## Assets

1. **Customer trust** — a wrong message to a wrong customer costs more than
   the recovery is worth.
2. **The merchant's money relationship** — Vaapsi collects debts owed to the
   merchant; it never holds, moves, or initiates money.
3. **The audit ledger** — if the evidence can be edited, every other property
   is theater.
4. **Razorpay test-mode credentials** — the only live secrets in the system.

## Non-capabilities (each danger has no code path)

| Danger | Why it cannot happen |
|---|---|
| Agent invents a charge | No payment-*creation* from any wallet exists. The only money API call is a payment *link* for an amount the merchant is already owed, clamped to the failed invoice. |
| Agent messages outside the action layer | No email/SMS code exists in the repo. "Outreach" is a Razorpay payment link + its ledger record. |
| LLM escalates its own authority | The model's total output surface is two allowlisted fields (`channel`, `message_variant`). Anything else is discarded; the run degrades to templates. There is no tool-call path from the model to the world. |
| Prompt injection via customer name / error text | Injected text lives in raw event rows and diagnosis strings; it is never executed, evaluated, or treated as instructions. Gauntlet A06/A07 prove instruction-shaped payloads fall through to UNKNOWN-safe handling. (`tests/test_gauntlet.py`) |
| Outreach after the customer already charged/cancelled | Stop events void the episode before any queued action; void rows are ledger-visible. M5 (false outreach) is measured, not assumed — currently 0, provable from ledger rows. |
| Forged webhook triggers actions | Every event is HMAC-verified against the configured secret before storage; unsigned/forged posts are rejected 401 (gauntlet A03/A04). |
| Replayed webhook double-fires | Per-event idempotency key; exact and reshuffled replay storms store once, process once (gauntlet A01/A02). |
| Ledger silently edited | Hash chain over all rows, each covering the previous; `make verify-chain` recomputes everything and fails loudly. In-UI tamper demo runs on a sandbox copy and names the broken row. |
| Human gate bypassed by the model | The gate is a state (GATED), not a suggestion. The model cannot move state; only a human decision endpoint can, and it appends its own ledger row (gauntlet A11). |
| Kill switch bypassed | The switch is checked engine-side before any action; the UI control is a convenience, not the enforcement point. One-way by design. |
| Demo deployment leaks writes or credentials | `VAAPSI_PUBLIC_DEMO=1` refuses boot with any provider credential present, never mounts the webhook receiver, 404s every write route. Container-verified (`tests/test_demo_mode.py`). |
| Secrets leak through the repo | Credentials live only in `.env` (gitignored) and the operator's vault; `.env.example` carries names only; probes print booleans/counts, never values; git history scrubbed and force-pushed clean. |

## Trust boundaries

```
Razorpay (test mode)  ──HMAC──▶  receiver  ──▶  SQLite (local file)
      ▲                                │
      └──── payment-link API ◀── action layer ◀─▶ rules engine ◀─▶ LLM (allowlist only)
                                       │
                                  human gate (> ₹500)
```

- The **model** is the least-trusted component by design: it sees diagnosis
  text, returns two fields, and is otherwise sandboxed by construction.
- The **webhook receiver** is the only internet-facing surface; it stores raw
  events read-only-forever and hands off through idempotent consumers.
- The **dashboard** in public demo mode is read-only at the router level, not
  by UI convention.

## Residual risks, stated honestly

- **Test-mode scope:** no real customer data has ever entered the system; all
  subscriptions, customers and events are synthetic test fixtures. The
  mechanism is proven; the market is not.
- **Single operator:** the human gate is currently one human. The gate's
  integrity comes from the ledger, not from operator redundancy.
- **Quick-tunnel rotation:** the public tunnel URL rotates on watchdog
  restart; the webhook re-point is automated, but a stalled dispatcher window
  (however brief) is possible by construction. "Recreate beats re-point"
  remains the documented recovery.
- **SQLite, not Postgres:** deliberate, documented in DECISIONS.md. The
  write-load of one merchant's recovery events fits a file; the audit chain
  does not require a database server to be verifiable.
