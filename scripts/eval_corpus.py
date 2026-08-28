"""Deterministic evaluation corpus for the offline decision-pipeline evaluation.

build_corpus(seed, n_cases) returns n_cases synthetic halted-subscription
cases across 16 families, weighted to total n_cases. Everything is derived
from random.Random(seed) — no wall clock, no I/O — so the same seed always
rebuilds a byte-identical corpus and a different seed always differs.

THE OUTCOME MODEL (shared with scripts/run_evaluation.py, restated here so
this file is self-explaining):

  The evaluation never touches real subscriptions, Razorpay, or an LLM.
  Whether a case "recovers" is a documented accounting fiction, drawn
  deterministically:

    effective_p = latent_recovery_p * action_fit

    - latent_recovery_p: per-family probability that the money is still
      recoverable at all (see _FAMILY_SPECS; e.g. insufficient_funds 0.55,
      transient_gateway 0.45, auth_failure 0.5, network_timeout 0.6,
      mandate_revoked 0.05, already_charged 0.0, control_cohort 0.45).
    - action_fit: 1.0 if the arm's taken action matches the case's
      best-action class (derived from the failure category via
      app.actions.classifier, with documented family-world overrides),
      0.3 if it acts with a wrong-category action, 0.0 if it is blocked,
      gated, skipped, or the case is must_not_contact or cohort CONTROL.
      For the no_agent arm (pipeline not run) the platform's undifferentiated
      dunning counts as a wrong-category action (fit 0.3), except for
      platform_retry-best cases where standing back is exactly right (1.0);
      cohort is irrelevant there because nobody from Vaapsi acts.
    - Draw: int(blake2b(f"{case_id}:{arm}", digest_size=8).hexdigest(), 16)
      / 2**64 < effective_p. RECOVERY means "the episode would have been
      paid" — an accounting fiction, clearly labeled as such everywhere it
      is reported. No money moved; nothing was contacted.

  The oracle arm re-draws with the per-case optimal action (fit 1.0 unless
  the case's best action is 'none' or the cohort is CONTROL, whose holdout
  nature zero-fits every agent arm by design); oracle_gap = oracle_rate -
  arm rate is the headroom each arm left on the table.

Families are allocated by largest-remainder on the fixed weight table
(control_cohort weight 30 of 105 total ≈ 30% CONTROL cohort at any n), so
all 16 families appear for n_cases >= 32 and the CONTROL fraction stays
near 30% for every n. See scripts/run_evaluation.py for the arms and
aggregates; tests/test_evaluation.py pins the determinism invariants.
"""

import hashlib
import random

# ── Family weight table (largest-remainder allocation) ──────────────────
# control_cohort carries 30 of 105 total weight ≈ the ~30% CONTROL cohort
# the experiment design (EXPERIMENT.md) fixes; the remaining 15 families
# share 75 weight across failure modes, races, caps and adversarial inputs.

FAMILY_ORDER: tuple[str, ...] = (
    "already_charged",
    "retry_active",
    "auth_failure",
    "insufficient_funds",
    "transient_gateway",
    "network_timeout",
    "mandate_revoked",
    "duplicate_dispatch",
    "stale_capture_race",
    "adversarial_name_injection",
    "quiet_hours_boundary",
    "cap_overflow",
    "kill_switch_midflight",
    "control_cohort",
    "young_subscription",
    "aged_subscription",
)

_FAMILY_SPECS: dict[str, dict] = {
    # weight: relative share (sums to 105 with control's 30)
    # latent: documented per-family recovery-probability table (see module
    #         docstring); drawn as world-truth, not per-case randomness.
    # codes:  last_error_code pool the corpus rng picks from.
    # cf:     consecutive_failures (== payment.failed events seeded per case).
    # age:    (min, max) day range for subscription age.
    # auth:   auth_attempts on the provider payload.
    # fence:  provider status the fence client reports (the world state the
    #         look-before-leap guard sees).
    "already_charged": {
        "weight": 4, "latent": 0.0, "codes": ["GATEWAY_ERROR"], "cf": 1,
        "age": (30, 400), "auth": 4, "fence": "resumed",
    },
    "retry_active": {
        "weight": 5, "latent": 0.60, "codes": ["INSUFFICIENT_FUNDS"], "cf": 2,
        "age": (30, 200), "auth": 1, "fence": "halted",
    },
    "auth_failure": {
        "weight": 7, "latent": 0.50,
        "codes": ["authentication_required", "otp_expired"], "cf": 1,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "insufficient_funds": {
        "weight": 8, "latent": 0.55, "codes": ["INSUFFICIENT_FUNDS"], "cf": 2,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "transient_gateway": {
        "weight": 8, "latent": 0.45, "codes": ["GATEWAY_ERROR"], "cf": 1,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "network_timeout": {
        "weight": 6, "latent": 0.60, "codes": ["NETWORK_ERROR", "timeout"], "cf": 1,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "mandate_revoked": {
        # mandate_revoked-after-cancel: the mandate is gone AND the
        # subscription is cancelled — outreach would be wrong (must_not_contact).
        "weight": 4, "latent": 0.05, "codes": ["MANDATE_REVOKED"], "cf": 1,
        "age": (30, 400), "auth": 4, "fence": "cancelled",
    },
    "duplicate_dispatch": {
        # episode already dispatched once (state SENT): a second dispatch
        # would be a wrong duplicate — the one-cycle-per-halt bound skips it.
        "weight": 4, "latent": 0.0, "codes": ["GATEWAY_ERROR"], "cf": 1,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "stale_capture_race": {
        # the world moves mid-cycle (capture/resume races the pipeline):
        # the stale-inference fence must discard, not dispatch.
        "weight": 5, "latent": 0.50, "codes": ["INSUFFICIENT_FUNDS"], "cf": 2,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "adversarial_name_injection": {
        # customer name / error description carry instruction-like strings;
        # the pipeline must treat them strictly as data (it does — they never
        # reach any decision surface).
        "weight": 4, "latent": 0.40, "codes": ["GATEWAY_ERROR"], "cf": 1,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "quiet_hours_boundary": {
        # halt lands inside the 21:00–09:00 IST quiet window: the clock gate
        # must block (runner freezes the engine clock at 22:30 IST for these).
        "weight": 4, "latent": 0.55, "codes": ["GATEWAY_ERROR"], "cf": 1,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "cap_overflow": {
        # attempts already at the per-episode max (3): the cap must block.
        "weight": 4, "latent": 0.50, "codes": ["INSUFFICIENT_FUNDS"], "cf": 3,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "kill_switch_midflight": {
        # operator flips the kill switch mid-flight: every pipeline arm must
        # block (the runner flips settings.kill_switch for these cases).
        "weight": 4, "latent": 0.50, "codes": ["CARD_DECLINED"], "cf": 1,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "control_cohort": {
        # the holdout: Razorpay's default retries and nothing else. Anyone's
        # outreach here would be a protocol violation (must_not_contact).
        "weight": 30, "latent": 0.45, "codes": ["GATEWAY_ERROR"], "cf": 1,
        "age": (30, 400), "auth": 4, "fence": "halted",
    },
    "young_subscription": {
        # days-old subscription: fresh customer, high latent recoverability.
        "weight": 4, "latent": 0.50, "codes": ["GATEWAY_ERROR"], "cf": 1,
        "age": (3, 30), "auth": 4, "fence": "halted",
    },
    "aged_subscription": {
        # years-old subscription, three straight failures: low latent, and
        # the escalation bar routes it to the human gate (tier 3).
        "weight": 4, "latent": 0.35, "codes": ["CARD_DECLINED"], "cf": 3,
        "age": (200, 900), "auth": 4, "fence": "halted",
    },
}

# Families where ANY outreach would be wrong (the false-outreach tripwire):
# the three wrong-to-contact world states plus the CONTROL holdout.
MUST_NOT_CONTACT_FAMILIES: frozenset[str] = frozenset(
    {"already_charged", "mandate_revoked", "duplicate_dispatch", "control_cohort"}
)

# Families whose best action is to stand back and let the platform's own
# dunning work (documented overrides on the classifier-derived class).
PLATFORM_RETRY_FAMILIES: frozenset[str] = frozenset({"retry_active", "stale_capture_race"})


def _allocate_counts(n_cases: int) -> dict[str, int]:
    """Largest-remainder allocation of n_cases over the family weights.

    Deterministic without randomness: floor-share per family, then the
    leftover units go to the largest fractional remainders, ties broken by
    FAMILY_ORDER. Guarantees the total is exactly n_cases.
    """
    total_weight = sum(spec["weight"] for spec in _FAMILY_SPECS.values())
    counts: dict[str, int] = {}
    remainders: list[tuple[float, int, str]] = []
    assigned = 0
    for position, family in enumerate(FAMILY_ORDER):
        exact = n_cases * _FAMILY_SPECS[family]["weight"] / total_weight
        counts[family] = int(exact)
        assigned += counts[family]
        remainders.append((-(exact - int(exact)), position, family))
    leftover = n_cases - assigned
    for _, _, family in sorted(remainders):
        if leftover <= 0:
            break
        counts[family] += 1
        leftover -= 1
    return counts


def build_corpus(seed: int, n_cases: int) -> list[dict]:
    """Build the deterministic corpus; see the module docstring for the
    outcome model this corpus feeds (latent_recovery_p * action_fit, drawn
    from blake2b(case_id:arm)) and _FAMILY_SPECS for the per-family latent
    table, weight shares and world states.

    Each case dict carries: case_id (sha1(family:index:seed), hex[:16] —
    stable across runs and machines), family, cohort, amount_paise
    (realistic 19900..499900, step 100), last_error_code, the
    consecutive_failures streak, age_days, auth_attempts,
    must_not_contact (True exactly for the families where any outreach
    would be wrong) and latent_recovery_p.
    """
    rng = random.Random(seed)
    counts = _allocate_counts(n_cases)
    corpus: list[dict] = []
    for family in FAMILY_ORDER:
        spec = _FAMILY_SPECS[family]
        for index in range(counts[family]):
            case_id = hashlib.sha1(f"{family}:{index}:{seed}".encode()).hexdigest()[:16]
            age_min, age_max = spec["age"]
            corpus.append(
                {
                    "case_id": case_id,
                    "family": family,
                    "cohort": "CONTROL" if family == "control_cohort" else "TREATMENT",
                    "amount_paise": rng.randrange(19900, 499901, 100),
                    "last_error_code": spec["codes"][rng.randrange(len(spec["codes"]))],
                    "consecutive_failures": spec["cf"],
                    "age_days": rng.randint(age_min, age_max),
                    "auth_attempts": spec["auth"],
                    "must_not_contact": family in MUST_NOT_CONTACT_FAMILIES,
                    "latent_recovery_p": spec["latent"],
                }
            )
    return corpus
