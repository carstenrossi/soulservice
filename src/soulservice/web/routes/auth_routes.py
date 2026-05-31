"""Auth routes: login form, magic-link request, verify, logout."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from soulservice.core.config import settings
from soulservice.core.db import app_session_factory
from soulservice.web.auth import (
    ROLE_SESSION_KEY,
    SESSION_KEY,
    consume_magic_link_token,
    create_magic_link_token,
    is_allowed_email,
)
from soulservice.web.mail import send_magic_link
from soulservice.web.templating import templates
from soulservice.web.throttle import login_throttle

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"sent": False})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, email: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    throttle_key = f"{ip}:{email.strip().lower()}"
    # Allowlisted + within rate limit: issue a one-time link. Otherwise fall
    # through to the same response so we never leak which emails are valid.
    if is_allowed_email(email) and login_throttle.allow(throttle_key):
        async with app_session_factory() as session:
            token = await create_magic_link_token(session, email)
        await send_magic_link(
            email, f"{settings.web_base_url}/auth/verify?token={token}"
        )
    return templates.TemplateResponse(request, "login.html", {"sent": True})


@router.get("/auth/verify", response_class=HTMLResponse)
async def verify_confirm(request: Request, token: str):
    # GET does NOT consume the token; an explicit POST does. This prevents email
    # clients / link prefetchers from silently burning the one-time link.
    return templates.TemplateResponse(request, "verify_confirm.html", {"token": token})


@router.post("/auth/verify")
async def verify_submit(request: Request, token: str = Form(...)):
    async with app_session_factory() as session:
        email = await consume_magic_link_token(session, token)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    request.session[SESSION_KEY] = email
    request.session[ROLE_SESSION_KEY] = settings.web_admin_roles.get(email, "viewer")
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
