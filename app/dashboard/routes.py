"""Dashboard routes (D5) — server-rendered operations surface.

Read-only over the same SQLite store the pipeline writes (WAL lets the
dashboard read while webhooks land; every page opens a short-lived
connection via app.db.get_conn and never writes to episodes/ledger).
The ONLY write endpoints are the two the design law demands a human for:

- POST /dashboard/kill — the kill switch: flips settings.kill_switch on
  the RUNNING process (in-memory, effective immediately everywhere
  settings is read — human_gate.decide and the policy engine included)
  and leaves a best-effort commented note in .env so the operator sees,
  after any restart, that the kill came from the dashboard. One-way by
  design: no un-kill endpoint, because a switch you can un-flip from a
  browser mid-incident is not a safety rail.

- POST /dashboard/approvals/{id}/approve|reject — the human gate's
  decide buttons, a thin HTTP skin over app.gates.human_gate.decide
  (whose default ActionClient is the offline RecordingStub — outreach is
  logged, not delivered, exactly like every other dispatch here). Double
  decisions and stop-event races redirect back to the episode page with
  a notice; the ledger remains the sole record of what actually happened.

Pages are Jinja2, zero JS, auto-refreshed by a meta tag; every color,
shadow, radius and weight comes from the Stripe-inspired token sheet in
static/vaapsi.css.
"""

import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse

from app.core.episodes import EPISODE_STATES
from app.dashboard import metrics
from app.db import get_conn
from app.gates import human_gate
from app.policy.merchant import list_policies
from app.settings import get_settings

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_CSS_PATH = Path(__file__).parent / "static" / "vaapsi.css"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# IST rendering (design law §5: "updated HH:MM:SS IST" caption); a fixed
# offset is correct — India has no DST.
_IST = timezone(timedelta(hours=5, minutes=30))

# outcome/state → badge color class (token sheet §4); the dashboard
# only ever colors by these tokens — no ad-hoc hex in templates.
BADGE_CLASS: dict[str, str] = {
    "NEW": "badge-new",
    "DIAGNOSED": "badge-slate",
    "SCORED": "badge-purple",
    "GATED": "badge-warn",
    "SENT": "badge-success",
    "VERIFIED": "badge-success",
    "CLOSED": "badge-slate",
    "VOIDED": "badge-slate",
    "KILLED": "badge-danger",
    "DEGRADED": "badge-warn",
    "NORMAL": "badge-plain",
    "EPISODE_CREATED": "badge-slate",
    "EPISODE_DIAGNOSED": "badge-slate",
    "EPISODE_SCORED": "badge-purple",
    "EPISODE_GATED": "badge-warn",
    "EPISODE_SENT": "badge-success",
    "EPISODE_VERIFIED": "badge-success",
    "EPISODE_CLOSED": "badge-slate",
    "EPISODE_VOIDED": "badge-slate",
    "human_rejected": "badge-slate",
    "DLQ_DRAINED": "badge-warn",
}

# Timeline dot color follows the same one-accent-story palette.
DOT_CLASS: dict[str, str] = {
    "EPISODE_SENT": "dot-success",
    "EPISODE_VERIFIED": "dot-success",
    "EPISODE_GATED": "dot-warn",
    "EPISODE_SCORED": "dot-purple",
    "EPISODE_VOIDED": "dot-danger",
}


def _fmt_paise(value: Any) -> str:
    """Integer paise → '₹499.00' — integer math only, never float."""
    p = int(value or 0)
    sign = "-" if p < 0 else ""
    p = abs(p)
    return f"{sign}₹{p // 100:,}.{p % 100:02d}"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _fmt_hours(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} h"


def _fmt_ist(ts: Any) -> str:
    """ISO-8601 UTC stamp → '28 Aug 05:00:00 IST' (tnum-friendly)."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return str(ts)
    return dt.astimezone(_IST).strftime("%d %b %H:%M:%S") + " IST"


def _badge_class(value: str | None) -> str:
    return BADGE_CLASS.get(value or "", "badge-slate")


def _dot_class(outcome: str | None) -> str:
    return DOT_CLASS.get(outcome or "", "dot-slate")


templates.env.filters["paise"] = _fmt_paise
templates.env.filters["pct"] = _fmt_pct
templates.env.filters["hours"] = _fmt_hours
templates.env.filters["ist"] = _fmt_ist
templates.env.filters["badge"] = _badge_class
templates.env.filters["dot"] = _dot_class


def _policy_summary(policy_eval: Any) -> str:
    """One-line policy_eval digest for a timeline node."""
    if not isinstance(policy_eval, dict):
        return "—"
    parts: list[str] = []
    if policy_eval.get("decision"):
        parts.append(str(policy_eval["decision"]))
    if policy_eval.get("from_state") or policy_eval.get("to_state"):
        parts.append(
            f"{policy_eval.get('from_state') or '·'} → {policy_eval.get('to_state') or '·'}"
        )
    if policy_eval.get("reason"):
        parts.append(str(policy_eval["reason"]))
    return " · ".join(parts) if parts else "—"


templates.env.filters["policy_summary"] = _policy_summary


def engine_mode(conn) -> str:
    """NORMAL | DEGRADED | KILLED for the mode banner.

    The live in-memory kill switch wins (while it is set the engine is
    KILLED even if the newest ledger row said NORMAL); otherwise the
    engine's latest stamped mode speaks — DEGRADED ledger rows are
    exactly how an LLM outage announces itself.
    """
    if get_settings().kill_switch:
        return "KILLED"
    row = conn.execute(
        "SELECT mode FROM audit_ledger ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if row is not None and row["mode"] in ("DEGRADED", "KILLED"):
        return str(row["mode"])
    return "NORMAL"


def _base_ctx(conn, nav: str, **extra: Any) -> dict[str, Any]:
    return {
        "mode": engine_mode(conn),
        "updated_ist": datetime.now(_IST).strftime("%H:%M:%S"),
        "nav": nav,
        **extra,
    }


def _episode_rows(
    conn, state: str | None, cohort: str | None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT e.*, EXISTS(SELECT 1 FROM approvals a WHERE a.episode_id = e.id "
        "AND a.status = 'PENDING') AS pending_approval FROM episodes e"
    )
    clauses: list[str] = []
    params: list[str] = []
    if state:
        clauses.append("e.state = ?")
        params.append(state)
    if cohort:
        clauses.append("e.cohort = ?")
        params.append(cohort)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY e.updated_ts_utc DESC"
    return [dict(r) for r in conn.execute(sql, params)]


def _episode_ledger(conn, episode: dict[str, Any]) -> list[dict[str, Any]]:
    """Ledger rows of THIS episode's cycle: same subscription, stamped at
    or after the episode's creation — a fresh halt after a terminal cycle
    opens a new episode, and its timeline must not borrow the old cycle's
    rows."""
    rows = conn.execute(
        "SELECT seq, ts_utc, subscription_id, trigger_event, policy_eval, score, "
        "human_gate, rzp_call, outcome, recovered_paise, mode FROM audit_ledger "
        "WHERE subscription_id = ? AND ts_utc >= ? ORDER BY seq ASC",
        (episode["subscription_id"], episode["created_ts_utc"]),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        try:
            row["policy_eval"] = (
                json.loads(row["policy_eval"]) if row["policy_eval"] else None
            )
        except (TypeError, json.JSONDecodeError):
            pass
        out.append(row)
    return out


def _pending_approval(conn, episode_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, episode_id, subscription_id, reason, status, created_ts_utc "
        "FROM approvals WHERE episode_id = ? AND status = 'PENDING' "
        "ORDER BY created_ts_utc DESC LIMIT 1",
        (episode_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _form_field(body: bytes, field: str) -> str:
    """First value of `field` from an urlencoded form body ('' if absent).

    python-multipart is deliberately absent (zero-dependency dashboard);
    plain HTML forms POST application/x-www-form-urlencoded, which the
    stdlib parses fine.
    """
    try:
        pairs = urllib.parse.parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return ""
    return pairs.get(field, [""])[0]


def _redirect(url: str) -> RedirectResponse:
    # 303: after a POST the browser must follow with a clean GET.
    return RedirectResponse(url, status_code=303)


# ── Pages ──────────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def overview(request: Request) -> HTMLResponse:
    with get_conn() as conn:
        context = _base_ctx(conn, "overview")
        # Per-merchant policy: the DEFAULT row (frozen constants) plus any
        # custom rows — one card, one definition, straight from the table.
        policies = list_policies(conn)
        context.update(
            {
                "m1_treatment": metrics.recovery_rate(conn, "TREATMENT"),
                "m1_control": metrics.recovery_rate(conn, "CONTROL"),
                "recovered_paise": metrics.recovered_paise_total(conn)[0],
                "open_episodes": metrics.open_episode_count(conn),
                "cohort_counts": metrics.cohort_counts(conn),
                "recent": metrics.recent_ledger(conn, limit=12),
                "policy_default": policies["default"],
                "policy_overrides": policies["custom"],
            }
        )
        # Sparkbar geometry: 320-unit viewBox, fill width = rate × 320.
        context["treatment_bar"] = round((context["m1_treatment"][0] or 0.0) * 320, 1)
        context["control_bar"] = round((context["m1_control"][0] or 0.0) * 320, 1)
        return templates.TemplateResponse(request, "overview.html", context)


@router.get("/episodes", response_class=HTMLResponse)
def episodes(request: Request, state: str = "", cohort: str = "") -> HTMLResponse:
    state = state if state in EPISODE_STATES else ""
    cohort = cohort if cohort in ("TREATMENT", "CONTROL") else ""
    with get_conn() as conn:
        context = _base_ctx(
            conn,
            "episodes",
            episodes=_episode_rows(conn, state or None, cohort or None),
            states=EPISODE_STATES,
            f_state=state,
            f_cohort=cohort,
        )
        return templates.TemplateResponse(request, "episodes.html", context)


@router.get("/episodes/{episode_id}", response_class=HTMLResponse)
def episode_detail(request: Request, episode_id: str) -> HTMLResponse:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such episode")
        episode = dict(row)
        context = _base_ctx(
            conn,
            "episodes",
            episode=episode,
            timeline=_episode_ledger(conn, episode),
            approval=_pending_approval(conn, episode_id),
        )
        return templates.TemplateResponse(
            request, "episode_detail.html", context
        )


@router.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request) -> HTMLResponse:
    with get_conn() as conn:
        newest = conn.execute(
            "SELECT ts_utc FROM audit_ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        context = _base_ctx(
            conn,
            "metrics",
            m1_treatment=metrics.recovery_rate(conn, "TREATMENT"),
            m1_control=metrics.recovery_rate(conn, "CONTROL"),
            m2_treatment=metrics.recovered_paise_total(conn, "TREATMENT"),
            m2_control=metrics.recovered_paise_total(conn, "CONTROL"),
            m3=metrics.time_to_recover_median(conn),
            m4=metrics.outreach_efficiency(conn),
            m5=metrics.false_outreach(conn),
            ledger_count=metrics.ledger_count(conn),
            newest_ts=newest["ts_utc"] if newest is not None else None,
        )
        return templates.TemplateResponse(request, "metrics.html", context)


@router.get("/static/vaapsi.css", include_in_schema=False)
def stylesheet() -> FileResponse:
    return FileResponse(_CSS_PATH, media_type="text/css")


# ── Write endpoints (the only ones) ────────────────────────────────────


def _note_env_kill(settings) -> None:
    """Leave the operator note in .env (best-effort, idempotent).

    Commented on purpose: the in-memory flip rules the RUNNING process,
    and a restart must not silently inherit the kill — resuming is a
    deliberate act. Any I/O problem is swallowed because a note must
    never be the thing that breaks the kill response.
    """
    path = settings.env_file_path
    marker = "dashboard kill endpoint"
    try:
        if path.is_file() and marker in path.read_text(encoding="utf-8"):
            return
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"# VAAPSI_KILL_SWITCH=1  # dashboard kill endpoint fired at "
                f"{datetime.now(timezone.utc).isoformat()} — in-memory switch "
                f"active for the running process; clear this note and set "
                f"VAAPSI_KILL_SWITCH explicitly to resume NORMAL\n"
            )
    except OSError:
        pass


@router.post("/kill")
async def kill(request: Request) -> RedirectResponse:
    confirm = _form_field(await request.body(), "confirm").strip().upper()
    if confirm != "KILL":
        # Wrong confirmation text → the switch is untouched; the overview
        # page (with its notice) is the error UI.
        return _redirect("/dashboard?notice=Kill+switch+untouched+-+type+KILL+to+confirm")
    settings = get_settings()
    settings.kill_switch = True  # in-memory: rules THIS process immediately
    _note_env_kill(settings)
    return _redirect("/dashboard?notice=Kill+switch+ACTIVE+-+all+outbound+actions+refused")


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str) -> RedirectResponse:
    return _decide_and_redirect(approval_id, approved=True)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str) -> RedirectResponse:
    return _decide_and_redirect(approval_id, approved=False)


def _decide_and_redirect(approval_id: str, *, approved: bool) -> RedirectResponse:
    """The decide skin: human_gate.decide on a per-request connection.

    decide() commits or rolls back the dispatch + ledger row + approval
    stamp on that one connection (house atomicity). Errors become
    redirects with a notice rather than JSON errors — the episode page
    IS the error UI, and the ledger (not the browser) is the record.
    """
    try:
        with get_conn() as conn:
            episode_id = str(
                human_gate.get_approval(conn, approval_id)["episode_id"]
            )
    except human_gate.ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="no such approval") from None

    try:
        with get_conn() as conn:
            result = human_gate.decide(conn, approval_id, approved=approved)
    except human_gate.ApprovalError as exc:
        # DoubleDecisionError (already decided), a stop-event race, or the
        # kill switch outranking the approval — all redirect; the approval
        # panel and episode state on the page show the truth.
        notice = urllib.parse.quote(str(exc))
        return _redirect(f"/dashboard/episodes/{episode_id}?notice={notice}")

    notice = urllib.parse.quote(
        f"Decision recorded: {result['status']} — episode now "
        f"{result['episode_state_after']}"
    )
    return _redirect(f"/dashboard/episodes/{episode_id}?notice={notice}")
