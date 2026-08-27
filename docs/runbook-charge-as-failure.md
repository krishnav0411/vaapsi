# Runbook — inducing `pending → halted` (Dashboard, test mode)

Each subscription needs **1 successful charge** (to activate) and then
**4 consecutive failed charges** (4th failure → `halted`). This is
Razorpay's documented test-mode simulation; nothing here is mocked by us.

## Per subscription (~60 seconds)

1. **Subscriptions** (left sidebar) → click the subscription from the
   cohort list (`sub_…`, check `data/cohort_manifest.csv`).
2. **First charge (activate):** click **"Charge this now"** → the test
   checkout opens → pay with any test method (e.g. UPI `success@razorpay`
   or test card `4111 1111 1111 1111`, any future expiry, any CVV).
   → subscription becomes `authenticated` → `active`.
3. **Failure ×4:** click **"Charge this now"** again → this time choose
   **"Charge as Failure"** → repeat 4 times total.
   - After failures 1–3 the subscription flaps to `pending` and back.
   - **After the 4th failure → status `halted`.**
   - Each failure also fires `payment.failed` (with `error_code`) — that's
     our diagnosis feature feed.

## What Vaapsi does automatically

Every event fires into the webhook (`subscription.authenticated`,
`subscription.charged`, `subscription.pending`, `subscription.halted`,
`payment.failed`) → HMAC-verified, archived raw, deduped. Check with:

```bash
curl -s http://127.0.0.1:8000/health
sqlite3 data/vaapsi.sqlite3 "SELECT event, subscription_id, event_ts_utc FROM webhook_events ORDER BY id DESC LIMIT 10;"
```

(No sqlite3 CLI? The Python one-liner in `docs/` works too — or just ask.)

## Batch rhythm (D6 evaluation run)

Grinding 60 subs × 5 clicks is the D6 overnight run; do 2–3 subs first to
confirm the loop, then batch the rest by cohort. Alternative for the batch
day: the agent can drive the Dashboard clicks via computer-use — decide
when we get there.
