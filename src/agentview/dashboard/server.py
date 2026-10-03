"""FastAPI app for the local-only dashboard.

Local security posture (v1.5 Section 7.5):
- Bind to 127.0.0.1 only -- enforced by the CLI; not this module.
- Session token: cryptographically random 32-byte URL-safe token required on
  all API requests. The CLI prints the startup URL (with token) to stderr only.
  The token is never logged and never appears in a predictable URL.
- Read-only HTTP: no state-changing endpoints exist.
- CORS: no CORS headers emitted; browser same-origin policy blocks cross-origin
  fetch from any other origin. (Adding Allow-Origin would widen the attack surface.)
- CSP: no inline scripts, no framing (frame-ancestors 'none'), connect-src self.
- Cache-Control: no-store on all API responses and the HTML shell.
- Referrer-Policy: no-referrer.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .api import DEFAULT_GROUP_WINDOW_SECONDS, reconstruct_sessions

STATIC_DIR = Path(__file__).parent / "static"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

HTML_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def _apply_security_headers(response: Response, is_html: bool = False) -> None:
    for key, value in SECURITY_HEADERS.items():
        response.headers[key] = value
    if is_html:
        response.headers["Content-Security-Policy"] = HTML_CSP


def create_app(
    fixture_name: str = "single-session-with-error.jsonl",
    group_window_seconds: float = DEFAULT_GROUP_WINDOW_SECONDS,
    session_token: str | None = None,
) -> FastAPI:
    """Build a FastAPI app bound to a fixture file.

    Parameters
    ----------
    session_token:
        Cryptographically random token required on all /api/* requests.
        If None, a token is generated automatically.
        The CLI prints the startup URL containing this token to stderr.
    """
    token = session_token if session_token is not None else generate_session_token()

    app = FastAPI(
        title="agentview dashboard",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Expose the token as an app-level attribute for testing and CLI startup message
    app.state.session_token = token

    fixture_path = FIXTURES_DIR / fixture_name

    def _require_token(request: Request) -> None:
        provided = request.query_params.get("token") or request.headers.get("X-AgentView-Token")
        if not provided or not secrets.compare_digest(provided, token):
            raise HTTPException(status_code=401, detail="Missing or invalid session token")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        html_path = STATIC_DIR / "index.html"
        if not html_path.exists():
            raise HTTPException(status_code=500, detail="Dashboard HTML missing")
        response = FileResponse(html_path, media_type="text/html")
        _apply_security_headers(response, is_html=True)
        return response

    @app.get("/api/sessions")
    def list_sessions(request: Request, fixture: str | None = None) -> JSONResponse:
        _require_token(request)
        target = FIXTURES_DIR / fixture if fixture else fixture_path
        if not target.exists() or FIXTURES_DIR not in target.resolve().parents:
            raise HTTPException(status_code=404, detail="Fixture not found")
        payload: dict[str, Any] = reconstruct_sessions(
            target, group_window_seconds=group_window_seconds
        )
        payload["source"] = target.name
        response = JSONResponse(payload)
        _apply_security_headers(response)
        return response

    @app.get("/api/fixtures")
    def list_fixtures(request: Request) -> JSONResponse:
        _require_token(request)
        names = sorted(p.name for p in FIXTURES_DIR.glob("*.jsonl"))
        response = JSONResponse({"fixtures": names, "default": fixture_path.name})
        _apply_security_headers(response)
        return response

    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR, check_dir=False),
        name="static",
    )

    return app


app = create_app()
