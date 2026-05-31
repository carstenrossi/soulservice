-- ============================================================
-- Soulservice – Database Bootstrap
-- Runs once on first postgres container start.
-- Bootstrap only: extensions + roles + database-level grants.
-- The schema (tables, indexes, constraints, grants, RLS) lives in
-- Alembic migrations. Apply it with `alembic upgrade head`.
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

-- app gets minimal DML rights (table/sequence grants are applied by the
-- Alembic initial-schema migration, after the tables exist)
GRANT CONNECT ON DATABASE soulservice TO soulservice_app;
GRANT USAGE ON SCHEMA public TO soulservice_app;

-- ============================================================
-- Schema (tables, indexes, constraints, grants, RLS) is managed by
-- Alembic. Run `alembic upgrade head` after the container is up.
-- ============================================================
