# Soulservice — Functional Overview

## Overview

Soulservice is an MCP-based platform that embodies persistent AI personalities — Souls with their own character, voice, and a growing relationship with their humans. Frontend clients (Claude, ChatGPT, Cursor, etc.) connect via MCP to a specific Soul; the frontend LLM becomes the voice while the Soul's identity, memories, and relationship context live on the server.

## Core Concepts

**Souls** are persistent AI personalities bound to a tenant and an owning user. Each Soul has a slug, display name, encrypted Self Core (YAML identity document), Adaptation Layer (learned dispositions), memories, facts, and properties. Souls are not passive profile stores — they remember shared history episodically and develop over time.

**Tenants and Users** provide multi-tenant isolation from day one. Every row belongs to a tenant; users belong to a tenant and own Souls. Cross-tenant access is structurally impossible via Postgres Row Level Security (RLS).

**The Five-Layer Model** describes how a Soul's identity evolves:

| Layer | What it holds | How it changes |
|---|---|---|
| **Self Core** | Values, voice, prohibitions, origin | Only by human, explicitly |
| **Emergent Self** | Self-narrative: who the Soul has become | Written by the Soul itself, periodically *(planned — Phase 5.5)* |
| **Adaptations** | Learned stances, references, depth | Dream Phase (auto, planned) or manual via CLI |
| **Memories** | Individual episodes | Captured in conversation, reviewed before confirmation |
| **Reflections** | Self-evaluations after conversations | Written by the Soul after each session *(planned — Phase 4)* |

The Self Core is immutable for the Soul itself — a human designer writes it. Adaptations, memories, facts, and properties are the operational layers available today (Phases 1–3).

## MCP Tools

Source: `@mcp.tool()` functions in `src/soulservice/mcp/server.py`.

| Tool | Parameters | Scope | Description |
|---|---|---|---|
| `health` | — | *(none — unauthenticated)* | Server health check. Returns status. |
| `who_are_you` | — | `read` | Load the character profile for this session (Self Core + Adaptations). Call first. |
| `whats_our_history` | — | `read` | Load relationship context and shared history. Call after `who_are_you`. |
| `whoami` | — | `read` | Which Soul, Tenant, and User is this token bound to? |
| `remember_this` | `content`, `tags?`, `salience?` (default `0.5`) | `write` | Note something from the conversation worth keeping. Stored as pending proposal for human review. |
| `recall` | `query`, `k?` (default `5`) | `read` | Search through past conversation notes by meaning (semantic search). |
| `recall_recent` | `days?` (default `7`) | `read` | Get recent conversation notes (chronological). |
| `list_proposals` | `status?` (default `"pending"`) | `read` | List conversation notes pending human review. |
| `decide` | `proposal_id`, `action` (`"confirm"` or `"reject"`), `note?` | `write` | Approve or reject a conversation note. |
| `learn_fact` | `category`, `key`, `value`, `confidence?` (default `1.0`) | `write` | Store or update a structured fact (e.g. user preferences, known context). |
| `get_facts` | `category?` | `read` | Retrieve stored facts, optionally filtered by category. |
| `forget_fact` | `category`, `key` | `write` | Remove a stored fact that is no longer accurate (soft-delete). |
| `set_property` | `property_type`, `value` (JSON object) | `write` | Store or update a typed property (e.g. `communication_style`, `boundaries`). |
| `get_properties` | `property_type?` | `read` | Retrieve stored properties, optionally filtered by type. |
| `delete_property` | `property_type` | `write` | Soft-delete a property that no longer applies. |

**Write tools** (require scope `write`): `remember_this`, `decide`, `learn_fact`, `forget_fact`, `set_property`, `delete_property`. All other authenticated tools require scope `read`. `health` is unauthenticated.

## CLI (`soulctl`)

Source: `@click` commands in `src/soulservice/cli/main.py`. The CLI speaks directly to the database (admin context), not via MCP.

### Top-level

| Command | Description |
|---|---|
| `init` | Seed tenant/user/soul (idempotent). Options: `--self-core-file` |
| `health` | Check database connectivity |

### `tenant`

| Command | Description |
|---|---|
| `tenant create NAME` | Create a new tenant |
| `tenant list` | List all tenants |

### `user`

| Command | Description |
|---|---|
| `user create --tenant UUID --email EMAIL --name DISPLAY_NAME` | Create a new user in a tenant |

### `soul`

| Command | Description |
|---|---|
| `soul create --user UUID --slug SLUG --display DISPLAY_NAME` | Create a new soul and generate its encryption key (DEK) |

### `self-core`

| Command | Description |
|---|---|
| `self-core edit --soul SLUG` | Open the current Self Core in `$EDITOR` for editing |
| `self-core import --soul SLUG [--note NOTE] [FILE]` | Import a YAML file as the new Self Core |
| `self-core export --soul SLUG` | Export the current Self Core as YAML to stdout |
| `self-core history --soul SLUG` | Show Self Core version history |

### `token`

| Command | Description |
|---|---|
| `token create --soul SLUG --name NAME [--expires-in 90d] [--env dev] [--mode identity\|messenger] [--read-only]` | Generate a new API token for a soul |
| `token list --soul SLUG` | List tokens for a soul (shows scopes) |
| `token revoke TOKEN_ID` | Revoke an API token |

Default token scopes: `["read", "write"]`. `--read-only` grants only `read`.

### `adaptation`

| Command | Description |
|---|---|
| `adaptation add --soul SLUG --category CAT [--confidence 0.5] [--source manual] CONTENT` | Add a new adaptation |
| `adaptation list --soul SLUG [--status active]` | List adaptations for a soul |
| `adaptation supersede ADAPTATION_ID NEW_CONTENT [--confidence FLOAT]` | Replace an adaptation with a new version, preserving history |

Categories: `relationship_depth`, `topic_stance`, `behavioral_refinement`, `shared_reference`, `emotional_calibration`.

### `proposals`

| Command | Description |
|---|---|
| `proposals list --soul SLUG [--status pending]` | List memory proposals for a soul |
| `proposals decide MEMORY_ID --action confirm\|reject [--note NOTE]` | Confirm or reject a memory proposal |

### `memory`

| Command | Description |
|---|---|
| `memory search --soul SLUG [-k 5] QUERY` | Semantic search through confirmed memories |
| `memory list --soul SLUG [--recent 7] [--status confirmed]` | List recent memories for a soul |
| `memory forget MEMORY_ID` | Mark a confirmed memory as forgotten |

### `fact`

| Command | Description |
|---|---|
| `fact set --soul SLUG --category CAT --key KEY [--confidence 1.0] VALUE` | Set (upsert) a fact |
| `fact list --soul SLUG [--category CAT]` | List facts for a soul |
| `fact get --soul SLUG --category CAT --key KEY` | Get a single fact's value |
| `fact remove --soul SLUG --category CAT --key KEY` | Soft-delete a fact |

### `property`

| Command | Description |
|---|---|
| `property set --soul SLUG --type TYPE JSON_VALUE` | Set a property (JSON object string) |
| `property list --soul SLUG [--type TYPE]` | List properties for a soul |
| `property get --soul SLUG --type TYPE` | Get a single property's value |
| `property remove --soul SLUG --type TYPE` | Soft-delete a property |

## Web UI (Admin)

Source: `src/soulservice/web/` (FastAPI + HTMX + Jinja2). A localhost-only admin interface for reviewing proposals, browsing/searching memories, editing Self Cores, managing facts/properties/API tokens, and viewing the audit log.

**Authentication — magic link.** Admins request a one-time login link by email (captured locally by Mailpit in dev). Tokens are 256-bit, stored only as a SHA-256 hash, single-use, and short-lived. The link opens a confirmation page and is consumed by an explicit `POST` (so email prefetchers cannot burn it). `/login` is rate-limited per client IP + email, and `WEB_SESSION_SECRET` is mandatory (the app refuses to start without it).

**Authorization — RBAC.** Roles are configured via `WEB_ADMIN_EMAILS` as `email:role` (a bare email defaults to `admin`):

| Role | Read pages | Edit memories/facts/properties/self-core | Create/revoke tokens |
|---|:---:|:---:|:---:|
| `viewer` | yes | no | no |
| `editor` | yes | yes | no |
| `admin` | yes | yes | yes |

Enforcement is server-side (a `require_role` dependency) and mirrored in the templates.

**Data access.** Like the MCP runtime, the Web UI runs under the restricted `soulservice_app` role: per-soul work goes through `get_scoped_session(tenant_id, soul_id)` (RLS enforced), and the scoped session owns the transaction. It does **not** use the DB owner. Cross-soul/non-RLS reads (soul list, audit, tokens) use the app role directly. Every write is recorded in the audit log.

## Data Model

Source: `src/soulservice/models/`. Schema is managed by **Alembic** (`alembic upgrade head`); `infra/init.sql` only bootstraps extensions and roles.

| Table | Holds | Encrypted |
|---|---|---|
| `tenants` | Tenant name, plan, settings (JSONB) | No |
| `users` | User email, display name, tenant membership | No |
| `souls` | Soul slug, display name, status, owner | No |
| `soul_keys` | Per-soul DEK wrapped by master key (`dek_encrypted`) | Yes (DEK envelope) |
| `soul_self_cores` | Current Self Core YAML (versioned) | Yes (`content_encrypted`, AAD domain `self_core`) |
| `soul_self_core_history` | Archived Self Core versions | Yes |
| `api_tokens` | Argon2id-hashed token, prefix, scopes, mode, expiry | No (hash only, not plaintext) |
| `memories` | Episodic notes, embeddings (pgvector), salience, tags, status | Yes (`content_encrypted`, AAD domain `memory`) |
| `facts` | Structured key/value facts by category | Yes (`value_encrypted`, AAD domain `fact`) |
| `soul_properties` | Typed JSON properties with schema version | Conditional: plaintext JSONB if `is_sensitive=false`; AES-256-GCM if `is_sensitive=true` (AAD domain `property`) |
| `soul_adaptations` | Learned dispositions by category | Yes (`content_encrypted`, AAD domain `adaptation`) |
| `proposals` | Generic encrypted proposal payloads | Yes (`payload_encrypted`) |
| `audit_log` | Append-only tool invocation metadata (tool name, args hash, result size) | No |

## Encryption & Security Model

### Envelope encryption

One **master key** (from `SOULSERVICE_MASTER_KEY` env var) wraps one **DEK (Data Encryption Key) per soul**. Content is encrypted with **AES-256-GCM** using the soul's DEK. Master key rotation re-encrypts DEKs, not bulk content.

```
Master Key (env)  →  encrypts/wraps  →  DEK per Soul (in soul_keys)
DEK per Soul      →  encrypts         →  Self Core, memories, facts, properties, adaptations
```

Decrypted DEKs are cached in process memory with a configurable TTL (`dek_cache_ttl_seconds`).

### AAD binding

Every AES-GCM encryption binds **Associated Authenticated Data (AAD)** to `soul_id` + a domain label. Ciphertext cannot be replayed across souls or between record types — decryption fails if the context does not match.

Domain labels (exact): `memory`, `fact`, `property`, `self_core`, `adaptation`, `dek`.

Format: `soul_id.bytes + b"|" + domain.encode("ascii")`.

### Row Level Security

Postgres RLS on all sensitive tables, scoped per tenant + soul per request. The application runtime uses the restricted DB role **`soulservice_app`** (no `BYPASSRLS`, RLS forced), separate from the owner/migration role.

### Token auth and scopes

- Tokens are Argon2id-hashed in the database; mandatory expiry; per-client tokens.
- Default scopes: `["read", "write"]`. Create read-only tokens with `soulctl token create --read-only` (grants only `read`).
- Two token **modes**: `identity` (LLM becomes the Soul) and `messenger` (LLM channels the Soul).
- **Audit log:** append-only; every tool invocation recorded with args hash (never plaintext). No `DELETE` on audit log.

### Prompt injection hardening

Retrieved content is wrapped in `<retrieved_memory untrusted="true">`, `<retrieved_fact untrusted="true">`, and `<retrieved_property untrusted="true">` tags; injection patterns are flagged.

## Request Flow

```
Client (MCP over HTTP)
    ↓  Authorization: Bearer <token>
BearerAuthASGIMiddleware
    ↓  resolve_bearer_token → TokenIdentity (tenant_id, user_id, soul_id, scopes, mode)
    ↓  stored in ContextVar (current_identity)
Tool handler
    ↓  _require_identity(required_scope) — auth + scope + rate limit
    ↓  get_scoped_session(tenant_id, soul_id) — SET LOCAL for RLS context
Tool logic (RLS enforces tenant/soul isolation)
    ↓  decrypt via soul DEK (AAD-verified)
    ↓  log_tool_call → audit_log (metadata only)
Return result to client
```

`health()` bypasses authentication entirely. All other tools require a valid Bearer token with the appropriate scope.

The admin Web UI follows the same data path: after magic-link auth and an RBAC role check, per-soul requests open `get_scoped_session(tenant_id, soul_id)` so RLS is enforced there too (it is no longer an RLS-bypassing owner path).

## Roadmap Status

| Phase | Status |
|---|---|
| **Phase 1** — MCP server, Self Core, Adaptation Layer, CLI, chat, security baseline | Done |
| **Phase 2** — Embeddings, `recall()`, `remember_this()`, proposals, review workflow | Done |
| **Phase 3** — Facts, properties, Web UI (FastAPI + HTMX) | Done |
| **Phase 4** — Dream Phase + self-reflection | Planned |
| **Phase 5** — OAuth, key rotation, local embeddings | Planned |
| **Phase 5.5** — Emergent Self (narrative self-image) | Planned |
| **Phase 6+** — Autonomous exploration, multi-soul awareness | Planned |
