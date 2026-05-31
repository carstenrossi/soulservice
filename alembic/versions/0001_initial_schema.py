"""Initial schema: all tables, indexes, grants, RLS.

This is the single source of truth for the database schema. Extensions and
roles are bootstrapped by infra/init.sql; everything else lives here.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


# asyncpg uses the extended query (prepared statement) protocol, which rejects
# multiple commands in a single execute. Each DDL statement is therefore run
# on its own via op.execute in the lists below.

_TABLES: list[str] = [
    """
    CREATE TABLE tenants (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      name TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      plan TEXT NOT NULL DEFAULT 'personal',
      settings JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE users (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      email TEXT,
      display_name TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (tenant_id, email)
    )
    """,
    """
    CREATE TABLE souls (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      slug TEXT NOT NULL,
      display_name TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'active',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (tenant_id, slug)
    )
    """,
    """
    CREATE TABLE soul_keys (
      soul_id UUID PRIMARY KEY REFERENCES souls(id) ON DELETE CASCADE,
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      dek_encrypted BYTEA NOT NULL,
      key_version INT NOT NULL DEFAULT 1,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE soul_self_cores (
      soul_id UUID PRIMARY KEY REFERENCES souls(id) ON DELETE CASCADE,
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      content_encrypted BYTEA NOT NULL,
      content_nonce BYTEA NOT NULL,
      current_version INT NOT NULL DEFAULT 1,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_by UUID REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE soul_self_core_history (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      soul_id UUID NOT NULL REFERENCES souls(id) ON DELETE CASCADE,
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      version INT NOT NULL,
      content_encrypted BYTEA NOT NULL,
      content_nonce BYTEA NOT NULL,
      changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      changed_by UUID REFERENCES users(id),
      change_note TEXT,
      UNIQUE (soul_id, version)
    )
    """,
    """
    CREATE TABLE api_tokens (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      soul_id UUID NOT NULL REFERENCES souls(id) ON DELETE CASCADE,
      token_hash TEXT NOT NULL,
      token_prefix TEXT NOT NULL,
      name TEXT NOT NULL,
      scopes TEXT[] NOT NULL DEFAULT ARRAY['read','write'],
      mode VARCHAR(16) NOT NULL DEFAULT 'identity'
        CHECK (mode IN ('identity', 'messenger')),
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      last_used_at TIMESTAMPTZ,
      expires_at TIMESTAMPTZ NOT NULL,
      revoked_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE memories (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      soul_id UUID NOT NULL REFERENCES souls(id) ON DELETE CASCADE,
      content_encrypted BYTEA NOT NULL,
      content_nonce BYTEA NOT NULL,
      embedding VECTOR(1024),
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      last_recalled_at TIMESTAMPTZ,
      recall_count INT NOT NULL DEFAULT 0,
      source_client TEXT,
      salience FLOAT NOT NULL DEFAULT 0.5,
      status TEXT NOT NULL DEFAULT 'pending',
      tags TEXT[] NOT NULL DEFAULT '{}',
      injection_flags TEXT[] NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE facts (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      soul_id UUID NOT NULL REFERENCES souls(id) ON DELETE CASCADE,
      category TEXT NOT NULL,
      key TEXT NOT NULL,
      value_encrypted BYTEA NOT NULL,
      value_nonce BYTEA NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      confidence FLOAT NOT NULL DEFAULT 1.0,
      status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'deleted')),
      UNIQUE (tenant_id, soul_id, category, key)
    )
    """,
    """
    CREATE TABLE soul_properties (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      soul_id UUID NOT NULL REFERENCES souls(id) ON DELETE CASCADE,
      property_type TEXT NOT NULL,
      schema_version INT NOT NULL,
      value JSONB NOT NULL,
      is_sensitive BOOLEAN NOT NULL DEFAULT FALSE,
      value_encrypted BYTEA,
      value_nonce BYTEA,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'deleted')),
      UNIQUE (tenant_id, soul_id, property_type)
    )
    """,
    """
    CREATE TABLE soul_adaptations (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      soul_id UUID NOT NULL REFERENCES souls(id) ON DELETE CASCADE,
      category TEXT NOT NULL
        CHECK (category IN (
          'relationship_depth', 'topic_stance', 'behavioral_refinement',
          'shared_reference', 'emotional_calibration'
        )),
      content_encrypted BYTEA NOT NULL,
      content_nonce BYTEA NOT NULL,
      confidence FLOAT NOT NULL DEFAULT 0.5,
      source TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      superseded_by UUID REFERENCES soul_adaptations(id),
      status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'rejected'))
    )
    """,
    # Note: the legacy init.sql proposals table was missing id/tenant_id/soul_id,
    # so its RLS policy silently failed under psql. The columns below match the
    # Proposal ORM model and make the tenant_soul_isolation policy valid.
    """
    CREATE TABLE proposals (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      soul_id UUID NOT NULL REFERENCES souls(id) ON DELETE CASCADE,
      kind TEXT NOT NULL,
      payload_encrypted BYTEA NOT NULL,
      payload_nonce BYTEA NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      status TEXT NOT NULL DEFAULT 'pending',
      reviewed_at TIMESTAMPTZ,
      source_client TEXT
    )
    """,
    """
    CREATE TABLE audit_log (
      id BIGSERIAL PRIMARY KEY,
      occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      tenant_id UUID,
      user_id UUID,
      soul_id UUID,
      token_id UUID,
      tool_name TEXT,
      args_hash TEXT,
      result_size INT,
      status TEXT,
      source_ip INET,
      source_client TEXT
    )
    """,
]

_INDEXES: list[str] = [
    "CREATE INDEX soul_self_core_history_soul_idx "
    "ON soul_self_core_history (soul_id, version DESC)",
    "CREATE INDEX memories_tenant_soul_idx ON memories (tenant_id, soul_id)",
    "CREATE INDEX memories_embedding_idx "
    "ON memories USING ivfflat (embedding vector_cosine_ops)",
    "CREATE INDEX soul_properties_value_idx ON soul_properties USING gin (value)",
    "CREATE INDEX soul_adaptations_active_idx "
    "ON soul_adaptations (soul_id, status) WHERE status = 'active'",
]

# App-user grants: SELECT/INSERT/UPDATE on data, no DELETE on audit
_GRANTS: list[str] = [
    "GRANT SELECT, INSERT, UPDATE ON tenants, users, souls TO soulservice_app",
    "GRANT SELECT, INSERT, UPDATE ON soul_keys TO soulservice_app",
    "GRANT SELECT, INSERT, UPDATE ON soul_self_cores TO soulservice_app",
    "GRANT SELECT, INSERT ON soul_self_core_history TO soulservice_app",
    "GRANT SELECT, INSERT, UPDATE ON api_tokens TO soulservice_app",
    "GRANT SELECT, INSERT, UPDATE ON memories TO soulservice_app",
    "GRANT SELECT, INSERT, UPDATE ON facts TO soulservice_app",
    "GRANT SELECT, INSERT, UPDATE ON soul_properties TO soulservice_app",
    "GRANT SELECT, INSERT, UPDATE ON soul_adaptations TO soulservice_app",
    "GRANT SELECT, INSERT, UPDATE ON proposals TO soulservice_app",
    "GRANT SELECT, INSERT ON audit_log TO soulservice_app",
    "GRANT USAGE ON SEQUENCE audit_log_id_seq TO soulservice_app",
    # Explicitly deny destructive ops on audit_log
    "REVOKE UPDATE, DELETE ON audit_log FROM soulservice_app",
]

_RLS_TABLES: list[str] = [
    "memories",
    "facts",
    "soul_properties",
    "soul_adaptations",
    "proposals",
    "soul_keys",
    "soul_self_cores",
    "soul_self_core_history",
]


def upgrade() -> None:
    for stmt in _TABLES:
        op.execute(stmt)
    for stmt in _INDEXES:
        op.execute(stmt)
    for stmt in _GRANTS:
        op.execute(stmt)
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_soul_isolation ON {table}
              FOR ALL TO soulservice_app
              USING (
                tenant_id = current_setting('app.current_tenant')::uuid
                AND soul_id = current_setting('app.current_soul')::uuid
              )
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS audit_log, proposals, soul_adaptations, soul_properties,
            facts, memories, api_tokens, soul_self_core_history, soul_self_cores,
            soul_keys, souls, users, tenants CASCADE
        """
    )
