"""D4 failure-engineering drills .

Each module is one repeatable drill against the REAL seams: replay.py
proves ingest idempotency under a delivery storm (via the pure
process_webhook conn/headers/body seam), faults.py injects Razorpay 5xx
outages at the ActionClient Protocol to exercise the retry → DLQ → drain
path, and llm_outage.py drives real recovery cycles through the real
OpenAI-compatible adapter aimed at a dead endpoint to prove the
DEGRADED fallback stamps and hold the policy line. Zero real network
anywhere — every transport is a stub or an injected fault; drills are
deterministic and offline by construction.
"""
