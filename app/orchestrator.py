"""Recovery orchestrator — one bounded, auditable cycle per halted episode.

run_recovery_cycle() wires the whole D3 pipeline behind one call: find the
subscription's open episode → diagnose (features from its webhook events,
via the deterministic scorer) → score (tier) → decide the outreach flavor
→ gate through the policy engine → act. The DECISION is deterministic-
first: when an LLMClient is injected its validated recommendation picks
the flavor, but ANY LLM failure (unavailable, malformed, out-of-allowlist
— app.llm.base.LLMError) falls back to the rules-only choice, and TIER 3
always escalates to the human gate no matter what the model said — the
model flavors outreach, it never widens what Vaapsi may do.

Engine-mode taxonomy (ledger rows): 'NORMAL' when the LLM decided,
'DEGRADED' when the rules-only fallback was used. Every row the cycle
writes (diagnose, score, sent, gated) carries the mode, so the ledger
answers "who decided this?" for the whole episode at a glance.

Ordering discipline: the policy engine runs AFTER the episode reaches
SCORED (it refuses non-SCORED states by design) and BEFORE any outreach —
a BLOCKED verdict stops the cycle with zero action writes: no dispatch,
no approval row, no attempt. The diagnose/score rows that precede the
gate are analysis evidence (the scorer is pure), not outreach — a blocked
episode ends SCORED with attempt_count untouched, which is exactly how an
auditor distinguishes "scored, then refused" from "never looked at".

States the cycle does not drive: GATED (awaits a human decision via
app.gates.human_gate.decide) and SENT/VERIFIED/CLOSED/VOIDED (nothing left
to decide) are returned as 'skipped'; a subscription with no open episode
returns 'no_open_episode' with zero writes.
"""

import hashlib
import sqlite3
from typing import Any

import httpx

from app.actions.base import ActionClient
from app.actions.execute import execute_episode_action
from app.actions.request_retry import maybe_request_retry
from app.audit import ledger
from app.audit.ledger import canonical_json
from app.core import episodes
from app.gates import human_gate
from app.llm.base import LLMClient, LLMError
from app.policy import fencing
from app.policy.engine import evaluate
from app.scoring.scorecard import ScoreResult, score_episode
from app.settings import get_settings

# Rules-only fallback choices (DEGRADED mode), keyed by scorer tier.
# Tier 3 has NO self-serve choice: its fallback IS the human gate.
FALLBACK_CHOICES: dict[int, dict[str, str]] = {
    1: {"action": "send_payment_link", "channel": "payment_link", "message_variant": "gentle"},
    2: {"action": "send_payment_link", "channel": "payment_link", "message_variant": "standard"},
}

DRIVABLE_STATES: frozenset[str] = frozenset({"NEW", "DIAGNOSED", "SCORED"})


def _llm_payload(episode: dict[str, Any], score: ScoreResult) -> dict[str, Any]:
    """The recommendation request payload — structured data only.

    Customer/event data enters the LLM exclusively through this payload,
    which the client serializes inside its marked untrusted fence (the
    injection-hardening boundary lives in app.llm, not here). No free
    text, no invented fields: tier, features, and the episode's counters.
    """
    return {
        "subscription_id": episode["subscription_id"],
        "episode_id": episode["id"],
        "attempt_count": episode["attempt_count"],
        "tier": score.tier,
        "features": score.features,
    }


def _llm_evidence(client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Request hash + model label for the ledger — output added after the call.

    The request is committed to the ledger as a hash (not the payload
    itself) so customer data never lands in the audit store in plaintext;
    the model's raw OUTPUT does land verbatim as llm_output_raw — that is
    the evidence an auditor replays. Model label: a client-provided
    `model_name` when the implementation offers one (test fakes), else the
    settings-configured model the real adapter is built with.
    """
    return {
        "llm_request_hash": hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest(),
        "llm_model": getattr(client, "model_name", None) or get_settings().llm_model,
    }


def _decide(
    episode: dict[str, Any],
    score: ScoreResult,
    client: LLMClient | None,
) -> tuple[dict[str, str] | None, str, dict[str, Any]]:
    """Pick the outreach flavor; return (choice, mode, ledger evidence).

    choice=None means "no self-serve outreach": the human gate. With a
    client, its validated recommendation wins (mode NORMAL) — but only the
    flavor: tier 3 still routes to the gate below. Any LLMError (or no
    client at all) falls back to the rules-only choice for the tier
    (mode DEGRADED). The returned evidence carries the request hash and
    model always when a client was consulted, plus the raw model output
    when it produced a usable recommendation.
    """
    if client is None:
        return FALLBACK_CHOICES.get(score.tier), "DEGRADED", {}
    payload = _llm_payload(episode, score)
    evidence = _llm_evidence(client, payload)
    try:
        recommendation = client.recommend(payload)
    except (LLMError, httpx.HTTPError):
        # ANY LLM failure degrades to rules-only: LLMError covers the
        # adapter's unavailable/invalid-output verdicts (incl. allowlist
        # enforcement), httpx.HTTPError covers transport errors the
        # adapter does not wrap (connection refused, DNS). A degraded
        # choice can never be wider than the deterministic one.
        return FALLBACK_CHOICES.get(score.tier), "DEGRADED", evidence
    evidence["llm_output_raw"] = recommendation["raw"]
    choice = {
        "action": recommendation["action"],
        "channel": recommendation["channel"],
        "message_variant": recommendation["message_variant"],
    }
    return choice, "NORMAL", evidence


def run_recovery_cycle(
    conn: sqlite3.Connection,
    subscription_id: str,
    client: LLMClient | None = None,
    *,
    action_client: ActionClient | None = None,
    fence_client: Any | None = None,
) -> dict[str, Any]:
    """Drive one recovery cycle for a subscription's open episode.

    Returns a summary dict: {'subscription_id', 'episode_id', 'status',
    'tier', 'mode', 'variant', 'reason', 'state_after', 'approval_id'} —
    status one of 'dispatched' | 'gated' | 'blocked' | 'skipped' |
    'no_open_episode' | 'request_retry'. `client` is the LLMClient (None →
    rules-only, DEGRADED); `action_client` is the Razorpay-backed
    ActionClient (None → offline RecordingStub, the demo/test default — no
    network unless prod injects one explicitly). `fence_client` is the
    fetch_subscription-capable provider client behind the dispatch fences
    (app.policy.fencing + app.actions.request_retry); None disables
    fencing entirely (the offline default — existing behavior unchanged).
    With one injected, the cycle runs three fences, all ledgered and all
    degrading to logged no-ops rather than raising:

    1. guard_dispatch — look-before-leap: a subscription not freshly
       "halted" writes FENCE_BLOCKED and ends the cycle before any work.
    2. stale-inference guard — the fingerprint is snapshotted before the
       LLM call and re-computed after; a change writes DISCARDED_STALE and
       leaves the episode for the next cycle (two-transaction pattern).
    3. verify-after-write — after a link exists, a moved subscription
       triggers best-effort link cancellation plus an always-on
       COMPENSATION row; REQUEST_RETRY stands back while the platform's
       own dunning will retry (ACTION_REQUEST_RETRY, revisit_at = now +
       cooling), falling through to the payment-link path otherwise.
    """
    summary: dict[str, Any] = {
        "subscription_id": subscription_id,
        "episode_id": None,
        "status": None,
        "tier": None,
        "mode": None,
        "variant": None,
        "reason": None,
        "state_after": None,
        "approval_id": None,
    }
    open_episodes = episodes.get_open_episodes(conn, subscription_id)
    if not open_episodes:
        summary["status"] = "no_open_episode"
        summary["reason"] = "no open episode for subscription"
        return summary
    episode = open_episodes[0]
    summary["episode_id"] = episode["id"]
    if episode["state"] not in DRIVABLE_STATES:
        summary["status"] = "skipped"
        summary["reason"] = f"episode state {episode['state']} is not drivable"
        summary["state_after"] = episode["state"]
        return summary

    # Fence 1 — look-before-leap. No cycle acts on a subscription the
    # provider no longer reports as halted (or that cannot be verified at
    # all — fail-closed). A blocked fence lands its own ledger row and
    # ends the cycle with zero further action; the episode stays put.
    snapshot: dict[str, Any] | None = None
    if fence_client is not None:
        guard = fencing.guard_dispatch(fence_client, episode)
        if guard["blocked"]:
            ledger.append(
                conn,
                subscription_id=subscription_id,
                trigger_event="fence.guard_dispatch",
                policy_eval={
                    "decision": "fence_blocked",
                    "episode_id": episode["id"],
                    "reason": guard["reason"],
                    "fresh_status": guard["fresh_status"],
                    "error": guard["error"],
                },
                human_gate=False,
                outcome=fencing.FENCE_BLOCKED_OUTCOME,
                mode=episodes.DEFAULT_MODE,
            )
            summary["status"] = "blocked"
            summary["reason"] = guard["reason"]
            summary["state_after"] = episode["state"]
            return summary
        snapshot = guard["subscription"]

    score = score_episode(conn, episode)
    summary["tier"] = score.tier
    choice, mode, llm_evidence = _decide(episode, score, client)
    summary["mode"] = mode

    # Fence 2 — stale-inference guard (two-transaction pattern): the
    # fingerprint above was snapshotted from the provider before the LLM
    # call; now that inference returned with no lock held, re-compute from
    # a fresh fetch. A changed fingerprint means the world moved under us:
    # the diagnosis is discarded, DISCARDED_STALE lands in the ledger, and
    # the episode is left untouched for the next cycle. An unreadable
    # provider degrades to proceeding — the verify-after-write fence still
    # covers the dispatch.
    if fence_client is not None and snapshot is not None:
        pre_fp = fencing.fingerprint_subscription(snapshot)
        recheck = fencing.fresh_fingerprint(fence_client, subscription_id)
        if recheck["fingerprint"] is not None and recheck["fingerprint"] != pre_fp:
            ledger.append(
                conn,
                subscription_id=subscription_id,
                trigger_event="fence.stale_inference",
                policy_eval={
                    "decision": "discard_stale",
                    "episode_id": episode["id"],
                    "pre_fingerprint": pre_fp,
                    "post_fingerprint": recheck["fingerprint"],
                },
                human_gate=False,
                outcome=fencing.DISCARDED_STALE_OUTCOME,
                mode=mode,
            )
            summary["status"] = "blocked"
            summary["reason"] = "stale_fingerprint"
            summary["state_after"] = episode["state"]
            return summary

    # Pipeline states land with their own ledger rows (the D2 demo stamped
    # these by hand; D3 makes them real evidence). Both transitions carry
    # the decision's mode so the chain shows who decided, end to end.
    if episode["state"] == "NEW":
        episode = episodes.transition(
            conn,
            episode["id"],
            "DIAGNOSED",
            ledger_fields={
                "features": score.features,
                "policy_eval": {
                    "decision": "diagnose",
                    "from_state": "NEW",
                    "to_state": "DIAGNOSED",
                },
                "mode": mode,
            },
        )
    episode = episodes.transition(
        conn,
        episode["id"],
        "SCORED",
        ledger_fields={
            "score": float(score.tier),
            "features": score.features,
            "policy_eval": {
                "decision": "score",
                "tier": score.tier,
                "rationale": score.rationale,
                "mode": mode,
                "choice": choice,
            },
            "mode": mode,
            **llm_evidence,
        },
    )

    decision = evaluate(conn, subscription_id, episode)
    if decision.action != "SEND":
        # Zero action writes: the episode stays SCORED, attempt_count
        # untouched — the pipeline rows above are analysis, not outreach.
        summary["status"] = "blocked"
        summary["reason"] = decision.reason
        summary["state_after"] = "SCORED"
        return summary

    # Gate routing is deterministic, never the model's call: tier 3 always
    # escalates, and the amount threshold is a second, independent trigger.
    gated_episode = {**episode, "amount_paise": score.features["amount_paise"]}
    amount_over = human_gate.requires_human_gate(gated_episode, decision)
    if score.tier == 3 or amount_over:
        reason = (
            human_gate.GATE_REASON_AMOUNT
            if amount_over
            else human_gate.GATE_REASON_TIER3
        )
        approval_id = human_gate.enqueue_for_approval(
            conn,
            episode,
            reason,
            mode=mode,
            ledger_fields=llm_evidence,
        )
        summary["status"] = "gated"
        summary["reason"] = reason
        summary["approval_id"] = approval_id
    else:
        # REQUEST_RETRY — action selection before dispatch: while the
        # platform's own dunning will retry on its own, Vaapsi stands
        # back (one ACTION_REQUEST_RETRY ledger row, revisit_at = now +
        # cooling, no customer outreach, episode untouched). Consulted
        # only after every policy gate returned SEND and the human-gate
        # routing did not fire, so all existing gates (cooling, 48h
        # interval, attempt cap, quiet hours, cohort) bound it exactly
        # like any other action. Not retrying → fall through to the
        # existing payment-link path below.
        if fence_client is not None:
            retry = maybe_request_retry(conn, episode, fence_client, mode=mode)
            if retry["handled"]:
                summary["status"] = "request_retry"
                summary["reason"] = retry["reason"]
                summary["state_after"] = episodes.get_episode(conn, episode["id"])["state"]
                return summary
        result = execute_episode_action(conn, episode, client=action_client, mode=mode)
        # The pre-check above already saw SEND on a fresh SCORED row; the
        # executor re-evaluates atomically. A non-dispatch here would mean
        # a concurrent stop event won the race — report it, write nothing.
        if not result["dispatched"]:
            summary["status"] = "blocked"
            summary["reason"] = result["policy"]["reason"]
        else:
            summary["status"] = "dispatched"
            summary["reason"] = decision.reason
            # The flavor is only "used" when outreach actually left; a
            # gated episode reports variant None even though the model
            # (or the fallback) produced a choice.
            if choice is not None:
                summary["variant"] = choice["message_variant"]
            # Fence 3 — verify-after-write. The link exists now: re-check
            # that the subscription did not move while we dispatched. If
            # it did, the fence best-effort cancels the link and ALWAYS
            # lands a COMPENSATION row. A DLQ-quarantined dispatch never
            # produced a link id — nothing to compensate.
            link_id = None
            if result.get("action") is not None:
                link_id = (result["action"].get("rzp_response") or {}).get("link_id")
            if fence_client is not None and link_id:
                fencing.verify_after_write(conn, fence_client, subscription_id, link_id)
    summary["state_after"] = episodes.get_episode(conn, episode["id"])["state"]
    return summary
