"""A4 local security tests for the dashboard server.

Verifies: session token enforcement, response headers (CSP, Cache-Control,
Referrer-Policy), read-only endpoint set, no state-changing endpoints,
CORS posture, and token randomness.
"""

from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from agentview.dashboard.server import (
    HTML_CSP,
    SECURITY_HEADERS,
    create_app,
    generate_session_token,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TOKEN = "test-token-fixed-for-tests"


@pytest.fixture
def app():
    return create_app(session_token=TOKEN)


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def authed(client):
    """TestClient with token pre-applied as a query param."""
    class _Authed:
        def get(self, url, **kwargs):
            sep = "&" if "?" in url else "?"
            return client.get(f"{url}{sep}token={TOKEN}", **kwargs)
    return _Authed()


# ---------------------------------------------------------------------------
# A4.1 -- Session token: required on API endpoints
# ---------------------------------------------------------------------------

class TestSessionToken:
    def test_api_sessions_without_token_is_401(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 401

    def test_api_fixtures_without_token_is_401(self, client):
        resp = client.get("/api/fixtures")
        assert resp.status_code == 401

    def test_api_sessions_with_correct_token_is_200(self, client):
        resp = client.get(f"/api/sessions?token={TOKEN}")
        assert resp.status_code == 200

    def test_api_fixtures_with_correct_token_is_200(self, client):
        resp = client.get(f"/api/fixtures?token={TOKEN}")
        assert resp.status_code == 200

    def test_api_sessions_with_wrong_token_is_401(self, client):
        resp = client.get("/api/sessions?token=wrong_token_xyz")
        assert resp.status_code == 401

    def test_api_sessions_with_header_token(self, client):
        resp = client.get("/api/sessions", headers={"X-AgentView-Token": TOKEN})
        assert resp.status_code == 200

    def test_index_does_not_require_token(self, client):
        """The HTML index page must be accessible without a token."""
        resp = client.get("/")
        # 200 if index.html exists; 500 if not (fixture env); never 401
        assert resp.status_code in (200, 500)
        assert resp.status_code != 401

    def test_token_is_not_predictable(self):
        """Two independently generated tokens must never be equal."""
        t1 = generate_session_token()
        t2 = generate_session_token()
        assert t1 != t2

    def test_token_length_is_adequate(self):
        """Token must have at least 32 URL-safe characters (192 bits)."""
        token = generate_session_token()
        assert len(token) >= 32

    def test_token_is_url_safe(self):
        """Token must consist only of URL-safe characters."""
        import re
        token = generate_session_token()
        assert re.match(r'^[A-Za-z0-9_-]+$', token), f"Token contains unsafe chars: {token!r}"

    def test_app_stores_token_as_state(self):
        """The app exposes the session token via app.state for CLI use."""
        app = create_app(session_token="known-token")
        assert app.state.session_token == "known-token"

    def test_auto_generated_token_is_stored(self):
        """When no token is provided, the auto-generated one is accessible."""
        app = create_app()
        assert len(app.state.session_token) >= 32


# ---------------------------------------------------------------------------
# A4.2 -- Security response headers
# ---------------------------------------------------------------------------

class TestSecurityHeaders:
    def test_api_sessions_has_cache_control_no_store(self, authed):
        resp = authed.get("/api/sessions")
        assert resp.headers.get("cache-control") == "no-store"

    def test_api_sessions_has_referrer_policy_no_referrer(self, authed):
        resp = authed.get("/api/sessions")
        assert resp.headers.get("referrer-policy") == "no-referrer"

    def test_api_sessions_has_nosniff(self, authed):
        resp = authed.get("/api/sessions")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_api_fixtures_has_cache_control_no_store(self, authed):
        resp = authed.get("/api/fixtures")
        assert resp.headers.get("cache-control") == "no-store"

    def test_index_has_csp(self, client):
        resp = client.get("/")
        if resp.status_code == 200:
            csp = resp.headers.get("content-security-policy", "")
            assert "frame-ancestors 'none'" in csp
            assert "connect-src 'self'" in csp

    def test_index_has_cache_control_no_store(self, client):
        resp = client.get("/")
        if resp.status_code == 200:
            assert resp.headers.get("cache-control") == "no-store"

    def test_csp_blocks_framing(self):
        assert "frame-ancestors 'none'" in HTML_CSP

    def test_csp_restricts_scripts_to_self(self):
        assert "script-src 'self'" in HTML_CSP

    def test_csp_restricts_connect_to_self(self):
        assert "connect-src 'self'" in HTML_CSP

    def test_csp_blocks_base_uri(self):
        assert "base-uri 'none'" in HTML_CSP

    def test_csp_blocks_form_action(self):
        assert "form-action 'none'" in HTML_CSP


# ---------------------------------------------------------------------------
# A4.3 -- Read-only endpoint set: no state-changing methods
# ---------------------------------------------------------------------------

class TestReadOnly:
    def test_no_post_on_api_sessions(self, client):
        resp = client.post(f"/api/sessions?token={TOKEN}", json={})
        assert resp.status_code in (404, 405, 422)

    def test_no_put_on_api_sessions(self, client):
        resp = client.put(f"/api/sessions?token={TOKEN}", json={})
        assert resp.status_code in (404, 405)

    def test_no_delete_on_api_sessions(self, client):
        resp = client.delete(f"/api/sessions?token={TOKEN}")
        assert resp.status_code in (404, 405)

    def test_no_patch_on_api_sessions(self, client):
        resp = client.patch(f"/api/sessions?token={TOKEN}", json={})
        assert resp.status_code in (404, 405)

    def test_no_post_on_api_fixtures(self, client):
        resp = client.post(f"/api/fixtures?token={TOKEN}", json={})
        assert resp.status_code in (404, 405)

    def test_openapi_schema_disabled(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 404

    def test_docs_disabled(self, client):
        resp = client.get("/docs")
        assert resp.status_code == 404

    def test_redoc_disabled(self, client):
        resp = client.get("/redoc")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# A4.4 -- CORS: no allow-origin header emitted
# ---------------------------------------------------------------------------

class TestCORS:
    def test_no_cors_allow_origin_on_api(self, authed):
        """API responses must not emit Access-Control-Allow-Origin.
        Without this header, browsers enforce same-origin policy by default.
        """
        resp = authed.get("/api/sessions")
        assert "access-control-allow-origin" not in resp.headers

    def test_no_cors_allow_origin_on_fixtures(self, authed):
        resp = authed.get("/api/fixtures")
        assert "access-control-allow-origin" not in resp.headers

    def test_preflight_not_supported(self, client):
        """OPTIONS requests with CORS headers must not return Allow-Origin."""
        resp = client.options(
            "/api/sessions",
            headers={"Origin": "http://evil.example.com", "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in resp.headers


# ---------------------------------------------------------------------------
# A4.5 -- Token comparison is timing-safe
# ---------------------------------------------------------------------------

class TestTimingSafety:
    def test_wrong_token_does_not_leak_timing(self, client):
        """Both a wrong token and an empty token should return 401 quickly.
        We cannot measure constant-time in a unit test, but we verify that
        secrets.compare_digest is used (not ==) by checking the import.
        """
        import inspect
        from agentview.dashboard import server as srv
        source = inspect.getsource(srv)
        assert "secrets.compare_digest" in source

    def test_partial_match_token_rejected(self, client):
        """A prefix of the correct token must be rejected."""
        partial = TOKEN[:10]
        resp = client.get(f"/api/sessions?token={partial}")
        assert resp.status_code == 401
