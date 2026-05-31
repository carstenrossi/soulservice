"""Audit log viewer with filters."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from soulservice.web import queries
from soulservice.web.auth import login_required
from soulservice.web.db import soul_context
from soulservice.web.templating import templates

router = APIRouter()

_PAGE_SIZE = 100


@router.get("/souls/{slug}/audit", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    slug: str,
    tool: str | None = Query(None),
    days: int = Query(7),
    page: int = Query(1, ge=1),
    _: str = Depends(login_required),
):
    offset = (page - 1) * _PAGE_SIZE
    async with soul_context(slug) as (soul, souls, session):
        items = await queries.list_audit(
            session, soul["id"], tool_name=tool, days=days,
            limit=_PAGE_SIZE, offset=offset,
        )
    has_next = len(items) == _PAGE_SIZE
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "soul": soul,
            "souls": souls,
            "items": items,
            "filter_tool": tool or "",
            "filter_days": days,
            "page": page,
            "has_next": has_next,
        },
    )
