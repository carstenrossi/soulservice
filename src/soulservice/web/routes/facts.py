"""Facts management: list, set, remove."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from soulservice.web import queries
from soulservice.web.auth import login_required, require_role
from soulservice.web.db import soul_context
from soulservice.web.templating import templates

router = APIRouter()


@router.get("/souls/{slug}/facts", response_class=HTMLResponse)
async def facts_page(
    request: Request,
    slug: str,
    category: str | None = Query(None),
    _: str = Depends(login_required),
):
    async with soul_context(slug) as (soul, souls, session):
        items = await queries.list_facts(session, soul["id"], category=category)
    return templates.TemplateResponse(
        request,
        "facts.html",
        {"soul": soul, "souls": souls, "items": items, "filter_category": category or ""},
    )


@router.post("/souls/{slug}/facts")
async def set_fact(
    request: Request,
    slug: str,
    category: str = Form(...),
    key: str = Form(...),
    value: str = Form(...),
    confidence: float = Form(1.0),
    _: str = Depends(require_role("editor")),
):
    async with soul_context(slug) as (soul, _souls, session):
        await queries.set_fact_web(session, soul, category, key, value, confidence)
    return RedirectResponse(url=f"/souls/{slug}/facts", status_code=303)


@router.post("/souls/{slug}/facts/remove")
async def remove_fact(
    request: Request,
    slug: str,
    category: str = Form(...),
    key: str = Form(...),
    _: str = Depends(require_role("editor")),
):
    async with soul_context(slug) as (soul, _souls, session):
        await queries.remove_fact_web(session, soul, category, key)
    return RedirectResponse(url=f"/souls/{slug}/facts", status_code=303)
