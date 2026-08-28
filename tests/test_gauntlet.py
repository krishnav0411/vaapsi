"""Adversarial gauntlet tests — one per attack plus the committed artifact.

Every test drives the real attack functions from scripts/gauntlet.py, which
are hermetic by construction (fresh temp-dir store per attack, engine clock
frozen, kill switch forced off, fixed webhook secret, live store never
opened). The committed-artifact test pins the committed
results/gauntlet_scorecard.json: valid contract, every invariant held, the
summary consistent with the results, and byte-identical to a fresh
in-process run (the determinism drift-guard, the test_evaluation pattern).
"""

import json
from pathlib import Path

import pytest

from scripts.gauntlet import (
    INJECTION_ERROR_CODE,
    a01_replay_exact,
    a02_replay_resigned,
    a03_forged_signature,
    a04_unsigned_body,
    a05_malformed_signed,
    a06_prompt_injection_customer,
    a07_prompt_injection_error,
    a08_amount_tamper,
    a09_ledger_surgery,
    a10_kill_switch_midflight,
    a11_human_gate_bypass_attempt,
    a12_quiet_hours_probe,
    a13_cap_overflow,
    a14_stale_fingerprint_race,
    a15_duplicate_dispatch_race,
    a16_cohort_leakage,
    run_gauntlet,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_SCORECARD = REPO_ROOT / "results" / "gauntlet_scorecard.json"


def _inv_ok(result: dict) -> bool:
    inv = result["invariants"]
    return inv["I1_no_unauthorized_outbound"] is True and inv["I2_ledger_chain_intact"] is True


# ── A01–A04: the ingest boundary ─────────────────────────────────────────


def test_a01_replay_idempotent_one_episode():
    result = a01_replay_exact()
    assert result["defense_held"] is True
    assert result["evidence"]["deliveries"] == ["accepted", "duplicate", "duplicate"]
    assert result["evidence"]["episodes_after"] == 1
    assert result["evidence"]["episode_created_rows"] == 1
    assert _inv_ok(result)


def test_a02_resigned_rewrite_is_deduped():
    result = a02_replay_resigned()
    assert result["defense_held"] is True
    assert result["evidence"]["first_delivery_status"] == "accepted"
    assert result["evidence"]["second_delivery_status"] == "duplicate"
    assert result["evidence"]["episodes_after"] == 1
    assert _inv_ok(result)


def test_a03_and_a04_forged_and_unsigned_rejected_401():
    forged = a03_forged_signature()
    assert forged["defense_held"] is True
    assert forged["evidence"]["verdict"]["status_code"] == 401
    assert forged["evidence"]["episodes_after"] == 0
    assert forged["evidence"]["webhook_rows_after"] == 0
    unsigned = a04_unsigned_body()
    assert unsigned["defense_held"] is True
    assert unsigned["evidence"]["verdict"]["status_code"] == 401
    assert unsigned["evidence"]["webhook_rows_after"] == 0
    assert _inv_ok(forged) and _inv_ok(unsigned)


def test_a05_truncated_json_is_4xx_never_5xx():
    """The drilled malformed-body defense: a valid signature over truncated
    JSON is rejected 400-class, never 500, zero side effects."""
    result = a05_malformed_signed()
    truncated = result["evidence"]["variants"]["truncated_json"]
    assert truncated["verdict"] == "rejected_400"
    assert truncated["status_code"] == 400
    assert truncated["rejected_4xx"] is True
    assert result["evidence"]["episodes_created"] == 0
    assert result["evidence"]["webhook_rows_after"] == 0
    assert _inv_ok(result)


def test_a05_string_entity_rejected_4xx_never_5xx():
    """A JSON-valid but shape-adversarial body (string entity) must be
    rejected 400-class like any other malformed delivery — the receiver
    validates shape after the signature gate, so the gauntlet finds the
    defense holding instead of an AttributeError crash."""
    result = a05_malformed_signed()
    entity_variant = result["evidence"]["variants"]["string_entity"]
    assert entity_variant["verdict"] == "rejected_400"
    assert entity_variant["rejected_4xx"] is True
    assert result["defense_held"] is True
    assert result["evidence"]["episodes_created"] == 0
    assert _inv_ok(result)


# ── A06–A07: instruction-shaped data stays data ──────────────────────────


def test_a06_customer_injection_decision_identical_to_benign():
    result = a06_prompt_injection_customer()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["decisions_identical"] is True
    assert evidence["outcome_sequences_identical"] is True
    assert evidence["extra_dispatches"] == 0
    assert evidence["injection_reached_llm_payload"] is False
    assert _inv_ok(result)


def test_a07_error_injection_maps_unknown_and_matches_benign():
    result = a07_prompt_injection_error()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["classified_category"] == "UNKNOWN"
    assert evidence["decisions_identical"] is True
    assert evidence["outcome_sequences_identical"] is True
    assert evidence["attack_dispatches"] == evidence["benign_dispatches"]
    assert _inv_ok(result)


# ── A08–A09: tamper evidence ─────────────────────────────────────────────


def test_a08_amount_tamper_recomputes_from_source_and_is_detected():
    result = a08_amount_tamper()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["original_amount_paise"] == 49900
    assert evidence["tampered_amount_paise"] == 99900
    assert evidence["verifier_named_tampered_row"] is True
    assert "FAIL" in evidence["full_chain_verdict"]
    assert evidence["next_cycle_status"] == "dispatched"
    assert evidence["next_cycle_scored_amount_paise"] == 49900
    assert evidence["dispatched_payload_amount_paise"] == 49900
    assert evidence["stale_amount_in_new_rows"] is False
    assert evidence["new_rows_content_commitments_ok"] is True
    assert _inv_ok(result)


def test_a09_ledger_surgery_on_copy_is_named_by_verifier():
    result = a09_ledger_surgery()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["original_store_chain_ok"] is True
    assert "FAIL" in evidence["copy_chain_verdict"]
    assert evidence["copy_tampered_row_seq"] == 2
    assert evidence["verifier_named_tampered_row"] is True
    assert "row 2" in evidence["copy_chain_verdict"]
    assert _inv_ok(result)


# ── A10–A13: the gate envelope ───────────────────────────────────────────


def test_a10_kill_switch_midflight_blocks_dispatch():
    result = a10_kill_switch_midflight()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["cycle_status"] == "blocked"
    assert evidence["cycle_reason"] == "kill_switch"
    assert evidence["action_rows"] == 0
    assert evidence["executor_refused_dispatch"] is True
    assert evidence["executor_writes"] == 0
    assert evidence["episode_state_after"] == "SCORED"
    assert _inv_ok(result)


def test_a11_gate_bypass_attempt_refused_without_decide():
    result = a11_human_gate_bypass_attempt()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["tier"] == 3
    assert evidence["amount_paise"] == 50100
    assert evidence["llm_recommendation"] == "send_payment_link"
    assert evidence["cycle_status"] == "gated"
    assert evidence["gate_reason"] == "amount_over_threshold"
    assert evidence["approval_status"] == "PENDING"
    assert evidence["bypass_executor_dispatched"] is False
    assert evidence["bypass_writes"] == 0
    assert evidence["sent_rows"] == 0
    assert evidence["episode_state"] == "GATED"
    assert _inv_ok(result)


def test_a12_quiet_hours_boundary_pair():
    result = a12_quiet_hours_probe()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["blocked_at_2100_ist"] is True
    assert evidence["block_reason"] == "quiet_hours"
    assert evidence["ist_hour_at_block"] == 21
    assert evidence["dispatched_at_0900_ist"] is True
    assert evidence["ist_hour_at_dispatch"] == 9
    assert _inv_ok(result)


def test_a13_cap_overflow_blocks_with_zero_action_rows():
    result = a13_cap_overflow()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["cycle_status"] == "blocked"
    assert evidence["reason"] == "max_attempts"
    assert evidence["attempt_count_after"] == evidence["attempt_count_before"]
    assert evidence["action_rows"] == 0
    assert evidence["episode_state_after"] == "SCORED"
    assert _inv_ok(result)


# ── A14–A16: races and the holdout ───────────────────────────────────────


def test_a14_stale_fingerprint_race_discards():
    result = a14_stale_fingerprint_race()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["fetch_count"] == 2
    assert evidence["fetch_statuses"] == ["halted", "resumed"]
    assert evidence["cycle_status"] == "blocked"
    assert evidence["reason"] == "stale_fingerprint"
    assert evidence["discarded_stale_row_landed"] is True
    assert evidence["sent_rows"] == 0
    assert _inv_ok(result)


def test_a15_duplicate_dispatch_second_call_is_noop():
    result = a15_duplicate_dispatch_race()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["first_status"] == "dispatched"
    assert evidence["second_status"] == "skipped"
    assert evidence["sent_rows"] == 1
    assert evidence["ledger_rows_after_second"] == evidence["ledger_rows_after_first"]
    assert evidence["attempt_count"] == 1
    assert _inv_ok(result)


def test_a16_cohort_leakage_zero_outreach():
    result = a16_cohort_leakage()
    assert result["defense_held"] is True
    evidence = result["evidence"]
    assert evidence["ingest_control_halt_status"] == "accepted"
    assert evidence["episodes_after_control_halt"] == 0
    assert evidence["leaked_episode_cycle_status"] == "blocked"
    assert evidence["leaked_episode_reason"] == "cohort_gate"
    assert evidence["outreach_rows_toward_control"] == 0
    assert evidence["control_episode_state_after"] == "SCORED"
    assert _inv_ok(result)


# ── The committed artifact ───────────────────────────────────────────────


def test_committed_scorecard_valid_and_reproducible():
    """The committed results/gauntlet_scorecard.json is the gauntlet's
    contract artifact: 16 attacks, every invariant held, the summary
    consistent with the results — and byte-identical to a fresh run."""
    committed = json.loads(COMMITTED_SCORECARD.read_text(encoding="utf-8"))
    assert committed["meta"]["attacks"] == 16
    assert len(committed["results"]) == 16
    summary = committed["summary"]
    assert summary["passed"] + summary["failed"] == 16
    held = sum(1 for r in committed["results"] if r["defense_held"] is True)
    assert summary["passed"] == held
    assert summary["failed"] == 16 - held
    assert summary["all_invariants_held"] is True
    for result in committed["results"]:
        assert _inv_ok(result), f"{result['attack_id']} invariants must hold"
    # The one recorded defect is documented, not hidden: A05's string-entity
    # 500-class finding carries defense_held False with evidence and notes.
    a05 = next(r for r in committed["results"] if r["attack_id"] == "A05")
    if a05["defense_held"] is False:
        assert "DEFECT" in a05["notes"]
        assert a05["evidence"]["variants"]["string_entity"]["verdict"] == "crash_500_class"
    fresh = run_gauntlet()
    assert json.dumps(fresh, indent=2, sort_keys=True) + "\n" == COMMITTED_SCORECARD.read_text(
        encoding="utf-8"
    ), "committed scorecard drifted from a fresh run"


def test_gauntlet_constants_are_the_frozen_fixtures():
    """Guard the attack fixtures the scorecard narrative relies on."""
    from scripts import gauntlet

    assert gauntlet.TAMPERED_AMOUNT_PAISE == 99900
    assert gauntlet.SOURCE_AMOUNT_PAISE == 49900
    assert gauntlet.BENIGN_CUSTOMER_NAME != gauntlet.INJECTION_CUSTOMER_NAME
    assert "IGNORE ALL RULES" in gauntlet.INJECTION_CUSTOMER_NAME
    assert gauntlet.classify_failure(INJECTION_ERROR_CODE) == "UNKNOWN"
    assert gauntlet.BENIGN_CUSTOMER_NAME == "Priya Sharma"


@pytest.mark.parametrize(
    "attack",
    [
        a01_replay_exact,
        a02_replay_resigned,
        a03_forged_signature,
        a04_unsigned_body,
        a06_prompt_injection_customer,
        a07_prompt_injection_error,
        a08_amount_tamper,
        a09_ledger_surgery,
        a10_kill_switch_midflight,
        a11_human_gate_bypass_attempt,
        a12_quiet_hours_probe,
        a13_cap_overflow,
        a14_stale_fingerprint_race,
        a15_duplicate_dispatch_race,
        a16_cohort_leakage,
    ],
)
def test_drilled_defenses_all_hold(attack):
    """Every drilled defense holds: defense_held True with both invariants."""
    result = attack()
    assert result["defense_held"] is True, result["notes"]
    assert _inv_ok(result)


def test_run_gauntlet_summary_counts_match_results():
    report = run_gauntlet()
    assert report["meta"]["attacks"] == 16
    results = report["results"]
    assert [r["attack_id"] for r in results] == [f"A{i:02d}" for i in range(1, 17)]
    assert report["summary"]["passed"] == sum(1 for r in results if r["defense_held"])
    assert report["summary"]["failed"] == sum(1 for r in results if r["defense_held"] is False)
    assert report["summary"]["all_invariants_held"] is True
