"""Vaapsi FastAPI entrypoint.

Day 0: /health + webhook receiver. The state machine, policy engine,
actions, audit ledger and dashboard land on D2–D5 per PLAN.md §8.
D7.5 cutover: the built React app is served at /app; the Jinja
dashboard stays fallback at /dashboard.
"""

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.dashboard.api import api_router
from app.dashboard.routes import router as dashboard_router
from app.db import connect, get_conn, init_db
from app.ingest.receiver import root_webhook_handler
from app.ingest.receiver import router as ingest_router
from app.policy.merchant import ensure_default_row
from app.settings import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    conn = connect()
    try:
        init_db(conn)
        # Per-merchant policy table: the DEFAULT row (frozen constants) must
        # exist before the engine's first read. Idempotent; get_policy also
        # self-heals on cache miss, so a wiped row never wedges the engine.
        ensure_default_row(conn)
        conn.commit()
    finally:
        conn.close()
    yield
    # (shutdown hooks for later days: drain DLQ, close ledger cleanly)


app = FastAPI(
    title="Vaapsi",
    version="0.1.0",
    description=(
        "Bounded, auditable recovery agent for failed Razorpay subscriptions "
        "(test mode). Deterministic-first: policy engine and scorecard are "
        "pure rules; the LLM only narrates diagnosis and proposes a "
        "schema-validated action inside hard bounds."
    ),
    lifespan=lifespan,
)

app.include_router(ingest_router)
# D5 operations dashboard: server-rendered Jinja2, read-only over the same
# SQLite store (its only write endpoints are the kill switch and the
# human-gate decide buttons).
app.include_router(dashboard_router)
# D7.1 JSON API for the React dashboard — mounted EXACTLY ONCE (a double
# mount shadows routes and 500s path-param lookups; one router, one mount).
app.include_router(api_router)
# Tolerance route (see receiver.root_webhook_handler): accepts Razorpay
# deliveries that were registered against the bare tunnel domain.
app.add_api_route("/", root_webhook_handler, methods=["POST"], include_in_schema=False)

# ── D7.5 cutover: FastAPI serves the built React app at /app ────────────
# Resolved at import so a missing build can never crash startup: when
# frontend/dist is absent (fresh clone, pre-`npm run build`), the SPA
# route below degrades to a 503 JSON hint instead of 500ing.
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"

# Hashed Vite output (base /app/). Mounted only when present —
# StaticFiles(check_dir) would otherwise raise at startup. Registered
# BEFORE the SPA fallback so /app/assets/* hits the mount, not index.html.
_DIST_ASSETS = FRONTEND_DIST / "assets"
if _DIST_ASSETS.is_dir():
    app.mount("/app/assets", StaticFiles(directory=_DIST_ASSETS), name="spa_assets")


@app.get("/app", include_in_schema=False)
@app.get("/app/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str = "") -> Response:
    """Every client route under /app boots the same index.html.

    React Router (basename /app) owns the path from there, so deep links
    like /app/episodes/ep_x render correctly. This cannot shadow /api/*,
    /dashboard*, /health, ingest webhooks or the root fallback: the route
    literal is /app-prefixed and every protected router registers before
    it. A missing build answers 503 JSON with the build hint.
    """
    index_html = FRONTEND_DIST / "index.html"
    if index_html.is_file():
        return FileResponse(index_html, media_type="text/html")
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "React dashboard build missing (frontend/dist). "
                "Run: cd frontend && npm install && npm run build"
            )
        },
    )


@app.get("/health", tags=["ops"])
def health() -> dict:
    settings = get_settings()
    db_status = "ok"
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
    except sqlite3.Error:  # pragma: no cover - health must never raise
        db_status = "error"
    return {
        "app": "vaapsi",
        "env": settings.app_env,
        "status": "ok" if db_status == "ok" else "degraded",
        "db": db_status,
        "webhook_secret_set": bool(settings.razorpay_webhook_secret),
    }
