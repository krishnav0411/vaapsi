"""Public demo mode — the fail-closed read-only deployment guard (Phase D).

Why this module exists: a public demo must be explorable by anyone with the
URL, which means every write surface has to be closed BEFORE the process
accepts traffic — not documented, closed. Two halves, both fail-closed:

1. Boot guard: ``assert_demo_safe`` refuses to start a demo deployment
   that also carries real Razorpay/LLM credentials. A demo that could
   touch live keys is not a demo; it refuses to boot (RuntimeError naming
   the offending setting names — never their values).

2. Write surface: ``BLOCKED_WRITE_ROUTES`` is the exact (method,
   path-pattern) list of write routes a demo blocks with a 404. The
   default posture is the opposite of a blocklist you have to remember to
   maintain: unknown method+path pairs stay reachable (GETs especially —
   the whole point is a read-only demo), and ONLY these exact write
   routes are refused. In demo mode the ingest router is additionally
   never mounted (see app/main.py), and the ingest paths appear here too
   so the block holds even if the router is somehow present.

``is_demo_mode`` is deliberately tiny and takes its settings as an
argument: the middleware re-reads the live settings object on every
request, so a flip (kill-switch style tooling, tests) takes effect
immediately without a restart.
"""

import re
from typing import Any

# The 404 body every blocked write route returns — one string, one truth,
# and the exact wording the UI tooltip reuses.
DEMO_BLOCKED_DETAIL = "disabled in public demo"

# Settings that must be EMPTY on a public demo deployment. Names only —
# values are never read into messages, logs or responses.
DEMO_FORBIDDEN_SETTINGS: tuple[str, ...] = (
    "razorpay_key_id",
    "razorpay_key_secret",
    "razorpay_webhook_secret",
    "llm_api_key",
)

# Exact write routes blocked in public demo mode. Patterns use FastAPI
# path syntax ({param} = one segment, {param:path} = rest of the URL).
# JSON API writes, the Jinja dashboard's human actions, and the whole
# ingest surface — nothing else is blocked, so every GET stays live.
BLOCKED_WRITE_ROUTES: tuple[tuple[str, str], ...] = (
    ("PUT", "/api/policy/{merchant_id}"),
    ("POST", "/api/kill"),
    ("POST", "/api/approvals/{approval_id}/decide"),
    ("POST", "/api/ledger/tamper-demo"),
    ("POST", "/api/drills/{drill_id}/run"),
    ("POST", "/dashboard/kill"),
    ("POST", "/dashboard/approvals/{approval_id}/approve"),
    ("POST", "/dashboard/approvals/{approval_id}/reject"),
    # The ingest surface is write-by-definition; app.main skips mounting
    # it entirely on demo boots — these entries make the block hold even
    # if the router were mounted anyway (defense in depth, fail-closed).
    ("POST", "/webhooks/razorpay"),
    ("POST", "/webhooks/razorpay/{tail:path}"),
    # The root tolerance route (registered against the bare tunnel domain)
    # is a webhook receiver too — same rule.
    ("POST", "/"),
)

_PARAM_RE = re.compile(r"\{(\w+)(?::path)?\}")


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    """FastAPI path pattern → anchored regex ({p} → [^/]+, {p:path} → .+)."""

    def _sub(match: re.Match[str]) -> str:
        return ".+" if match.group(0).endswith(":path}") else "[^/]+"

    return re.compile("^" + _PARAM_RE.sub(_sub, pattern) + "$")


_COMPILED_BLOCKED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, _compile_pattern(pattern)) for method, pattern in BLOCKED_WRITE_ROUTES
)


def is_demo_mode(settings: Any) -> bool:
    """True when this deployment runs as a public read-only demo."""
    return bool(getattr(settings, "public_demo", False))


def is_demo_blocked(method: str, path: str) -> bool:
    """True for the EXACT write routes a demo refuses (404).

    Method and path must both match a BLOCKED_WRITE_ROUTES entry; anything
    unknown — every GET, every read path, misspelled paths — is NOT blocked
    (fail-closed means the demo refuses writes, not that it hides reads).
    """
    return any(
        matched_method == method.upper() and pattern.match(path)
        for matched_method, pattern in _COMPILED_BLOCKED
    )


def assert_demo_safe(settings: Any) -> None:
    """Refuse to boot a public demo that carries real credentials.

    Called from app.main's lifespan before anything else runs. When
    VAAPSI_PUBLIC_DEMO is on and ANY of DEMO_FORBIDDEN_SETTINGS is
    non-empty, this raises RuntimeError naming the offending setting
    names (never values) — a demo deployment with live keys must not
    start at all, because a read-only UI cannot make key use safe.
    No-op when demo mode is off: normal deployments keep their secrets.
    """
    if not is_demo_mode(settings):
        return
    offenders = [
        name
        for name in DEMO_FORBIDDEN_SETTINGS
        if str(getattr(settings, name, "") or "").strip()
    ]
    if offenders:
        raise RuntimeError(
            "VAAPSI_PUBLIC_DEMO=1 refuses to start: a public demo deployment "
            "must be credential-free, but these settings are non-empty: "
            + ", ".join(offenders)
            + ". Clear them in the environment (values are never shown) or "
            "unset VAAPSI_PUBLIC_DEMO."
        )
