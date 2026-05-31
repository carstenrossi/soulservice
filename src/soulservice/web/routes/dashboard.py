"""Dashboard: overview counts and recent activity."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from soulservice.core.db import app_session_factory
from soulservice.web import queries
from soulservice.web.auth import login_required
from soulservice.web.db import soul_context
from soulservice.web.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, _: str = Depends(login_required)):
    async with app_session_factory() as bootstrap:
        souls = await queries.list_souls(bootstrap)
    if not souls:
        return templates.TemplateResponse(
            request, "dashboard.html", {"soul": None, "souls": [], "data": None}
        )
    slug = request.query_params.get("soul", souls[0]["slug"])
    async with soul_context(slug) as (soul, souls, session):
        data = await queries.get_dashboard_data(session, soul)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"soul": soul, "souls": souls, "data": data},
    )


@router.get("/souls/{slug}", response_class=HTMLResponse)
async def soul_dashboard(
    request: Request, slug: str, _: str = Depends(login_required)
):
    return RedirectResponse(url=f"/?soul={slug}", status_code=303)
