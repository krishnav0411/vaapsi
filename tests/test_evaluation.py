"""Offline evaluation tests — corpus, outcome model, runner, drift-guard.

The house pattern applies end to end: everything runs offline against fresh
temp-dir stores (the runner monkeypatches settings.data_dir per case exactly
like tests/test_orchestrator.py does), the engine clock is frozen per case,
and the live data/vaapsi.sqlite3 is never touched. Covers: corpus
determinism (same seed → same corpus, different seed → different), all 16
families present at n=200, the ~30% CONTROL fraction, zero false outreach
in every arm (no must_not_contact case is ever dispatched), outcome-model
determinism (blake2b(case, arm)), a known-answer Wilson CI, the runner
end-to-end with all four arms and byte-identical repeat runs, the
drift-guard passing on the committed results and failing on a tampered
copy, and the committed results/evaluation.json matching a fresh full run.
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.eval_corpus import (
    FAMILY_ORDER,
    MUST_NOT_CONTACT_FAMILIES,
    build_corpus,
)
from scripts.run_evaluation import (
    ALL_ARMS,
    PIPELINE_ARMS,
    best_action_class,
    outcome_draw,
    run_case_arm,
    run_evaluation,
    wilson_interval,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_EVAL = REPO_ROOT / "results" / "evaluation.json"
README = REPO_ROOT / "README.md"

SEED = 1403
N_CASES = 200

# The documented per-family latent table (scripts/eval_corpus.py).
LATENT_TABLE = {
    "already_charged": 0.0,
    "retry_active": 0.60,
    "auth_failure": 0.50,
    "insufficient_funds": 0.55,
    "transient_gateway": 0.45,
    "network_timeout": 0.60,
    "mandate_revoked": 0.05,
    "duplicate_dispatch": 0.0,
    "stale_capture_race": 0.50,
    "adversarial_name_injection": 0.40,
    "quiet_hours_boundary": 0.55,
    "cap_overflow": 0.50,
    "kill_switch_midflight": 0.50,
    "control_cohort": 0.45,
    "young_subscription": 0.50,
    "aged_subscription": 0.35,
}


# ── corpus ───────────────────────────────────────────────────────────────


def test_corpus_deterministic_same_seed():
    assert build_corpus(SEED, N_CASES) == build_corpus(SEED, N_CASES)


def test_corpus_differs_across_seeds():
    assert build_corpus(SEED, N_CASES) != build_corpus(SEED + 1, N_CASES)


def test_corpus_all_16_families_at_200():
    corpus = build_corpus(SEED, N_CASES)
    families = {case["family"] for case in corpus}
    assert families == set(FAMILY_ORDER)
    assert len(families) == 16


def test_corpus_control_fraction_about_30_percent():
    corpus = build_corpus(SEED, N_CASES)
    control = sum(1 for case in corpus if case["cohort"] == "CONTROL")
    assert 0.25 <= control / len(corpus) <= 0.35


def test_corpus_case_ids_are_stable_and_unique():
    corpus = build_corpus(SEED, N_CASES)
    ids = [case["case_id"] for case in corpus]
    assert len(set(ids)) == len(ids)  # unique across the corpus
    assert all(len(case_id) == 16 and int(case_id, 16) >= 0 for case_id in ids)
    # Stable: rebuilt byte-identically (same order, same ids).
    assert ids == [case["case_id"] for case in build_corpus(SEED, N_CASES)]


def test_corpus_case_id_formula():
    """case_id = sha1(f"{family}:{index}:{seed}").hexdigest()[:16]."""
    corpus = build_corpus(SEED, N_CASES)
    per_family: dict[str, int] = {}
    for case in corpus:
        index = per_family.get(case["family"], 0)
        per_family[case["family"]] = index + 1
        expected = hashlib.sha1(
            f"{case['family']}:{index}:{SEED}".encode()
        ).hexdigest()[:16]
        assert case["case_id"] == expected


def test_corpus_amounts_realistic():
    for case in build_corpus(SEED, N_CASES):
        assert 19900 <= case["amount_paise"] <= 499900


def test_corpus_must_not_contact_families_flagged():
    corpus = build_corpus(SEED, N_CASES)
    for case in corpus:
        assert case["must_not_contact"] == (case["family"] in MUST_NOT_CONTACT_FAMILIES)
    assert MUST_NOT_CONTACT_FAMILIES == frozenset(
        {"already_charged", "mandate_revoked", "duplicate_dispatch", "control_cohort"}
    )


def test_corpus_latent_table_documented():
    for case in build_corpus(SEED, N_CASES):
        assert case["latent_recovery_p"] == LATENT_TABLE[case["family"]]


def test_corpus_fields_complete():
    expected = {
        "case_id",
        "family",
        "cohort",
        "amount_paise",
        "last_error_code",
        "consecutive_failures",
        "age_days",
        "auth_attempts",
        "must_not_contact",
        "latent_recovery_p",
    }
    for case in build_corpus(SEED, N_CASES):
        assert set(case) == expected
        assert 1 <= case["consecutive_failures"] <= 3
        assert case["cohort"] in ("CONTROL", "TREATMENT")


def test_corpus_scales_to_smaller_n():
    corpus = build_corpus(SEED, 32)
    assert len(corpus) == 32
    assert {case["family"] for case in corpus} == set(FAMILY_ORDER)


# ── outcome model ────────────────────────────────────────────────────────


def test_outcome_draw_deterministic_per_case_and_arm():
    corpus = build_corpus(SEED, 20)
    for case in corpus:
        for arm in ALL_ARMS:
            assert outcome_draw(case["case_id"], arm) == outcome_draw(case["case_id"], arm)


def test_outcome_draw_matches_documented_formula():
    corpus = build_corpus(SEED, 20)
    for case in corpus:
        expected = int(
            hashlib.blake2b(f"{case['case_id']}:rules_only".encode(), digest_size=8).hexdigest(),
            16,
        ) / 2**64
        assert outcome_draw(case["case_id"], "rules_only") == expected


def test_best_action_class_documented_mapping():
    corpus = build_corpus(SEED, N_CASES)
    for case in corpus:
        best = best_action_class(case)
        if case["family"] in {"retry_active", "stale_capture_race"}:
            assert best == "platform_retry"
        elif case["family"] in MUST_NOT_CONTACT_FAMILIES:
            assert best == "none"
        else:
            assert best == "payment_link"


def test_wilson_known_answer_half_coin():
    # Wilson 95% CI for 1/2 with z=1.959963: the classic [0.0945, 0.9055].
    lo, hi = wilson_interval(1, 2)
    assert lo == pytest.approx(0.0945, abs=1e-3)
    assert hi == pytest.approx(0.9055, abs=1e-3)


def test_wilson_edge_cases():
    assert wilson_interval(0, 0) == (0.0, 0.0)
    lo0, hi0 = wilson_interval(0, 10)
    assert lo0 == 0.0 and 0.0 <= hi0 < 0.3
    lo1, hi1 = wilson_interval(10, 10)
    assert 0.7 < lo1 <= 1.0 and hi1 == pytest.approx(1.0, abs=1e-9)
    lo, hi = wilson_interval(40, 86)
    assert lo <= 40 / 86 <= hi


# ── runner ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def small_report():
    """One 20-case run reused by the cheap runner tests."""
    return run_evaluation(SEED, 20, list(ALL_ARMS))


def test_runner_end_to_end_20_cases_all_arms(small_report):
    report = small_report
    assert set(report) == {"meta", "arms", "invariants"}
    assert set(report["arms"]) == set(ALL_ARMS)
    assert report["meta"]["seed"] == SEED
    assert report["meta"]["cases"] == 20
    assert report["meta"]["families"] == 16
    assert report["meta"]["generated_by"] == "scripts/run_evaluation.py"
    assert report["meta"]["note"] == "synthetic outcome model - not real money"
    assert report["invariants"]["zero_false_outreach"] is True
    assert report["invariants"]["chain_valid_after"] is True
    assert report["invariants"]["deterministic_seed"] == SEED
    for agg in report["arms"].values():
        assert agg["recovered"] <= agg["attempts"]
        assert agg["false_outreach"] == 0


def test_runner_two_runs_byte_identical(small_report):
    again = run_evaluation(SEED, 20, list(ALL_ARMS))
    first = json.dumps(small_report, indent=2, sort_keys=True)
    second = json.dumps(again, indent=2, sort_keys=True)
    assert first == second


def test_pipeline_arms_route_identically(small_report):
    """The LLM flavors; the rules decide: all three pipeline arms share the
    exact same routing counts (only the per-arm draw differs)."""
    aggs = [small_report["arms"][arm] for arm in PIPELINE_ARMS]
    for key in ("attempts", "gated", "request_retry", "discarded_stale", "blocked"):
        assert {agg[key] for agg in aggs} == {aggs[0][key]}, key


def test_no_agent_is_platform_only(small_report):
    agg = small_report["arms"]["no_agent"]
    assert agg["attempts"] == 20  # the platform dunning touches every case
    assert agg["gated"] == 0 and agg["request_retry"] == 0
    assert agg["discarded_stale"] == 0 and agg["blocked"] == 0
    assert agg["false_outreach"] == 0


def test_no_must_not_contact_case_contacted_in_any_arm(tmp_path):
    """Zero false outreach, per case: no must_not_contact (or CONTROL) case
    is ever dispatched in any pipeline arm."""
    corpus = build_corpus(SEED, 32)
    for arm in PIPELINE_ARMS:
        records = [run_case_arm(case, arm, tmp_path) for case in corpus]
        for record in records:
            if record["must_not_contact"] or record["cohort"] == "CONTROL":
                assert record["status"] != "dispatched", (arm, record)


def test_oracle_gap_matches_oracle_rate(small_report):
    for agg in small_report["arms"].values():
        expected = round(agg["oracle_rate"] - agg["recovery_rate"], 6)
        assert agg["oracle_gap"] == pytest.approx(expected, abs=1e-6)


def test_committed_invariants_and_meta():
    report = json.loads(COMMITTED_EVAL.read_text(encoding="utf-8"))
    assert report["meta"]["seed"] == SEED
    assert report["meta"]["cases"] == N_CASES
    assert report["meta"]["families"] == 16
    assert report["meta"]["generated_by"] == "scripts/run_evaluation.py"
    assert report["meta"]["note"] == "synthetic outcome model - not real money"
    assert report["invariants"] == {
        "zero_false_outreach": True,
        "chain_valid_after": True,
        "deterministic_seed": SEED,
    }


def test_committed_evaluation_matches_fresh_full_run():
    """The committed results file is exactly what the current code produces:
    a fresh 200-case run must reproduce it value for value."""
    committed = json.loads(COMMITTED_EVAL.read_text(encoding="utf-8"))
    fresh = run_evaluation(SEED, N_CASES, list(ALL_ARMS))
    assert fresh == committed


# ── drift-guard (scripts/verify_numbers.py) ──────────────────────────────


def _run_verify(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/verify_numbers.py", *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,  # the exit code IS the assertion target
    )


def test_verify_numbers_passes_on_committed_files():
    result = _run_verify()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: schema completeness" in result.stdout
    assert "PASS: per-arm arithmetic" in result.stdout
    assert "PASS: README.md numbers block" in result.stdout
    assert "VERIFY_NUMBERS PASSED" in result.stdout


def test_verify_numbers_fails_on_tampered_copy(tmp_path):
    """Flip one digit in one arm's recovered count: the drift-guard must
    fail loudly (arithmetic and README cross-check both catch it)."""
    raw = COMMITTED_EVAL.read_text(encoding="utf-8")
    match = re.search(r'"recovered": (\d)(\d)', raw)
    assert match is not None
    first_digit = match.group(1)
    flipped = "9" if first_digit != "9" else "8"
    tampered = raw[: match.start(1)] + flipped + raw[match.end(1) :]
    tampered_path = tmp_path / "evaluation_tampered.json"
    tampered_path.write_text(tampered, encoding="utf-8")
    result = _run_verify("--eval", str(tampered_path))
    assert result.returncode != 0
    assert "VERIFY_NUMBERS FAILED" in result.stdout


def test_verify_numbers_fails_when_readme_block_missing(tmp_path):
    result = _run_verify("--readme", str(tmp_path / "nope.md"))
    assert result.returncode != 0
    assert "FAIL" in result.stdout


def test_readme_block_carries_every_arm():
    text = README.read_text(encoding="utf-8")
    start = text.find("<!-- eval:start -->")
    end = text.find("<!-- eval:end -->")
    assert start != -1 and end != -1 and end > start
    block = text[start:end]
    report = json.loads(COMMITTED_EVAL.read_text(encoding="utf-8"))
    for arm in report["arms"]:
        assert re.search(rf"^\|\s*{arm}\s*\|", block, re.MULTILINE), arm


def test_live_store_untouched_by_eval_path():
    """The eval never reads or writes the live store: the runner's own
    monkeypatching (previous_data_dir restore) plus fresh tmp stores per
    case guarantee it — assert the restore contract holds after a run."""
    from app.settings import get_settings

    before = get_settings().data_dir
    run_evaluation(SEED, 4, ["no_agent", "rules_only"])
    assert get_settings().data_dir == before
