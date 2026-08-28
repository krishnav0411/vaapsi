# Vaapsi

A recovery agent for failed Razorpay subscription charges. Built for the
Razorpay AI Buildathon 2026 (Track 03), test-mode APIs only.

When a subscription charge fails a few times, Razorpay halts the subscription.
The money is usually still there; the customer just had an expired card or a
dry UPI mandate. Nobody chases it. Someone has to notice, then email the
customer, then hope. Vaapsi does that chase the boring way: a rules engine
decides whether and when to reach out, an LLM only picks the wording and the
channel, and every step lands in an append-only ledger.

## Why the rules engine decides, not the model

An agent that can message customers and create payment links, with nothing
holding it back, is a liability. So the LLM here never decides anything that
matters. It reads a diagnosis and returns two fields: channel and
message_variant. Both come from an allowlist in code. If the model is down,
returns garbage, or tries something outside the allowlist, the run continues
with fixed templates instead. The recovery doesn't stop because the model did.

The rules that hold before any outreach happens:

- max 3 attempts per episode
- 6 hours cooling after a halt before the first action
- at most one outreach per 48 hours
- no actions between 21:00 and 09:00 IST
- amounts above ₹500 wait for a human approval
- any charge or cancellation voids the episode on the spot

## What it looks like

```text
webhook (HMAC verified, idempotent)
   → episode state machine
      → policy engine (plain Python, no LLM)
         → LLM picks {channel, message_variant} from an allowlist
            → payment link via Razorpay API
               → verify consumer watches for the payment
```

Every state change writes one row to an append-only SQLite ledger where each
row's hash covers the previous row's hash. `make verify-chain` replays the
whole thing and fails loudly if anything was touched.

## Running it

```bash
cp .env.example .env      # add your Razorpay test keys
make install
make run                  # http://localhost:8000/health to check it's alive
```

The dashboard is at http://localhost:8000/app. A built copy of the React
frontend ships in `frontend/dist/`, so you don't need node to see it. There's
also an older server-rendered version at `/dashboard`.

```bash
make test                 # 192 tests
make verify-chain         # replays the audit ledger
```

## Numbers so far

The experiment was written down before any data was collected (see
`EXPERIMENT.md`): 60 test subscriptions, 30 get the agent (treatment), 30
don't (control), assigned alternately at creation time.

| Metric | Value | Note |
|---|---|---|
| Cohorts | 30 / 30 | assigned alternately at creation |
| Halt events | 13 | 9 led to recovery episodes |
| Episodes opened | 5 | 1 outreach sent, 4 waiting on their window |
| Outreach to already-charged subs | 0 | checked against stop events in the ledger |
| Recovered | ₹0 | one link created, not paid yet |

Small cohort, one plan, seven-day window, test mode throughout. I'll freeze
the full table with honest denominators in `RESULTS.md` before submitting.

## Offline pipeline evaluation

A committed, reproducible offline evaluation of the decision pipeline
(`make eval`): 200 synthetic halted-subscription cases across 16 failure
families, four arms over the same corpus, a documented synthetic outcome
model — clearly not real money, no real outreach, the live data store is
never opened. Every case runs in a throwaway temp-dir SQLite store and each
arm's ledgers are hash-chain-verified afterwards. The numbers below are the
drift-guard contract: `python scripts/verify_numbers.py` fails CI if this
block and `results/evaluation.json` ever disagree.

<!-- eval:start -->
seed 1403, 200 cases, 16 families, synthetic outcome model - not real money

| arm | attempts | recovered | recovery_rate | 95% Wilson CI | oracle_gap |
|---|---|---|---|---|---|
| full_llm | 86 | 43 | 0.5000 | [0.3966, 0.6034] | -0.1650 |
| no_agent | 200 | 32 | 0.1600 | [0.1157, 0.2171] | 0.1750 |
| random_allowlist | 86 | 42 | 0.4884 | [0.3855, 0.5922] | -0.1534 |
| rules_only | 86 | 40 | 0.4651 | [0.3635, 0.5698] | -0.1301 |

Invariants: zero false outreach in every pipeline arm, every arm's ledger
chain verifies, seed pinned. The three pipeline arms route identically
(the LLM flavors, the rules decide); the recovery draw is per (case, arm),
so the residual spread between them is sampling noise, not signal.
<!-- eval:end -->

## What went wrong

- Razorpay's webhook dispatcher went quiet for hours mid-experiment. The API
  accepted every halt from my side, but no webhook ever arrived. Diffing
  Razorpay's API against my table showed the gap; deleting and recreating the
  webhook in the dashboard fixed it, and events flowed again within a minute.
- The first outreach dispatch died on a Razorpay 400: reference_id is capped
  at 40 characters and mine was 43. The error is still in the ledger and
  rendered on the episode timeline. I truncated the id, repaired the payload,
  and drained it from the dead-letter queue.
- I killed the LLM mid-episode on purpose. The run switched to templates in
  about four seconds and still completed the action. The ledger row says
  DEGRADED.

## Layout

```text
app/
  ingest/      webhook receiver (HMAC, idempotency) + halt/verify consumers
  core/        episode state machine
  policy/      the rules engine (caps, cooling, quiet hours, gates)
  llm/         allowlisted client + degraded fallback
  actions/     payment-link dispatch, dead-letter queue
  gates/       human approval
  audit/       hash-chained ledger + verifier
  dashboard/   JSON API, React app at /app, older Jinja pages at /dashboard
tests/         192 tests
chaos/         failure drills (replay storms, fake 5xx, dead LLM)
EXPERIMENT.md  the cohort design, written before any data
```

## Scope

Test mode only, no real money. Outreach is logged rather than delivered,
since test mode doesn't send real SMS or email. The agent only collects what
a merchant is already owed and it does nothing else.

## CI

![](https://github.com/krishnav0411/vaapsi/actions/workflows/ci.yml/badge.svg)

Every push and pull request runs three jobs: the Python suite (ruff +
pytest), the frontend (vitest + a production build), and the drift-guard
that checks the published evaluation numbers against the committed JSON.
No secrets are used — everything runs offline against seeded stores.
