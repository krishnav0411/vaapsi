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
        assert client.get("/api/mode").json() == {"mode": "NORMAL", "demo": False}
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


# ── Blade dark-mode design law (shell v2: the dist is the contract) ─────


class TestBladeDarkModeLaw:
    """The BUILT bundle under frontend/dist must carry the shell-v2 dark
    flip: a .dark selector that redefines the token block, the azure
    primary present in BOTH themes (light #1364f1, dark lifted step),
    and light-mode token values byte-identical to the frozen law — the
    dark flip must be strictly additive, so three sampled light tokens
    are pinned by their exact hexes in the shipped CSS."""

    @staticmethod
    def built_css() -> str:
        assets = FRONTEND / "dist" / "assets"
        css_files = sorted(assets.glob("*.css"))
        assert css_files, "npm run build must emit hashed css under frontend/dist/assets"
        return "\n".join(f.read_text(encoding="utf-8") for f in css_files)

    def test_built_css_declares_dark_selector(self):
        css = self.built_css()
        assert ".dark" in css
        # The dark selector must actually flip tokens, not merely exist.
        normalized = re.sub(r"\s+", "", css).lower()
        assert ".dark{" in normalized
        assert "--color-canvas:#0b0e14" in normalized

    def test_built_css_carries_azure_primary_in_both_themes(self):
        css = self.built_css().lower()
        assert "#1364f1" in css  # light theme azure (azure.500)
        assert "#6ea3f7" in css  # dark theme lifted azure (same family)

    def test_light_mode_tokens_byte_identical(self):
        normalized = re.sub(r"\s+", "", self.built_css()).lower()
        for token, frozen in (
            ("--color-primary", "#1364f1"),
            ("--color-primary-hover", "#0e54cd"),
            ("--color-primary-tint", "#eaf1fe"),
        ):
            assert f"{token}:{frozen}" in normalized, (
                f"light-mode {token} must stay byte-identical to {frozen}"
            )


# ── D8 surfaces shipped in the dist (ledger / drills / approvals) ────────


class TestD8SurfacesShipped:
    """The D8 React surfaces must exist in the COMMITTED build: the three
    routes registered in the bundle, the tamper-demo copy shipped (the
    video-climax feature is really in the build), and the dark-law tests
    above stay untouched and passing alongside."""

    @staticmethod
    def built_js() -> str:
        assets = FRONTEND / "dist" / "assets"
        js_files = sorted(assets.glob("*.js"))
        assert js_files, "npm run build must emit hashed js under frontend/dist/assets"
        return "\n".join(f.read_text(encoding="utf-8") for f in js_files)

    def test_dist_registers_new_routes(self):
        js = self.built_js()
        for route in ("/ledger", "/drills", "/approvals"):
            pattern = rf"path:\s*['\"]{route}['\"]"
            assert re.search(pattern, js), f"built JS must register the {route} route"

    def test_dist_js_contains_tamper_demo_copy(self):
        assert "Prove it: tamper demo" in self.built_js()

    def test_dist_js_contains_approvals_empty_state_copy(self):
        assert "Nothing awaiting judgment" in self.built_js()

    def test_dist_js_contains_app_ready_marker(self):
        """The app shell carries a data-testid="app-ready" on its root
        div (an ops smoke-test hook). dist is a JS bundle, so the marker
        surfaces as the string literal "app-ready" in the built bundle."""
        assert "app-ready" in self.built_js()

    def test_dist_js_contains_public_demo_chip_copy(self):
        """The Phase D public-demo chip ships in the bundle: when
        /api/mode reports demo:true the shell badges itself "PUBLIC DEMO
        — read only" (the string literal is the contract in a built bundle)."""
        assert "PUBLIC DEMO — read only" in self.built_js()
