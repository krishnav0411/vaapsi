"""LLM seam contract — Protocol, typed recommendation, enforced allowlists.

Why an allowlist IN CODE (not in the prompt): prompts are suggestions,
code is law. A model can be talked into emitting "refund_all" by a
customer name that says "ignore previous instructions" — the only defense
that always holds is validating the parsed output against frozen sets
here, before the value can influence any action. Anything outside the
sets, missing from the schema, or unparseable raises LLMInvalidOutput and
the caller falls back to the deterministic choice (DEGRADED mode), so an
attack or a malformed response can never widen what Vaapsi may do.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Frozen vocabularies — enforcement lives in validate_recommendation(), in
# code, never only in prompt text.
ACTION_ALLOWLIST = frozenset(
    {"send_payment_link", "send_registration_link", "send_invoice_nudge"}
)
CHANNEL_ALLOWLIST = frozenset({"payment_link", "upi_intent", "email"})
MESSAGE_VARIANT_ALLOWLIST = frozenset({"gentle", "standard", "firm"})

# The exact output schema: a single JSON object with exactly these keys.
REQUIRED_KEYS: tuple[str, ...] = ("action", "channel", "message_variant")


class LLMError(RuntimeError):
    """Base for every LLM failure — callers catch this to fall back."""


class LLMUnavailable(LLMError):
    """No usable LLM: key unset, endpoint refused, or 5xx/timeout past retry."""


class LLMInvalidOutput(LLMError):
    """Parsed output violated the schema or an allowlist (fallback trigger)."""


@runtime_checkable
class LLMClient(Protocol):
    """Anything that can turn an episode payload into a validated decision.

    Implementations (OpenAICompatibleClient, test fakes, the D3 demo's
    FakeLLM) return a dict with keys action / channel / message_variant /
    raw — the validated recommendation plus the raw model output kept for
    the ledger's llm_output_raw evidence.
    """

    def recommend(self, payload: dict) -> dict: ...


@dataclass
class LLMRecommendation:
    """Typed view of a validated LLM recommendation; `raw` is audit evidence."""

    action: str
    channel: str
    message_variant: str
    raw: dict


def validate_recommendation(raw: object) -> LLMRecommendation:
    """Enforce schema + allowlists against parsed model output.

    `raw` is whatever json.loads produced from the model's text. Strict
    schema: a dict with exactly action / channel / message_variant, all
    strings, each inside its allowlist. ANY violation — wrong type,
    missing key, extra key, out-of-allowlist value — raises
    LLMInvalidOutput. This function is the single enforcement point: the
    prompt asks for the schema, but only this code decides what passes.
    """
    if not isinstance(raw, dict):
        raise LLMInvalidOutput(
            f"model output must be a JSON object, got {type(raw).__name__}"
        )
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise LLMInvalidOutput(f"model output missing required keys: {missing}")
    unknown = sorted(set(raw) - set(REQUIRED_KEYS))
    if unknown:
        raise LLMInvalidOutput(f"model output has keys outside the schema: {unknown}")
    values: dict[str, str] = {}
    for key, allowlist in (
        ("action", ACTION_ALLOWLIST),
        ("channel", CHANNEL_ALLOWLIST),
        ("message_variant", MESSAGE_VARIANT_ALLOWLIST),
    ):
        value = raw[key]
        if not isinstance(value, str) or value not in allowlist:
            raise LLMInvalidOutput(
                f"{key}={value!r} is not in the allowlist {sorted(allowlist)}"
            )
        values[key] = value
    return LLMRecommendation(raw=dict(raw), **values)
