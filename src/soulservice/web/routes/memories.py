"""Memory browser: list, semantic search, detail, forget."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from soulservice.web import queries
from soulservice.web.auth import login_required, require_role
from soulservice.web.db import soul_context
from soulservice.web.templating import templates

router = APIRouter()

_PAGE_SIZE = 50


@router.get("/souls/{slug}/memories", response_class=HTMLResponse)
async def memories_page(
    request: Request,
    slug: str,
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    _: str = Depends(login_required),
):
    offset = (page - 1) * _PAGE_SIZE
    async with soul_context(slug) as (soul, souls, session):
        if q:
            items = await queries.search_memories(session, soul["id"], q)
            has_next = False
        else:
            items = await queries.list_memories(
                session, soul["id"], limit=_PAGE_SIZE, offset=offset
            )
            has_next = len(items) == _PAGE_SIZE
    return templates.TemplateResponse(
        request,
        "memories.html",
        {
            "soul": soul,
            "souls": souls,
            "items": items,
            "query": q or "",
            "page": page,
            "has_next": has_next,
        },
    )


@router.get("/souls/{slug}/memories/{memory_id}", response_class=HTMLResponse)
async def memory_detail(
    request: Request,
    slug: str,
    memory_id: str,
    _: str = Depends(login_required),
):
    async with soul_context(slug) as (soul, souls, session):
        memory = await queries.get_memory(session, soul["id"], memory_id)
    if memory is None:
        return RedirectResponse(url=f"/souls/{slug}/memories", status_code=303)
    return templates.TemplateResponse(
        request,
        "memory_detail.html",
        {"soul": soul, "souls": souls, "memory": memory},
    )


@router.post("/souls/{slug}/memories/{memory_id}/forget")
async def forget_memory(
    request: Request,
    slug: str,
    memory_id: str,
    _: str = Depends(require_role("editor")),
):
    async with soul_context(slug) as (soul, _souls, session):
        await queries.forget_memory_web(session, soul, memory_id)
    return RedirectResponse(url=f"/souls/{slug}/memories", status_code=303)
