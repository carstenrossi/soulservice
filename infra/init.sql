-- ============================================================
-- Soulservice – Database Initialization
-- Runs once on first postgres container start.
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- Roles: app (restricted) and migrate (schema changes)
-- In production, use separate credentials. For local dev, simple passwords suffice.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'soulservice_app') THEN
    CREATE ROLE soulservice_app LOGIN PASSWORD 'soulservice_app_pw';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'soulservice_migrate') THEN
    CREATE ROLE soulservice_migrate LOGIN PASSWORD 'soulservice_migrate_pw';
  END IF;
END
$$;

-- migrate can do schema changes
GRANT ALL ON DATABASE soulservice TO soulservice_migrate;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO soulservice_migrate;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO soulservice_migrate;

-- app gets minimal DML rights (granted per table after creation)
GRANT CONNECT ON DATABASE soulservice TO soulservice_app;
GRANT USAGE ON SCHEMA public TO soulservice_app;

-- ============================================================
-- Tables
-- ============================================================

CREATE TABLE tenants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  plan TEXT NOT NULL DEFAULT 'personal',
  settings JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  email TEXT,
  display_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, email)
);

CREATE TABLE souls (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slug TEXT NOT NULL,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, slug)
);

CREATE TABLE soul_keys (
  soul_id UUID PRIMARY KEY REFERENCES souls(id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  dek_encrypted BYTEA NOT NULL,
  key_version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE soul_self_cores (
  soul_id UUID PRIMARY KEY REFERENCES souls(id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  content_encrypted BYTEA NOT NULL,
  content_nonce BYTEA NOT NULL,
  current_version INT NOT NULL DEFAULT 1,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by UUID REFERENCES users(id)
);

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
);

CREATE INDEX soul_self_core_history_soul_idx
  ON soul_self_core_history (soul_id, version DESC);

CREATE TABLE api_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  soul_id UUID NOT NULL REFERENCES souls(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL,
  token_prefix TEXT NOT NULL,
  name TEXT NOT NULL,
  scopes TEXT[] NOT NULL DEFAULT ARRAY['read','write'],
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ
);

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
);

CREATE INDEX memories_tenant_soul_idx ON memories (tenant_id, soul_id);
CREATE INDEX memories_embedding_idx ON memories USING ivfflat (embedding vector_cosine_ops);

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
  UNIQUE (tenant_id, soul_id, category, key)
);

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
  UNIQUE (tenant_id, soul_id, property_type)
);

CREATE INDEX soul_properties_value_idx ON soul_properties USING gin (value);

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
);

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
);

-- ============================================================
-- App-User Grants: SELECT/INSERT/UPDATE on data, no DELETE on audit
-- ============================================================
GRANT SELECT, INSERT, UPDATE ON tenants, users, souls TO soulservice_app;
GRANT SELECT, INSERT, UPDATE ON soul_keys TO soulservice_app;
GRANT SELECT, INSERT, UPDATE ON soul_self_cores TO soulservice_app;
GRANT SELECT, INSERT ON soul_self_core_history TO soulservice_app;
GRANT SELECT, INSERT, UPDATE ON api_tokens TO soulservice_app;
GRANT SELECT, INSERT, UPDATE ON memories TO soulservice_app;
GRANT SELECT, INSERT, UPDATE ON facts TO soulservice_app;
GRANT SELECT, INSERT, UPDATE ON soul_properties TO soulservice_app;
GRANT SELECT, INSERT, UPDATE ON proposals TO soulservice_app;
GRANT SELECT, INSERT ON audit_log TO soulservice_app;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO soulservice_app;

-- Explicitly deny destructive ops on audit_log
REVOKE UPDATE, DELETE ON audit_log FROM soulservice_app;

-- ============================================================
-- Row Level Security
-- ============================================================
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_properties ENABLE ROW LEVEL SECURITY;
ALTER TABLE proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_self_cores ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_self_core_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_soul_isolation ON memories
  FOR ALL TO soulservice_app
  USING (
    tenant_id = current_setting('app.current_tenant')::uuid
    AND soul_id = current_setting('app.current_soul')::uuid
  );

CREATE POLICY tenant_soul_isolation ON facts
  FOR ALL TO soulservice_app
  USING (
    tenant_id = current_setting('app.current_tenant')::uuid
    AND soul_id = current_setting('app.current_soul')::uuid
  );

CREATE POLICY tenant_soul_isolation ON soul_properties
  FOR ALL TO soulservice_app
  USING (
    tenant_id = current_setting('app.current_tenant')::uuid
    AND soul_id = current_setting('app.current_soul')::uuid
  );

CREATE POLICY tenant_soul_isolation ON proposals
  FOR ALL TO soulservice_app
  USING (
    tenant_id = current_setting('app.current_tenant')::uuid
    AND soul_id = current_setting('app.current_soul')::uuid
  );

CREATE POLICY tenant_soul_isolation ON soul_keys
  FOR ALL TO soulservice_app
  USING (
    tenant_id = current_setting('app.current_tenant')::uuid
    AND soul_id = current_setting('app.current_soul')::uuid
  );

CREATE POLICY tenant_soul_isolation ON soul_self_cores
  FOR ALL TO soulservice_app
  USING (
    tenant_id = current_setting('app.current_tenant')::uuid
    AND soul_id = current_setting('app.current_soul')::uuid
  );

CREATE POLICY tenant_soul_isolation ON soul_self_core_history
  FOR ALL TO soulservice_app
  USING (
    tenant_id = current_setting('app.current_tenant')::uuid
    AND soul_id = current_setting('app.current_soul')::uuid
  );
