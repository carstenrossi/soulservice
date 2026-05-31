"""Self Core editor with CodeMirror YAML mode."""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from soulservice.core.crypto import dek_cache
from soulservice.web import queries
from soulservice.web.auth import login_required, require_role
from soulservice.web.db import soul_context
from soulservice.web.templating import templates

router = APIRouter()


@router.get("/souls/{slug}/self-core", response_class=HTMLResponse)
async def self_core_page(
    request: Request, slug: str, _: str = Depends(login_required)
):
    async with soul_context(slug) as (soul, souls, session):
        content, version = await queries.load_self_core(session, soul)
    return templates.TemplateResponse(
        request,
        "self_core.html",
        {"soul": soul, "souls": souls, "content": content, "version": version, "error": None},
    )


@router.post("/souls/{slug}/self-core", response_class=HTMLResponse)
async def save_self_core(
    request: Request,
    slug: str,
    content: str = Form(...),
    note: str = Form(""),
    version: int = Form(...),
    _: str = Depends(require_role("editor")),
):
    async with soul_context(slug) as (soul, souls, session):
        try:
            await queries.save_self_core(session, soul, content, note, version)
        except yaml.YAMLError as e:
            return templates.TemplateResponse(
                request,
                "self_core.html",
                {
                    "soul": soul,
                    "souls": souls,
                    "content": content,
                    "version": version,
                    "error": f"Invalid YAML: {e}",
                },
            )
        except HTTPException as e:
            if e.status_code != 409:
                raise
            # Conflict: reload the current stored version so the user can rebase.
            current, current_version = await queries.load_self_core(session, soul)
            return templates.TemplateResponse(
                request,
                "self_core.html",
                {
                    "soul": soul,
                    "souls": souls,
                    "content": current,
                    "version": current_version,
                    "error": e.detail,
                },
            )
    dek_cache.invalidate(soul["id"])
    return RedirectResponse(url=f"/souls/{slug}/self-core", status_code=303)
