"""CI drift-guard: validate results/evaluation.json against itself and README.md.

Three layers of checking, each printing PASS/FAIL lines:

  1. schema completeness — meta/arms/invariants carry exactly the keys the
     runner contract promises, every arm has every aggregate key.
  2. per-arm arithmetic — recovered <= attempts, recovery_rate ==
     recovered / attempts, the Wilson bounds bracket the rate, and
     oracle_gap == oracle_rate - recovery_rate.
  3. README cross-check — the numbers block delimited by
     <!-- eval:start --> and <!-- eval:end --> in README.md must carry a
     table row for every arm in evaluation.json with matching numbers
     (4-decimal rendering of the JSON values). A missing block, a missing
     arm row, or ANY drifted digit fails with a clear message and a
     nonzero exit.

Exits 0 only when every check passes; any FAIL line means the committed
evaluation results and the published numbers have diverged and CI stops.
"""

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_META_KEYS = ("seed", "cases", "families", "generated_by", "note")
REQUIRED_INVARIANT_KEYS = ("zero_false_outreach", "chain_valid_after", "deterministic_seed")
REQUIRED_ARMS = ("rules_only", "full_llm", "random_allowlist", "no_agent")
REQUIRED_ARM_KEYS = (
    "attempts",
    "recovered",
    "recovery_rate",
    "ci95_wilson_lo",
    "ci95_wilson_hi",
    "false_outreach",
    "gated",
    "request_retry",
    "discarded_stale",
    "blocked",
    "oracle_rate",
    "oracle_gap",
)
PIPELINE_ARMS = ("rules_only", "full_llm", "random_allowlist")

# README block markers (the drift-guard contract with README.md).
EVAL_START = "<!-- eval:start -->"
EVAL_END = "<!-- eval:end -->"

# | arm | attempts | recovered | rate | [lo, hi] | oracle_gap |
_ROW_RE = re.compile(
    r"^\|\s*(?P<arm>[a-z_]+)\s*\|\s*(?P<attempts>\d+)\s*\|\s*(?P<recovered>\d+)\s*\|"
    r"\s*(?P<rate>\d+\.\d+)\s*\|\s*\[(?P<lo>\d+\.\d+),\s*(?P<hi>\d+\.\d+)\]\s*\|"
    r"\s*(?P<gap>-?\d+\.\d+)\s*\|\s*$"
)

TOL = 1e-6          # float tolerance inside the JSON itself
README_TOL = 6e-5   # README renders 4 decimals; allow the rounding gap


def check_schema(report: dict) -> list[str]:
    """The report carries exactly the promised keys, with sane types."""
    problems: list[str] = []
    meta = report.get("meta")
    if not isinstance(meta, dict):
        return ["FAIL schema: 'meta' missing or not an object"]
    for key in REQUIRED_META_KEYS:
        if key not in meta:
            problems.append(f"FAIL schema: meta missing key {key!r}")
    for key in ("seed", "cases", "families"):
        if key in meta and not isinstance(meta[key], int):
            problems.append(f"FAIL schema: meta.{key} must be an integer")
    arms = report.get("arms")
    if not isinstance(arms, dict):
        problems.append("FAIL schema: 'arms' missing or not an object")
        return problems
    for arm in REQUIRED_ARMS:
        if arm not in arms:
            problems.append(f"FAIL schema: arm {arm!r} missing")
    for arm, agg in arms.items():
        if not isinstance(agg, dict):
            problems.append(f"FAIL schema: arms.{arm} is not an object")
            continue
        for key in REQUIRED_ARM_KEYS:
            if key not in agg:
                problems.append(f"FAIL schema: arms.{arm} missing key {key!r}")
    invariants = report.get("invariants")
    if not isinstance(invariants, dict):
        problems.append("FAIL schema: 'invariants' missing or not an object")
        return problems
    for key in REQUIRED_INVARIANT_KEYS:
        if key not in invariants:
            problems.append(f"FAIL schema: invariants missing key {key!r}")
    for key in ("zero_false_outreach", "chain_valid_after"):
        if key in invariants and invariants[key] is not True:
            problems.append(f"FAIL schema: invariant {key} is not true")
    return problems


def check_arithmetic(arm: str, agg: dict) -> list[str]:
    """Per-arm numbers must add up: recovered <= attempts, rate and the
    Wilson bounds consistent, oracle_gap the difference it claims to be."""
    problems: list[str] = []
    attempts = agg["attempts"]
    recovered = agg["recovered"]
    rate = agg["recovery_rate"]
    lo = agg["ci95_wilson_lo"]
    hi = agg["ci95_wilson_hi"]
    if not isinstance(attempts, int) or not isinstance(recovered, int):
        problems.append(f"FAIL arithmetic [{arm}]: attempts/recovered must be integers")
        return problems
    if attempts < 0 or recovered < 0:
        problems.append(f"FAIL arithmetic [{arm}]: negative attempts/recovered")
    if recovered > attempts:
        problems.append(
            f"FAIL arithmetic [{arm}]: recovered {recovered} exceeds attempts {attempts}"
        )
    if attempts == 0:
        if rate != 0.0:
            problems.append(f"FAIL arithmetic [{arm}]: zero attempts but rate {rate}")
        return problems
    expected_rate = recovered / attempts
    if abs(rate - expected_rate) > TOL:
        problems.append(
            f"FAIL arithmetic [{arm}]: rate {rate} != recovered/attempts "
            f"{expected_rate:.6f} ({recovered}/{attempts})"
        )
    if not (0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0):
        problems.append(f"FAIL arithmetic [{arm}]: Wilson bounds outside [0, 1]: [{lo}, {hi}]")
    if lo > rate + TOL or hi < rate - TOL:
        problems.append(
            f"FAIL arithmetic [{arm}]: Wilson bounds [{lo}, {hi}] do not bracket rate {rate}"
        )
    gap = agg["oracle_rate"] - rate
    if abs(agg["oracle_gap"] - gap) > TOL:
        problems.append(
            f"FAIL arithmetic [{arm}]: oracle_gap {agg['oracle_gap']} != "
            f"oracle_rate - rate ({gap:.6f})"
        )
    if arm in PIPELINE_ARMS and agg["false_outreach"] != 0:
        problems.append(
            f"FAIL arithmetic [{arm}]: false_outreach {agg['false_outreach']} must be 0"
        )
    return problems


def check_readme(report: dict, readme_text: str) -> list[str]:
    """Every arm in evaluation.json must have a README row whose numbers
    match (4-decimal rendering). Missing block/rows/values fail loudly."""
    problems: list[str] = []
    start = readme_text.find(EVAL_START)
    end = readme_text.find(EVAL_END)
    if start == -1 or end == -1 or end < start:
        return [f"FAIL readme: no {EVAL_START} ... {EVAL_END} block in README.md"]
    block = readme_text[start + len(EVAL_START) : end]
    rows: dict[str, dict[str, float]] = {}
    for line in block.splitlines():
        match = _ROW_RE.match(line.strip())
        if match:
            rows[match.group("arm")] = {
                "attempts": float(match.group("attempts")),
                "recovered": float(match.group("recovered")),
                "rate": float(match.group("rate")),
                "lo": float(match.group("lo")),
                "hi": float(match.group("hi")),
                "gap": float(match.group("gap")),
            }
    for arm, agg in report["arms"].items():
        if arm not in rows:
            problems.append(
                f"FAIL readme: evaluation.json arm {arm!r} has no numbers row in the "
                f"{EVAL_START} block"
            )
            continue
        row = rows[arm]
        expected = {
            "attempts": float(agg["attempts"]),
            "recovered": float(agg["recovered"]),
            "rate": float(agg["recovery_rate"]),
            "lo": float(agg["ci95_wilson_lo"]),
            "hi": float(agg["ci95_wilson_hi"]),
            "gap": float(agg["oracle_gap"]),
        }
        for key, want in expected.items():
            got = row[key]
            if got != want and abs(got - want) > README_TOL:
                problems.append(
                    f"FAIL readme: {arm}.{key} drifted: README has {got}, "
                    f"evaluation.json has {want}"
                )
    return problems


def verify(report: dict, readme_text: str) -> list[str]:
    """All three layers; returns the list of FAIL messages (empty = pass)."""
    problems = check_schema(report)
    if not problems:
        for arm, agg in report["arms"].items():
            problems.extend(check_arithmetic(arm, agg))
        problems.extend(check_readme(report, readme_text))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the committed evaluation numbers")
    parser.add_argument("--eval", default="results/evaluation.json")
    parser.add_argument("--readme", default="README.md")
    args = parser.parse_args()

    eval_path = Path(args.eval)
    readme_path = Path(args.readme)
    if not eval_path.exists():
        print(f"FAIL: {eval_path} does not exist — run scripts/run_evaluation.py first")
        return 1
    if not readme_path.exists():
        print(f"FAIL: {readme_path} does not exist")
        return 1

    report = json.loads(eval_path.read_text(encoding="utf-8"))
    readme_text = readme_path.read_text(encoding="utf-8")
    problems = verify(report, readme_text)

    # Layer verdicts for the PASS/FAIL lines.
    schema_ok = not check_schema(report)
    print(("PASS" if schema_ok else "FAIL") + ": schema completeness")
    if schema_ok:
        arithmetic_ok = True
        for arm, agg in report["arms"].items():
            arm_problems = check_arithmetic(arm, agg)
            arithmetic_ok = arithmetic_ok and not arm_problems
            for line in arm_problems:
                print(line)
        print(("PASS" if arithmetic_ok else "FAIL") + ": per-arm arithmetic")
        readme_problems = check_readme(report, readme_text)
        for line in readme_problems:
            print(line)
        print(("PASS" if not readme_problems else "FAIL") + ": README.md numbers block")
    else:
        for line in problems:
            print(line)
        print("FAIL: per-arm arithmetic (skipped — schema invalid)")
        print("FAIL: README.md numbers block (skipped — schema errors)")

    if problems:
        print(f"VERIFY_NUMBERS FAILED ({len(problems)} problem(s))")
        return 1
    print("VERIFY_NUMBERS PASSED: evaluation.json and README.md agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
