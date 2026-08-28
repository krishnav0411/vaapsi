# Build log

Short notes per day while building. No plan documents, just what happened.

## Day 0 — the webhook problem

Razorpay signs every webhook with an HMAC. If you verify it wrong, you get
either false accepts or false rejects. I wrote the receiver to read the raw
request body before parsing it, since the signature covers the raw bytes, not
the parsed object. Ingest is idempotent: Razorpay retries deliveries, so the
same event arriving twice must not create two rows. That took a few
iterations because events span multiple entity types and I initially keyed
idempotency too narrowly.

By midnight the webhook was live end to end: registered 12 event types,
posted a signed self-test through a public tunnel, watched it verify and
land in the database. One snag: I first used ngrok, which Windows Defender
blocked as a PUA. cloudflared worked fine and stayed.

## Day 1 — the experiment, and the first mistake

Before creating any data, I wrote down the experiment design: 60 test
subscriptions on one ₹499/month plan, half treatment and half control,
assigned alternately at creation so nothing can be re-sorted later. Then I
created them.

Then I noticed the first mistake. I had created subscriptions without
customer objects, and Razorpay's "charge as failure" on a customer-less
subscription runs an invoice-notification flow instead of the subscription
charge simulation. Six clicks produced standalone payments, zero subscription
charge cycles, zero webhooks. None of it counted as experiment data, so
nothing had to be thrown away: 60 fresh subscriptions with customers
attached, same plan, same interleaving. The v1 subscriptions were cancelled
and archived. The addendum about this is in EXPERIMENT.md, written before any
halt existed.

Second overnight problem: my webhook URL had no path suffix, and Razorpay
POSTed to the bare tunnel domain all night. My server logged a wall of 404s
from AWS Mumbai retry traffic. Added an app-level fallback route that
verifies HMAC on those deliveries too.

## Day 2 — ledger, state machine, rules

The audit ledger came first: append-only SQLite, each row's hash covering the
previous row's hash plus its content. Then the episode state machine (new →
diagnosed → scored → gated → sent → verified, with a voided state for stop
events), where every transition and its ledger write share one transaction.
The policy engine is the part I care most about: six ordered checks, plain
Python, no network, no model. Caps read episode columns rather than ledger
rows so a replayed ledger can't inflate attempt counts.

The offline demo at the end of the day dispatched 6 treatment episodes,
blocked 4 control ones, wrote 16 ledger rows, and verified the chain.

## Day 3 — where the LLM is allowed

The LLM gets a diagnosis and returns channel plus message_variant, both from
allowlists enforced in code. Anything else — a different action, a smuggled
amount field, a non-dict — gets rejected by schema. I ran three injection
probes against it and all three died in validation. Then the human gate for
amounts above ₹500, and the orchestrator that ties diagnosis → scoring →
decision → policy → action together, switching to fixed templates the moment
the model errors.

## Day 4 — breaking it on purpose

Three drills, all repeatable commands now: replay 30 webhooks where 25 are
identical and watch idempotency collapse them to one row; a fake client that
returns 5xx until retries exhaust into the dead-letter queue, then drain it;
kill the LLM and confirm the run downgrades to templates instead of dying.
The first quiet-hours block I saw was on my own chaos probe at 06:50, which
was oddly satisfying: the compliance layer refusing to misbehave even for a
test.

## Day 5 — the dashboard

Metrics first (M1 recovery rate, M2 rupees, M3 time-to-recover, M4 outreach
efficiency, M5 false outreach), then pages, then the kill switch with a
typed-KILL confirmation. Styling followed Stripe's dashboard tokens initially
and got redone in Razorpay's Blade tokens a day later, after reading Blade's
source. Live kill-cycle tested on an isolated port.

## Day 6 — the stall

Razorpay's webhook dispatcher stopped delivering. My side was clean: tunnel
up, server healthy, secret correct, API showed the halts, and the dashboard
showed zero attempts. After two hours of diffing I refund-probed with a
throwaway event, confirmed nothing arrived, and concluded the dispatcher was
wedged on my webhook's URL (I had mutated it four times that day). Deleting
and recreating the webhook in the dashboard un-wedged it; a refund landed 60
seconds later.

The forensic replay backfilled the events the stall ate: 5 halted
subscriptions, 11 events each, pushed through the real receiver. Idempotency
converged replay against live with zero duplicates, which is the exact
scenario it was built for.

Also this day: the reference_id 40-character limit killed the first real
dispatch (400 error, three retries, dead-letter queue), I truncated the id
and repaired the payload, and the drained link became the first real payment
link the system created. Wired the halt→episode consumer and the
payment_link.paid → verified consumer.

## Day 7 — the React rebuild

Rewrote the dashboard as a React app in Razorpay's Blade design language,
vendored Razorpay's actual fonts from their open-source Blade package (8
woff2 files, no CDN), and put the whole thing behind the FastAPI server at
/app with SPA fallbacks. 12 regression tests guard the design tokens. The
server-rendered pages stay as a fallback.
