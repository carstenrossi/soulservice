"""Soul export/import routes for the admin web UI."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from soulservice.core import portability
from soulservice.core.crypto import dek_cache
from soulservice.core.db import get_session
from soulservice.web import queries
from soulservice.web.auth import login_required, require_role
from soulservice.web.db import soul_context
from soulservice.web.templating import templates

router = APIRouter()

MAX_IMPORT_BYTES = 50 * 1024 * 1024


@router.get("/souls/{slug}/portability", response_class=HTMLResponse)
async def portability_page(
    request: Request,
    slug: str,
    _: str = Depends(login_required),
):
    async with soul_context(slug) as (soul, souls, _session):
        pass
    users: list[dict] = []
    async with get_session() as owner_session:
        users = await queries.list_users(owner_session)
    flash = request.session.pop("flash_import", None)
    return templates.TemplateResponse(
        request,
        "portability.html",
        {
            "soul": soul,
            "souls": souls,
            "users": users,
            "conflict_modes": ["overwrite", "skip"],
            "flash": flash,
        },
    )


@router.post("/souls/{slug}/export")
async def export_soul_route(
    slug: str,
    include_audit: str | None = Form(None),
    all_statuses: str | None = Form(None),
    _: str = Depends(require_role("admin")),
):
    async with soul_context(slug) as (_soul, _souls, session):
        try:
            manifest, memories = await portability.export_soul(
                session,
                slug,
                include_audit=(include_audit == "true"),
                all_statuses=(all_statuses == "true"),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        ndjson = "".join(
            portability.memory_to_ndjson_line(m) + "\n" for m in memories
        )
        zf.writestr("memories.ndjson", ndjson)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}-export.zip"'},
    )


@router.post("/souls/{slug}/import")
async def import_soul_route(
    request: Request,
    slug: str,
    file: Annotated[UploadFile, File()],
    mode: str = Form("merge"),
    owner_user_id: str | None = Form(None),
    new_slug: str | None = Form(None),
    display_name: str | None = Form(None),
    on_conflict: str = Form("overwrite"),
    recompute_embeddings: str | None = Form(None),
    _: str = Depends(require_role("admin")),
):
    raw = await file.read()
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Upload exceeds 50 MB limit.")

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            try:
                lines = zf.read("memories.ndjson").decode("utf-8").splitlines()
            except KeyError:
                lines = []
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError) as e:
        raise HTTPException(status_code=400, detail="Invalid export ZIP file.") from e

    mems = [portability.parse_ndjson_line(line) for line in lines if line.strip()]

    try:
        async with get_session() as session:
            stats = await portability.import_soul(
                session,
                manifest,
                mems,
                into_slug=(slug if mode == "merge" else None),
                owner_user_id=(owner_user_id if mode == "new" else None),
                new_slug=new_slug or None,
                display_name=display_name or None,
                on_conflict=on_conflict,
                recompute_embeddings=(recompute_embeddings == "true"),
            )
            await session.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    dek_cache.invalidate(UUID(stats["soul_id"]))
    request.session["flash_import"] = stats
    target = new_slug if mode == "new" and new_slug else slug
    return RedirectResponse(url=f"/souls/{target}/portability", status_code=303)
