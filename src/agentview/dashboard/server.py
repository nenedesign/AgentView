"""FastAPI app for the local-only dashboard.

Milestone 1 goal: dashboard shell renders against bundled fixture JSONL files.
Milestone 3 will swap the fixture path for a user-selected trace file.

Local security posture (per v1.5 Section 7.5):
- Bind to 127.0.0.1 only (enforced by the CLI, not this module).
- Read-only HTTP methods; no state-changing endpoints exist in v1.
- No cookies, no auth; CSRF-safe by construction.
- Response headers: Cache-Control: no-store, Referrer-Policy: no-referrer,
  and a strict CSP for the HTML shell.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
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


def _apply_security_headers(response: Response, is_html: bool = False) -> None:
    for key, value in SECURITY_HEADERS.items():
        response.headers[key] = value
    if is_html:
        response.headers["Content-Security-Policy"] = HTML_CSP


def create_app(
    fixture_name: str = "single-session-with-error.jsonl",
    group_window_seconds: float = DEFAULT_GROUP_WINDOW_SECONDS,
) -> FastAPI:
    """Build a FastAPI app bound to a fixture file.

    The CLI will later pass a user-selected trace path instead of a fixture
    name. For Milestone 1 the dashboard is driven entirely by bundled samples.
    """
    app = FastAPI(
        title="agentview dashboard",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    fixture_path = FIXTURES_DIR / fixture_name

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        html_path = STATIC_DIR / "index.html"
        if not html_path.exists():
            raise HTTPException(status_code=500, detail="Dashboard HTML missing")
        response = FileResponse(html_path, media_type="text/html")
        _apply_security_headers(response, is_html=True)
        return response

    @app.get("/api/sessions")
    def list_sessions(fixture: str | None = None) -> JSONResponse:
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
    def list_fixtures() -> JSONResponse:
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
