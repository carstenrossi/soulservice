"""API token management: list, create, revoke."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from soulservice.core.auth import VALID_MODES
from soulservice.web import queries
from soulservice.web.auth import login_required, require_role
from soulservice.web.db import soul_context
from soulservice.web.templating import templates

router = APIRouter()


@router.get("/souls/{slug}/tokens", response_class=HTMLResponse)
async def tokens_page(
    request: Request,
    slug: str,
    _: str = Depends(login_required),
):
    new_token = request.session.pop("flash_new_token", None)
    async with soul_context(slug) as (soul, souls, session):
        items = await queries.list_tokens(session, soul["id"])
    return templates.TemplateResponse(
        request,
        "tokens.html",
        {
            "soul": soul,
            "souls": souls,
            "items": items,
            "modes": VALID_MODES,
            "new_token": new_token,
        },
    )


@router.post("/souls/{slug}/tokens")
async def create_token(
    request: Request,
    slug: str,
    name: str = Form(...),
    mode: str = Form("identity"),
    read_only: str | None = Form(None),
    _: str = Depends(require_role("admin")),
):
    is_read_only = read_only == "true"
    async with soul_context(slug) as (soul, _souls, session):
        full_token, _meta = await queries.create_token_web(
            session, soul, name, mode=mode, read_only=is_read_only
        )
    request.session["flash_new_token"] = full_token
    return RedirectResponse(url=f"/souls/{slug}/tokens", status_code=303)


@router.post("/souls/{slug}/tokens/{token_id}/revoke")
async def revoke_token(
    request: Request,
    slug: str,
    token_id: str,
    _: str = Depends(require_role("admin")),
):
    async with soul_context(slug) as (soul, _souls, session):
        await queries.revoke_token_web(session, soul, token_id)
    return RedirectResponse(url=f"/souls/{slug}/tokens", status_code=303)
