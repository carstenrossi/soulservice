"""Audit logging – append-only record of all tool invocations."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def log_tool_call(
    session: AsyncSession,
    *,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    soul_id: UUID | None = None,
    token_id: UUID | None = None,
    tool_name: str,
    args: dict | None = None,
    result_size: int | None = None,
    status: str = "success",
    source_ip: str | None = None,
    source_client: str | None = None,
) -> None:
    """Insert an audit log entry. Args are hashed, never stored as plaintext."""
    args_hash = (
        hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        if args
        else None
    )
    await session.execute(
        text("""
            INSERT INTO audit_log
                (tenant_id, user_id, soul_id, token_id, tool_name,
                 args_hash, result_size, status, source_ip, source_client)
            VALUES
                (:tenant_id, :user_id, :soul_id, :token_id, :tool_name,
                 :args_hash, :result_size, :status, :source_ip::inet, :source_client)
        """),
        {
            "tenant_id": str(tenant_id) if tenant_id else None,
            "user_id": str(user_id) if user_id else None,
            "soul_id": str(soul_id) if soul_id else None,
            "token_id": str(token_id) if token_id else None,
            "tool_name": tool_name,
            "args_hash": args_hash,
            "result_size": result_size,
            "status": status,
            "source_ip": source_ip,
            "source_client": source_client,
        },
    )
