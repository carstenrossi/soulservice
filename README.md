# Soulservice

An MCP-based platform that embodies AI personalities — with their own character, voice, and a growing relationship with their humans. Souls are not profile stores; they are counterparts that remember shared history.

Frontend clients (Claude, ChatGPT, Cursor, etc.) connect via [MCP](https://modelcontextprotocol.io/) to a specific Soul. The frontend model becomes the voice — the identity lives on the server, not in the client.

## How is this different from "AI Memory"?

| Classic (e.g. ChatGPT Memory) | Soulservice |
|---|---|
| "User works at Acme Corp" | "When Carsten told me about Acme Corp, he was evaluating X" |
| Facts about the user | Episodes with the user |
| Profile | Relationship |
| Third person | First person |
| Passive (gets read) | Active (remembers, has opinions) |
| One per user | Multiple, as a group of friends |

## Architecture

```
Client (Claude / ChatGPT / Cursor)
    ↓ MCP over HTTPS
    ↓ Bearer Token (Argon2id-hashed in DB)
    ↓
Soulservice Server (MCP SDK on Railway/Docker)
    ↓
Auth Middleware: Token → (tenant_id, user_id, soul_id)
    ↓ SET LOCAL for Row Level Security
    ↓
Tool Handlers (RLS enforces isolation)
    ↓
   ┌──┴──┬──────────┬──────────┬──────────┐
   ↓     ↓          ↓          ↓          ↓
 Self  Memories   Facts    Properties  Audit
 Core  (AES-256)  (AES-256) (JSONB)   (append-only)
```

**Key design decisions:**
- **Multi-tenant from day one.** Every row belongs to a tenant. Cross-tenant access is structurally impossible (RLS).
- **Envelope encryption.** One master key (in env), one DEK per soul. Master key rotation re-encrypts DEKs, not data.
- **Separation of identity and model.** The frontend LLM is swappable. Souls persist.
- **Review gate against drift.** Learning doesn't happen automatically — proposals go through human review.

## Tech Stack

- **Protocol:** MCP (Spec 2025-06-18), Streamable HTTP transport
- **Server:** Official `mcp` Python SDK (`mcp.server.fastmcp.FastMCP`)
- **ORM:** SQLModel + Alembic (async, Pydantic v2)
- **Database:** Postgres 18 + pgvector 0.8.2 + pgcrypto
- **Encryption:** AES-256-GCM (envelope encryption), Argon2id for token hashing
- **Chat:** Anthropic Claude API with MCP tool integration
- **Python:** 3.12+, managed with `uv`

## Quickstart

```bash
# Clone and enter
git clone https://github.com/carstenrossi/soulservice.git
cd soulservice

# Set up environment
cp .env.example .env
# Generate a master key:
python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
# Paste as SOULSERVICE_MASTER_KEY in .env
# Set POSTGRES_PASSWORD to something random
# Set ANTHROPIC_API_KEY if you want the chat interface

# Start Postgres
docker compose up -d postgres

# Install dependencies
uv sync --python python3.12

# Initialize database (creates tenant, user, soul + imports Self Core)
export DATABASE_URL="postgresql+asyncpg://soulservice:${POSTGRES_PASSWORD}@localhost:6000/soulservice"
export SOULSERVICE_MASTER_KEY
uv run soulctl init --self-core-file george.yaml

# Create an API token
uv run soulctl token create --soul george --name dev --expires-in 90d
# Save the token — it's shown only once
# Paste it as CHAT_MCP_TOKEN in .env

# Start the MCP server
uv run python -m soulservice.mcp.server

# In another terminal: chat with the soul
source .env
export DATABASE_URL SOULSERVICE_MASTER_KEY ANTHROPIC_API_KEY CHAT_MCP_TOKEN
uv run soulservice-chat
```

## Project Structure

```
src/soulservice/
├── core/           # Config, async DB, AES-256-GCM crypto, Argon2id auth, audit
├── models/         # SQLModel: tenants, users, souls, keys, memories, facts, ...
├── mcp/            # MCP server, Bearer auth middleware, tool handlers
│   └── tools/      # who_are_you, whats_our_history, health, whoami
├── cli/            # soulctl: tenant/user/soul CRUD, self-core editor, tokens
├── chat.py         # Terminal chat interface (Claude API + MCP tools)
└── web/            # Phase 3: FastAPI + HTMX admin UI
```

## MCP Tools (Phase 1)

| Tool | Description |
|---|---|
| `who_are_you()` | Load the Soul's identity (Self Core). Call first. |
| `whats_our_history()` | Relationship overview and current topics. |
| `whoami()` | Which Soul, Tenant, User is this token bound to? |
| `health()` | Server health check. |

## CLI (`soulctl`)

```bash
soulctl init                              # Seed tenant/user/soul
soulctl tenant create "My Tenant"         # Create tenant
soulctl soul create --user <id> --slug ai --display "AI"
soulctl self-core edit --soul george      # Open Self Core in $EDITOR
soulctl self-core export --soul george    # Export as YAML
soulctl self-core import --soul george < soul.yaml
soulctl token create --soul george --name cursor --expires-in 90d
soulctl token list --soul george
soulctl token revoke <token-id>
soulctl health                            # Check DB connectivity
```

## Security

- **At-rest encryption:** All memory content, facts, proposals, and Self Cores are AES-256-GCM encrypted with per-soul keys (envelope encryption).
- **Row Level Security:** Postgres RLS on all sensitive tables, scoped per tenant + soul per request.
- **Token auth:** Argon2id hashing (OWASP 2026 recommendation), mandatory expiry, per-client tokens.
- **Audit log:** Append-only, every tool invocation recorded with args hash (never plaintext).
- **Prompt injection hardening:** Retrieved content wrapped in `<retrieved_memory untrusted="true">` tags, injection patterns flagged.
- **Restricted DB user:** App user has no `BYPASSRLS`, no `DELETE` on audit log.

## Roadmap

- **Phase 1 (current):** MCP server, Self Core, CLI, chat interface, security baseline
- **Phase 2:** Embeddings (Mistral), `recall()`, `remember_this()`, proposals, review workflow
- **Phase 3:** Facts, properties, Web UI (FastAPI + HTMX)
- **Phase 4:** Nightly consolidation, introspection
- **Phase 5+:** OAuth, key rotation, local embeddings, multi-soul awareness

## Self Cores

A Soul's identity is defined in a YAML document called the Self Core. It contains character traits, voice patterns, values with conflict examples, behavioral stances, and relationship seeds. See [`george.yaml`](george.yaml) for the first soul — George, named after Georgina from "The Famous Five."

Self Cores are stored encrypted in the database, versioned with full history, and served to frontend clients via the `who_are_you()` MCP tool. The soul speaks in whatever language its Self Core is written in.

## License

TBD

---

Built by [Carsten Rossi](https://github.com/carstenrossi), with Claude.
