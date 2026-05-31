"""FastAPI app factory for the local admin web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from soulservice.core.config import settings
from soulservice.web.auth import NotAuthenticatedError, NotAuthorizedError

_BASE = Path(__file__).parent


def create_app() -> FastAPI:
    if not settings.web_session_secret:
        msg = (
            "WEB_SESSION_SECRET is not set. The admin UI refuses to start without "
            "a strong session secret (cookies would otherwise be forgeable). "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
        raise RuntimeError(msg)

    app = FastAPI(title="Soulservice Admin", docs_url=None, redoc_url=None)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.web_session_secret,
        https_only=settings.web_secure_cookies,
        same_site="lax",
        session_cookie="soulservice_admin",
    )
    app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")

    @app.exception_handler(NotAuthenticatedError)
    async def _redirect_to_login(request: Request, exc: NotAuthenticatedError):
        return RedirectResponse(url="/login", status_code=303)

    @app.exception_handler(NotAuthorizedError)
    async def _forbidden(request: Request, exc: NotAuthorizedError):
        return HTMLResponse(
            "<h1>403 Forbidden</h1><p>Your role does not permit this action.</p>",
            status_code=403,
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    from soulservice.web.routes import (
        audit,
        auth_routes,
        dashboard,
        facts,
        memories,
        portability,
        properties,
        proposals,
        self_core,
        tokens,
    )

    for module in (
        auth_routes,
        dashboard,
        proposals,
        memories,
        facts,
        properties,
        self_core,
        tokens,
        audit,
        portability,
    ):
        app.include_router(module.router)
    return app
