"""Properties management: schema-driven forms."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from soulservice.mcp.tools.properties import PROPERTY_SCHEMAS
from soulservice.web import queries
from soulservice.web.auth import login_required, require_role
from soulservice.web.db import soul_context
from soulservice.web.templating import templates

router = APIRouter()


@router.get("/souls/{slug}/properties", response_class=HTMLResponse)
async def properties_page(
    request: Request, slug: str, _: str = Depends(login_required)
):
    async with soul_context(slug) as (soul, souls, session):
        items = await queries.list_properties(session, soul["id"])
    existing = {p["property_type"]: p for p in items}
    return templates.TemplateResponse(
        request,
        "properties.html",
        {
            "soul": soul,
            "souls": souls,
            "items": items,
            "schemas": PROPERTY_SCHEMAS,
            "existing": existing,
        },
    )


@router.post("/souls/{slug}/properties/{property_type}")
async def set_property_route(
    request: Request,
    slug: str,
    property_type: str,
    _: str = Depends(require_role("editor")),
):
    form = await request.form()
    schema = PROPERTY_SCHEMAS.get(property_type)
    if schema is None:
        return RedirectResponse(url=f"/souls/{slug}/properties", status_code=303)

    value: dict = {}
    for key in schema["allowed_keys"]:
        raw = form.get(key, "")
        if raw:
            value[key] = raw

    async with soul_context(slug) as (soul, _souls, session):
        await queries.set_property_web(session, soul, property_type, value)
    return RedirectResponse(url=f"/souls/{slug}/properties", status_code=303)


@router.post("/souls/{slug}/properties/{property_type}/delete")
async def delete_property_route(
    request: Request,
    slug: str,
    property_type: str,
    _: str = Depends(require_role("editor")),
):
    async with soul_context(slug) as (soul, _souls, session):
        await queries.delete_property_web(session, soul, property_type)
    return RedirectResponse(url=f"/souls/{slug}/properties", status_code=303)
