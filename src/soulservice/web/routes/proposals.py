"""Proposals inbox: list pending memories, confirm/reject via HTMX."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from soulservice.web import queries
from soulservice.web.auth import login_required, require_role
from soulservice.web.db import soul_context
from soulservice.web.templating import templates

router = APIRouter()


@router.get("/souls/{slug}/proposals", response_class=HTMLResponse)
async def proposals_page(
    request: Request, slug: str, _: str = Depends(login_required)
):
    async with soul_context(slug) as (soul, souls, session):
        items = await queries.list_pending_proposals(session, soul["id"])
    return templates.TemplateResponse(
        request,
        "proposals.html",
        {"soul": soul, "souls": souls, "items": items},
    )


@router.post(
    "/souls/{slug}/proposals/{memory_id}/decide", response_class=HTMLResponse
)
async def decide(
    request: Request,
    slug: str,
    memory_id: str,
    action: str = Form(...),
    _: str = Depends(require_role("editor")),
):
    async with soul_context(slug) as (soul, _souls, session):
        await queries.decide_proposal_web(session, soul, memory_id, action)
    return HTMLResponse("")
