"""D4 Drill 3 — LLM outage → DEGRADED: formalize what the chaos probe proved.

A live probe showed the right behavior once, by
accident of timing: a real transport outage mid-cycle, every decision
falling back to rules-only, DEGRADED stamped in the ledger, policy still
refusing blocked outreach. This drill makes that story REPEATABLE: it
drives run_recovery_cycle for each subscription through the REAL
OpenAICompatibleClient aimed at a dead endpoint — the same
transport-failure path a live outage produces (httpx.ConnectTimeout /
HTTP 500 surfacing through the adapter's own retry-once logic as
LLMUnavailable) — with zero sockets: the transport is an injected
httpx.MockTransport whose handler raises the exact exceptions a dead
base_url would, so tests and the demo stay fully offline.

Per-episode independence: each subscription runs its OWN recovery cycle;
a dead endpoint is caught INSIDE that episode's decide step and degrades
only that episode — run_outage_drill asserts every episode it drives ends
with mode='DEGRADED' cycle rows (creation rows stay event-layer NORMAL by
design), tier-appropriate fallback choices on every SCORED row, the
outage evidenced in the ledger (request hash present, model output
absent — the model was consulted and produced nothing usable), policy
still enforced (a CONTROL cohort is blocked with zero outreach writes
even while the LLM is down), and zero rows lacking a mode stamp.
"""

import sqlite3
from collections.abc import Sequence
from typing import Any

import httpx

from app.audit.ledger import iter_rows
from app.core import episodes
from app.llm.base import LLMClient
from app.llm.openai_compat import OpenAICompatibleClient
from app.orchestrator import FALLBACK_CHOICES, run_recovery_cycle
from app.settings import get_settings

# The dead endpoint: TCP discard port — by convention nothing listens
# here, so a real client would be refused instantly. The drill never
# opens that socket (the transport is mocked), but the client's config
# records the same dead base_url a live outage would point at.
DEAD_BASE_URL = "http://127.0.0.1:9"

# Ledger label for outage-driven decisions: deterministic regardless of
# whatever model a local .env names, so the demo's evidence is stable.
OUTAGE_MODEL_LABEL = "openai-compat@dead-endpoint"

# The rows a recovery CYCLE writes (vs the event layer's creation row):
# these are the decision rows the outage must stamp DEGRADED.
CYCLE_OUTCOMES: tuple[str, ...] = (
    "EPISODE_DIAGNOSED",
    "EPISODE_SCORED",
    "EPISODE_SENT",
    "EPISODE_GATED",
)


def _dead_transport(hits: list[int]) -> httpx.MockTransport:
    """A transport that fails like a dead endpoint, alternating flavors.

    Odd hits raise httpx.ConnectTimeout (endpoint unreachable), even hits
    return HTTP 500 (endpoint up but burning) — the two shapes this drill
    injects. The real adapter retries a timeout/5xx exactly once, so
    every consult burns exactly 2 transport hits before LLMUnavailable.
    `hits` records the URL of every attempt, exposed for drills/tests to
    prove the wire was actually hit — and hit at the DEAD endpoint.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        if len(hits) % 2 == 1:
            raise httpx.ConnectTimeout(
                f"drill endpoint {DEAD_BASE_URL} unreachable", request=request
            )
        return httpx.Response(500, text="outage drill: endpoint on fire")

    return httpx.MockTransport(handler)


def dead_endpoint_client() -> OpenAICompatibleClient:
    """The REAL adapter, configured against a dead endpoint, zero network.

    Reuses app.llm.openai_compat.OpenAICompatibleClient unchanged — same
    constructor, same retry-once, same LLMUnavailable contract — with an
    explicit placeholder key (the endpoint is dead; the key is never
    honored and is not a secret) and a socket-free MockTransport. The
    instance carries `model_name` (deterministic ledger label) and
    `transport_hits` (how many times the dead wire was hit).
    """
    hits: list[int] = []
    client = OpenAICompatibleClient(
        api_key="outage-drill-placeholder-key",
        base_url=DEAD_BASE_URL,
        model="outage-drill",
        transport=_dead_transport(hits),
    )
    client.model_name = OUTAGE_MODEL_LABEL
    client.transport_hits = hits
    return client


def run_outage_drill(
    conn: sqlite3.Connection,
    subscription_ids: Sequence[str],
    outage_client: LLMClient,
) -> dict[str, Any]:
    """Drive one recovery cycle per subscription through a dead LLM.

    Runs run_recovery_cycle(conn, sub, outage_client) for every
    subscription independently (per-episode independence: one episode's
    LLM failure is caught and degraded inside its own cycle, never
    propagated), then asserts the drill contract on the caller's
    connection:

    1. every episode the cycle DECIDED (dispatched/gated/blocked) carries
       mode='DEGRADED';
    2. zero ledger rows for these subscriptions lack a mode stamp, and
       every cycle row (diagnosed/scored/sent/gated) is DEGRADED;
    3. every SCORED row chose the tier-appropriate rules-only fallback
       (FALLBACK_CHOICES[tier], None → human gate for tier 3), with the
       outage evidenced: llm_request_hash present, llm_output_raw absent;
    4. policy still enforced during the outage: CONTROL episodes are
       blocked at the cohort gate with attempt_count 0 and zero outreach
       rows — a dead model must not widen what Vaapsi may do.

    Returns the summaries and counts for the demo table/tests to quote.
    Raises AssertionError on any violated invariant (drill = proof).
    """
    summaries: dict[str, dict[str, Any]] = {}
    for subscription_id in subscription_ids:
        summaries[subscription_id] = run_recovery_cycle(
            conn, subscription_id, outage_client
        )

    driven = {
        sub_id: summary
        for sub_id, summary in summaries.items()
        if summary["episode_id"] is not None
    }

    # ── invariant 1: every decided episode is DEGRADED ─────────────────
    for sub_id, summary in driven.items():
        if summary["status"] in ("dispatched", "gated", "blocked"):
            assert summary["mode"] == "DEGRADED", (
                f"{sub_id}: episode decided in mode {summary['mode']!r} "
                f"while the LLM endpoint was dead"
            )

    wanted = frozenset(subscription_ids)
    rows = [r for r in iter_rows(conn) if r["subscription_id"] in wanted]
    cycle_rows = [r for r in rows if r["outcome"] in CYCLE_OUTCOMES]

    # ── invariant 2: zero rows lacking mode; cycle rows all DEGRADED ───
    for row in rows:
        assert row["mode"], (
            f"{row['subscription_id']}: {row['outcome']} row lacks a mode stamp"
        )
    for row in cycle_rows:
        assert row["mode"] == "DEGRADED", (
            f"{row['subscription_id']}: cycle row {row['outcome']} carries "
            f"mode {row['mode']!r} during a dead-endpoint outage"
        )

    # ── invariant 3: tier-appropriate fallback + outage evidence ───────
    for row in (r for r in cycle_rows if r["outcome"] == "EPISODE_SCORED"):
        tier = row["policy_eval"]["tier"]
        assert row["policy_eval"]["choice"] == FALLBACK_CHOICES.get(tier), (
            f"{row['subscription_id']}: tier {tier} fallback choice drifted "
            f"from FALLBACK_CHOICES: {row['policy_eval']['choice']!r}"
        )
        assert row["llm_request_hash"], (
            f"{row['subscription_id']}: no LLM request evidence — the outage "
            f"consult left nothing to audit"
        )
        assert row["llm_output_raw"] is None, (
            f"{row['subscription_id']}: dead endpoint must not produce model output"
        )

    # ── invariant 4: policy still enforced during the outage ───────────
    for sub_id, summary in driven.items():
        episode = episodes.get_episode(conn, summary["episode_id"])
        if episode["cohort"] != "CONTROL":
            continue
        assert summary["status"] == "blocked" and summary["reason"] == "cohort_gate", (
            f"{sub_id}: CONTROL reached {summary['status']!r} during the outage — "
            f"the cohort gate must hold without an LLM"
        )
        assert episode["state"] == "SCORED" and episode["attempt_count"] == 0, (
            f"{sub_id}: blocked CONTROL episode was mutated during the outage"
        )
        outreach = [
            r
            for r in rows
            if r["subscription_id"] == sub_id
            and r["outcome"] in ("EPISODE_SENT", "EPISODE_GATED")
        ]
        assert not outreach, (
            f"{sub_id}: CONTROL produced outreach evidence during the outage"
        )

    return {
        "episodes": len(driven),
        "dispatched": sorted(s for s, v in driven.items() if v["status"] == "dispatched"),
        "gated": sorted(s for s, v in driven.items() if v["status"] == "gated"),
        "blocked": sorted(s for s, v in driven.items() if v["status"] == "blocked"),
        "cycle_rows": len(cycle_rows),
        "degraded_rows": sum(1 for r in cycle_rows if r["mode"] == "DEGRADED"),
        "llm_model": getattr(outage_client, "model_name", None)
        or get_settings().llm_model,
        "summaries": summaries,
    }
