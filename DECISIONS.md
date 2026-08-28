# DECISIONS.md

The choices that shaped this build, with the reasoning and the rejected
alternatives. Reverse-chronological within themes; each entry says what we'd
do again and what we'd watch.

## Track choice by data, not enthusiasm

**Decision:** Track 03 (AI Revenue Recovery), chosen after scanning ~60
buildathon repos and grading 532 candidates against a frozen rubric.
**Why:** the build surface is small and fully documentable — webhooks in,
bounded actions out, one ledger, one dashboard, test mode end to end — which
lets safety engineering, not infrastructure, be the differentiator.
**Rejected:** Track 02 (payment-ops dashboards — bigger surface, thinner
safety story). Would repeat as-is.

## Rules decide; the LLM only picks wording

**Decision:** the policy engine is plain Python. The LLM returns exactly two
allowlisted fields; on any failure it degrades to templates in-place.
**Why:** an agent that can create payment links needs a decision layer that
is testable line-by-line. "The model suggested it" is not an audit answer.
**Rejected:** LLM-as-decider with guardrails (unguardable in principle),
LLM-with-schema (still unbounded output surface).
**Evidence:** offline evaluation — full-LLM 0.50 vs rules-only 0.4651
recovery rate with identical routing; zero false outreach in every arm. The
model adds tone, not judgment. Would repeat as-is.

## SQLite, not Postgres

**Decision:** one file, WAL mode, hash-chained ledger table inside it.
**Why:** the audit chain's verifiability doesn't need a server; the write
load of one merchant's recovery events fits a file comfortably; and a
judge running `make verify-chain` against a file needs no infrastructure.
**Rejected:** Neon/Postgres for the hosted demo (Arc-style). Cost: ephemerality
on free hosts — answered by seed-on-boot building a sanitized, chain-valid
store. Would repeat.

## Hash-chained ledger from day zero

**Decision:** every state change appends one row; each row's hash covers the
previous row's hash; verification is a CLI command and a dashboard button.
**Why:** "trust the log" is cheap to say and expensive to prove. The chain
makes tampering detectable in one command, and the in-UI tamper demo turns
that property into something a judge can watch in six seconds.
**Cost:** every writer must route through `ledger.append` — enforced by tests.
Would repeat.

## Human gate above ₹500 — and the model can't walk past it

**Decision:** GATED is a state, not a flag. Approvals enqueue atomically with
their ledger row; only a human decision moves the state.
**Why:** outreach above a threshold is exactly where "the agent seemed sure"
must not be the reason it fired.
**Rejected:** configurable threshold from the start (shipped per-merchant
policy later, but the *gate itself* stays non-bypassable). Would repeat.

## Adversarial gauntlet with a defect protocol

**Decision:** 16 black-box attacks, two global invariants after every attack,
and a rule that defects are *recorded*, never fixed mid-run. A05 (receiver
500 on malformed-but-signed) was found this way, recorded, then fixed and
re-run to 16/16.
**Why:** fixing mid-run destroys the evidence that the harness finds real
defects. The 15/16 first run is the proof the gauntlet isn't theater.
Would repeat.

## Fail-closed public demo

**Decision:** demo mode refuses boot with any credential present, never
mounts the webhook receiver, 404s every write route, and seeds a sanitized
chain-valid store on boot.
**Why:** a public URL that can write to a money-adjacent system is the whole
threat model in one sentence. Read-only-or-nothing.
**Rejected:** sandboxed write demo (too much surface for a hackathon demo).
Would repeat.

## README numbers enforced by CI

**Decision:** `scripts/verify_numbers.py` fails CI if the README's evaluation
block and `results/evaluation.json` ever disagree; the block lives between
`eval:start/end` markers.
**Why:** every repo claims numbers; almost none make the claim falsifiable.
A drift guard converts the README from prose to contract.
Would repeat.

## Honest zeros on the live experiment

**Decision:** the dashboard renders ₹0 and 0% recovery rates with their
denominators ("0 of 5 within 7d") rather than hiding sparse data, and the
CONTROL cohort shows "—" (no data) where a rate would mislead.
**Why:** a recovery agent that fakes its own metrics would invalidate its
safety story. The zeros are the point until the money arrives.
Would repeat.

## Node/React rebuild of the dashboard mid-build

**Decision:** D7 replaced the Jinja dashboard with Vite + React + Tailwind +
shadcn, keeping the FastAPI JSON API untouched; Jinja routes still work as a
fallback.
**Why:** interaction depth (palette, explorer, drills console, approvals
inbox with keyboard flow) was not reachable in server-rendered templates at
the quality bar the field set.
**Cost:** a rebuild window mid-experiment. Mitigated by keeping the API
frozen and shipping dist in-repo. Would repeat, earlier.
