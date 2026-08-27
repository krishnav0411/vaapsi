"""D3 LLM layer — provider-agnostic, schema-validated, injection-hardened.

app.llm answers one question: what outreach should this episode get? It is
deliberately a SEAM, not a dependency — the deterministic scorer
(app.scoring) and the policy engine remain the safety envelope, and the
caller (D3 orchestrator) falls back to rules-only on ANY LLM failure.
app.llm.base holds the contract (Protocol, typed recommendation, code-
enforced allowlists); app.llm.openai_compat holds the OpenAI-compatible
chat-completions adapter with injection-hardened prompt construction.
"""

from app.llm.base import (
    ACTION_ALLOWLIST,
    CHANNEL_ALLOWLIST,
    MESSAGE_VARIANT_ALLOWLIST,
    LLMClient,
    LLMError,
    LLMInvalidOutput,
    LLMRecommendation,
    LLMUnavailable,
    validate_recommendation,
)
from app.llm.openai_compat import (
    DEFAULT_TIMEOUT_SECONDS,
    OpenAICompatibleClient,
    parse_model_output,
    strip_code_fences,
)

__all__ = [
    "ACTION_ALLOWLIST",
    "CHANNEL_ALLOWLIST",
    "DEFAULT_TIMEOUT_SECONDS",
    "MESSAGE_VARIANT_ALLOWLIST",
    "LLMClient",
    "LLMError",
    "LLMInvalidOutput",
    "LLMRecommendation",
    "LLMUnavailable",
    "OpenAICompatibleClient",
    "parse_model_output",
    "strip_code_fences",
    "validate_recommendation",
]
