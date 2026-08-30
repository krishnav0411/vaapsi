# WHAT_BROKE.md

Ten failures from the build, each with the root cause, how it was caught,
the fix, and the lesson. Published rather than hidden: a bounded agent's
failure story is part of its safety evidence.

---

### 1. Malformed-but-signed webhook body crashed the receiver (500)

- **What broke:** a POST with a valid HMAC signature over a shape-adversarial
  body (`"payload": "not-a-dict"`, truncated JSON, string entities) reached
  the receiver and crashed with `AttributeError` → HTTP 500. Found by the
  adversarial gauntlet, attack A05, on its first run.
- **Root cause:** the receiver trusted that a *signed* body was also a
  *well-shaped* body. Signature validity proves who sent it, not what they
  sent.
- **How it was caught:** the gauntlet's contract says a shape-adversarial but
  signature-valid body must be rejected 400-class, never 500. The scorecard
  recorded the defect honestly (`results/gauntlet_scorecard.json`, run 1:
  15/16) instead of patching mid-run.
- **Fix:** the receiver now validates the payload container and entity shape
  after signature verification and rejects 400-class; the halt consumer also
  normalizes any non-dict entity as defense in depth. A05 re-run: 16/16.
- **Lesson:** authenticate first, validate shape second, act third. A
  signature is an identity proof, not a schema.

### 2. Razorpay's webhook dispatcher went silent for hours

- **What broke:** six test-mode halts were induced through the dashboard API;
  Razorpay accepted every one; zero webhook events arrived for ~8 hours. The
  dashboard's own attempts page showed zero attempts.
- **Root cause:** Razorpay-side dispatcher stall, not our code. Diffing the
  Razorpay API (subscriptions genuinely HALTED) against the local event table
  (no halts received) isolated the gap to the middle of the pipe.
- **How it was caught:** a ground-truth sweep that queried Razorpay's API
  directly instead of trusting the local DB.
- **Fix:** deleted and recreated the webhook; events flowed within a minute.
  A tunnel watchdog now guards the local side (health-poll, relaunch,
  auto-repoint) so the next stall is at least provably not ours.
- **Lesson:** when an integration misbehaves, diff both endpoints against
  ground truth before debugging the middle. Also: 6 of the "lost" halts
  flushed from Razorpay's retry queue a day later — receiver idempotency
  handled them without double-processing.

### 2b. The dispatcher went silent again — and this time it cost a recovery window

- **What broke:** the day after failure 2, four ₹499 payment links were paid
  in checkout (captures confirmed on Razorpay's API within minutes). The
  `payment_link.paid` events never arrived — 2+ hours of silence, while the
  webhook's own health checks kept hitting the receiver every minute.
- **Root cause:** the dashboard's per-event selection had silently dropped to
  zero active events while the configuration API still reported all of them
  subscribed. The dispatcher had nothing enabled to send; health checks kept
  flowing because they are not event-gated. A second desync of the same
  family as failure 2, on a webhook that was mutated (re-pointed) several
  times across tunnel rotations.
- **How it was caught:** the same ground-truth discipline as failure 2, plus
  a control experiment — a signed self-test posted through the tunnel was
  accepted and stored in seconds, proving the receiver path live, and the
  server log showed the provider's health checks arriving but zero event
  POSTs. A ₹1 probe payment made *after* re-enabling the events landed its
  webhook in ~1 second, which pinned the fault to the event-selection layer
  and not the pipe.
- **Fix:** re-enabled the events in the dashboard; a fresh event flowed
  immediately. The four captures from the outage window could not be resent
  (their dispatch window had passed, and the API refuses webhook deletion on
  a live registration, so recreate-after-delete was unavailable). Instead of
  bending the safety envelope, the episodes re-drive through the ordinary
  policy path: the 48-hour outreach interval is honored, attempt 2 fires when
  the window lifts, and the ledger records attempt 1, the outage, and
  attempt 2 in sequence.
- **Lesson:** twice now the integration's silent failure mode has been
  Razorpay-side, and both times the ground-truth diff (their API vs. our
  event table) found it in minutes. The compensating controls when a
  provider loses events: read the provider's API for state, keep attempt
  counters so a lawful retry remains possible, and never re-attribute money
  from a reconstruction — only from a matched, real event.

### 2c. The guardian itself died, and took the webhook with it

- **What broke:** the tunnel watchdog — the process that keeps the tunnel
  alive and re-points the webhook URL after every rotation — crashed
  overnight on a DNS resolution error inside its own polling loop. The
  tunnel process it was supposed to supervise survived as an orphan with a
  fresh URL nobody recorded, while the webhook registration kept pointing
  at the dead address. Net effect: hours of silently undeliverable
  webhooks, and the same "config looks fine but nothing arrives" desync as
  failures 2 and 2b, caused this time by our own resilience layer.
- **Root cause:** an uncaught exception (transient `getaddrinfo` failure)
  escaped the watchdog's main loop. A guardian without its own guard rail.
- **How it was caught:** the process list showed cloudflared running with no
  watchdog parent, and the watchdog log ended mid-stack-trace instead of at
  a poll cycle.
- **Fix:** the entire poll loop is now exception-guarded — any probe error
  is logged as "poll error (suppressed, retrying)" and the loop continues;
  plus the standard restart procedure (kill orphan tunnel, relaunch
  watchdog, which re-points the webhook and runs a signed self-test).
- **Lesson:** every layer you add to prevent failure is itself a new
  failure surface. The watchdog now treats its own errors the way the
  receiver treats malformed events: catch, log, survive.

### 3. First real dispatch died on Razorpay 400: reference_id over 40 chars

- **What broke:** the first genuine outreach dispatch was rejected —
  `reference_id` capped at 40 characters, ours was 43.
- **Root cause:** an id format assumption never checked against the API
  contract.
- **How it was caught:** the full error, verbatim, landed in the ledger row
  and rendered on the episode timeline — the failure was designed to be
  visible, so it was.
- **Fix:** id truncated to fit; the stale payload was surgically repaired and
  re-drained from the dead-letter queue; a real payment link came back.
- **Lesson:** every external API constraint belongs in a test fixture. The
  episode timeline now tells this story to judges, which is the point of an
  audit ledger.

### 4. `cp1252` vs the rupee sign on Windows

- **What broke:** printing/report code writing `₹` died with a codec error on
  the default Windows code page.
- **Root cause:** Windows text mode defaults to cp1252; the code assumed
  UTF-8 everywhere.
- **How it was caught:** first live report render.
- **Fix:** explicit UTF-8 on every file write and console path; rupee amounts
  stored as integer paise end-to-end so the symbol only exists at the very
  edge.
- **Lesson:** store money as integers, render currency at the edge, never
  trust the platform's default encoding.

### 5. Patch-fusion error: two concurrent edits merged into wrong code

- **What broke:** two overlapping fixes applied in sequence fused into a
  hybrid that passed neither's intent — caught immediately by the test suite.
- **Root cause:** applying a second patch against stale in-memory context
  after the first had shifted the surrounding lines.
- **How it was caught:** suite went red within one run.
- **Fix:** re-read the file from disk before every targeted edit; sequential
  patches now always operate on the current on-disk state, never memory.
- **Lesson:** optimistic editing is fine for prose, never for code paths
  under test.

### 6. Long build step looked alive after it had already exited

- **What broke:** a long build step completed but the completion signal never
  fired; the run looked alive while actually finished.
- **Root cause:** the build script's completion was detected by grepping its
  streaming output for a phrase; the process exited between polls and the
  phrase never appeared, so a finished run read as still-running.
- **How it was caught:** the after-every-step gate (git status + test count)
  noticed nothing had landed.
- **Fix:** every long step now runs to a real exit and the step ends with a
  mandatory gate check — git status plus the test count — regardless of exit
  code.
- **Lesson:** exit codes lie by omission. Verify artifacts, not signals.

### 7. Vite dev server bound to IPv6, health check probed IPv4

- **What broke:** the frontend dev proxy came up "dead" — health checks got
  connection refused while the page worked in a real browser.
- **Root cause:** Node's dev server bound `::1`; the probe used `127.0.0.1`.
- **How it was caught:** the page loading fine while curl failed.
- **Fix:** explicit host binding; probes and binds now agree on the address
  family.
- **Lesson:** "localhost" is two addresses. Pin the family on both ends.

### 8. ngrok blocked as PUA on the host network

- **What broke:** the tunnel binary was flagged as potentially-unwanted
  software and silently blocked, killing the planned public URL path.
- **Root cause:** AV reputation, not anything we did.
- **How it was caught:** tunnel up in logs, unreachable from outside.
- **Fix:** switched the tunnel strategy to Cloudflare quick tunnels with a
  watchdog that relaunches and re-points the webhook automatically; rotation
  is handled, not feared.
- **Lesson:** infrastructure dependencies need an observed-working fallback,
  chosen once, calmly, before the deadline makes it urgent.

### 9. SQLite column-name drift in a probe (probe bug, not product bug)

- **What broke:** a health-check probe queried `event_type`; the table's
  column is `event`. The probe reported the table missing.
- **Root cause:** two schemas existed across the codebase's history; the
  probe hand-wrote SQL against the wrong one.
- **How it was caught:** running the product's own CLI verifier, which
  disagreed with the probe — the discrepancy isolated the bug to the probe.
- **Fix:** probes now reuse the app's data-access functions instead of
  hand-rolled SQL; schema literals live in one place (`app/db.py`).
- **Lesson:** when a check and the product disagree, suspect the check
  first. It's cheaper and usually right.

### 10. CI green locally, red on GitHub: Node version drift

- **What broke:** the first CI run failed the frontend job with
  `webidl.util.markAsUncloneable is not a function` inside vitest workers —
  after passing locally, twice.
- **Root cause:** ten packages in the lockfile (jsdom 30, undici 8, vitest 4)
  require Node ≥22.13; local dev ran Node 24, CI pinned Node 20.
- **How it was caught:** GitHub Actions run #1. The engines field audit of
  `package-lock.json` pinned it in minutes.
- **Fix:** CI moved to Node 24; run #3 green across all three jobs.
- **Lesson:** "works on my machine" now includes your runtime version.
  Audit `engines` when adding dev dependencies, and let CI be the arbiter,
  not the laptop.

---

The pattern across all eleven: nothing failed silently that mattered. Either
the ledger, the test suite, the gauntlet, or a ground-truth diff named the
failure within one step. That is the property a money-adjacent agent needs
most, and it is the reason this file is public.
