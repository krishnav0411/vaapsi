"""D7.5 cutover tests — FastAPI serves the React build at /app.

House fixture pattern (mirrors tests/test_dashboard.py): fresh tmp
store + kill switch off + tmp env-file path per test — nothing here
touches the real data/ store or .env. Covers the serving contract:
/app serves dist/index.html with the React root div, deep client
routes get the same file (SPA fallback), the hashed asset bundle is
reachable under /app/assets, and nothing is shadowed — /api, /dashboard
and /health keep answering. A missing build (fresh clone pre-npm)
degrades to a 503 JSON hint instead of crashing import.

The Blade design-law guard pins docs/design/RAZORPAY-BLADE-DESIGN.md
in source: the azure/canvas tokens, tabular numerals, the vendored
TASA Orbiter + Inter fonts.css and the exactly-8 woff2 set. The Jinja
fallback keeps its own D5 Stripe guard in tests/test_dashboard.py —
untouched here.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.dashboard import killswitch
from app.main import app
from app.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = REPO_ROOT / "frontend"
DIST_INDEX = FRONTEND / "dist" / "index.html"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Fresh store per test + kill switch off + tmp .env kill target."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "kill_switch", False)
    monkeypatch.setattr(s, "env_file_path", tmp_path / "env-note")
    monkeypatch.setattr(killswitch, "ENV_PATH", tmp_path / "env-note")
    with TestClient(app) as c:
        yield c


# ── Serving: /app boots the SPA ─────────────────────────────────────────


class TestServing:
    def test_app_serves_built_index_with_react_root(self, client):
        r = client.get("/app")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert 'id="root"' in r.text
        assert r.text == DIST_INDEX.read_text(encoding="utf-8")

    def test_deep_client_route_falls_back_to_index(self, client):
        r = client.get("/app/episodes")
        assert r.status_code == 200
        assert r.text == DIST_INDEX.read_text(encoding="utf-8")

    def test_deep_episode_detail_route_falls_back_too(self, client):
        r = client.get("/app/episodes/ep_some_id")
        assert r.status_code == 200
        assert 'id="root"' in r.text

    def test_trailing_slash_route_serves_index_too(self, client):
        assert client.get("/app/").status_code == 200

    def test_hashed_assets_served_under_app_assets(self, client):
        """The build's base=/app/ contract: index.html references hashed
        bundles under /app/assets/* and the mount actually serves them
        (an unbased build would reference /assets and 404 here)."""
        html = DIST_INDEX.read_text(encoding="utf-8")
        asset_match = re.search(r'src="(/app/assets/[^"]+\.js)"', html)
        assert asset_match, "built index.html must reference /app/assets/*.js"
        assert client.get(asset_match.group(1)).status_code == 200


# ── Nothing shadowed: the cutover must not disturb existing surfaces ────


class TestNothingShadowed:
    def test_api_dashboard_health_still_answer(self, client):
        assert client.get("/api/overview").status_code == 200
        assert client.get("/api/episodes").status_code == 200
        assert client.get("/api/mode").json() == {"mode": "NORMAL"}
        assert client.get("/dashboard").status_code == 200
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

    def test_unknown_api_path_is_not_swallowed_by_spa(self, client):
        # /api/* 404s must stay JSON API 404s, never SPA index.html.
        r = client.get("/api/nonexistent")
        assert r.status_code == 404
        assert "root" not in r.text


# ── Missing build: 503 hint, never a crash ──────────────────────────────


class TestMissingBuild:
    def test_missing_dist_returns_503_json_hint(self, client, monkeypatch, tmp_path):
        import app.main as main_module

        empty_dist = tmp_path / "dist"
        empty_dist.mkdir()
        monkeypatch.setattr(main_module, "FRONTEND_DIST", empty_dist)
        r = client.get("/app")
        assert r.status_code == 503
        body = r.json()
        assert "npm run build" in body["detail"]

    def test_missing_deep_route_returns_503_json_hint_too(
        self, client, monkeypatch, tmp_path
    ):
        import app.main as main_module

        empty_dist = tmp_path / "dist"
        empty_dist.mkdir()
        monkeypatch.setattr(main_module, "FRONTEND_DIST", empty_dist)
        r = client.get("/app/episodes")
        assert r.status_code == 503
        assert r.headers["content-type"].startswith("application/json")


# ── Blade design-law guard (RAZORPAY-BLADE-DESIGN.md is the law) ────────


class TestBladeDesignLaw:
    def test_index_css_carries_blade_tokens(self):
        css = (FRONTEND / "src" / "index.css").read_text(encoding="utf-8")
        assert "#1364f1" in css  # azure primary
        assert "#f7f7f7" in css  # canvas
        assert "tabular-nums" in css  # money numerals align in tables

    def test_fonts_css_declares_tasa_orbiter_and_inter(self):
        css = (FRONTEND / "public" / "fonts" / "fonts.css").read_text(encoding="utf-8")
        assert "TASA Orbiter" in css  # display font (no CDN substitution)
        assert "Inter" in css  # UI font

    def test_exactly_eight_vendored_woff2(self):
        fonts = sorted((FRONTEND / "public" / "fonts").rglob("*.woff2"))
        assert len(fonts) == 8
        assert all(f.suffix == ".woff2" for f in fonts)
