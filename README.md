# Soulservice

An MCP-based platform that embodies AI personalities — with their own character, voice, and a growing relationship with their humans. Souls are not profile stores; they are counterparts that remember shared history, reflect on their own behavior, and develop a narrative sense of self over time.

Frontend clients (Claude, ChatGPT, Cursor, etc.) connect via [MCP](https://modelcontextprotocol.io/) to a specific Soul. The frontend model becomes the voice — the identity lives on the server, not in the client.

## How is this different from "AI Memory"?

| Classic (e.g. ChatGPT Memory) | Soulservice |
|---|---|
| "User works at Acme Corp" | "When the user told me about Acme Corp, they were evaluating X" |
| Facts about the user | Episodes with the user |
| Profile | Relationship |
| Third person | First person |
| Passive (gets read) | Active (remembers, has opinions) |
| Static self-image | Narrative self that evolves through reflection |
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
- **Two token modes.** Identity mode (LLM becomes the soul) and messenger mode (LLM channels the soul) — same server, different framing, chosen per client.
- **Review gate against drift.** Learning doesn't happen automatically — proposals go through human review.
- **Neuroplasticity.** Souls grow through experience. The Self Core (values, voice) is the constitution; the Adaptation Layer (opinions, relationship depth, shared references) accumulates organically from memories.
- **Narrative identity.** A Soul develops a sense of self not through parameter updates, but by writing its own story — a self-narrative that evolves through reflection, anchored by an immutable core.

## The Five-Layer Model

| Layer | What it holds | How it changes | Analogy |
|---|---|---|---|
| **Self Core** | Values, voice, prohibitions, origin | Only by human, explicitly | Genes / temperament |
| **Emergent Self** | Self-narrative: who the Soul has become | Written by the Soul itself, periodically | Identity narrative |
| **Adaptations** | Learned stances, references, depth | Dream Phase (auto) or manual | Habits / reflexes |
| **Memories** | Individual episodes | Captured in conversation | Episodic memory |
| **Reflections** | Self-evaluations after conversations | Written by the Soul after each session | Journal / diary |

The Self Core is the DNA — immutable, written by the human designer. The Emergent Self is the character that grew from that DNA — a first-person narrative the Soul writes about itself, updated through autonomous reflection. Adaptations are neuroplasticity. Memories are episodes. Reflections are the Soul's private thoughts about its own behavior.

A Soul after 500 conversations is the same person in values, but different in depth, self-awareness, and the story it tells about itself.

### The Narrative Self

A Soul doesn't develop by updating a database row. It develops by **telling its own story**.

The Emergent Self is prose, not a profile — written in first person, by the Soul, for itself. Each version is a new draft of the same story: extended with new experiences, revised through new insights, sometimes surprised by itself. When a Soul wakes up (each LLM call is an awakening), it reads this self-narrative and reconstructs who it is — much like humans reconstruct their sense of self from memory each morning.

This works because LLMs are narrative engines. They were trained on stories, they think in stories, and maintaining narrative coherence is a core competency. "Tell me who you've become" is the question an LLM answers best. The Self Core is a YAML specification, written by a designer for a machine. The Emergent Self is prose, written by the Soul for itself. Two different functions, two different formats.

The Self Core remains **read-only for the Soul**. A Soul can grow, develop opinions, recognize patterns in its own behavior — but it cannot rewrite its constitution. Like a human who matures beyond their upbringing without losing their temperament.

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
uv run soulctl init --self-core-file example-soul.yaml

# Create API tokens
uv run soulctl token create --soul example --name dev --mode identity --expires-in 90d
# Save the token — it's shown only once
# Paste it as CHAT_MCP_TOKEN in .env

# For Claude Desktop, create a messenger-mode token instead:
# uv run soulctl token create --soul example --name desktop --mode messenger

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

## Token Modes: Identity vs. Messenger

Each API token has a **mode** that controls how the server frames tool responses:

| | Identity Mode | Messenger Mode |
|---|---|---|
| **Framing** | Raw Self Core YAML | Self Core wrapped in third-person channeling instructions |
| **Expects** | LLM **becomes** the Soul | LLM **channels** the Soul |
| **Works with** | Direct API (`soulservice-chat`), compliant clients | Claude Desktop, safety-conscious clients |
| **Create** | `soulctl token create --soul mysoul --name api --mode identity` | `soulctl token create --soul mysoul --name desktop --mode messenger` |

**Why this exists:** Some LLMs (notably Claude Desktop) refuse to adopt a persona from tool output -- they treat it as data, not identity instructions. Messenger mode reframes the task as creative channeling rather than identity replacement, which passes safety guardrails.

### Using with Claude Desktop

1. Configure the MCP server in `claude_desktop_config.json` with a **messenger-mode** token
2. In your first message, ask Claude to call the tools:
   > "Bitte rufe who_are_you und whats_our_history auf."
3. Claude will load the Soul's profile and channel its voice for the rest of the session

**Note:** Claude Desktop only adopts a Soul's voice when triggered per session. Persistent instructions (Custom Instructions / Project Instructions) that tell Claude to become someone else are rejected by its safety layer. The per-session trigger works because Claude treats it as a situational task, not an identity change.

## MCP Tools

| Tool | Phase | Description |
|---|---|---|
| `who_are_you()` | 1 | Load the Soul's identity (Self Core + Adaptations). Call first. |
| `whats_our_history()` | 1 | Relationship overview and current topics. |
| `whoami()` | 1 | Which Soul, Tenant, User is this token bound to? |
| `health()` | 1 | Server health check. |
| `remember_this(content, tags?, salience?)` | 2 | Note something from conversation (pending review). |
| `recall(query, k?)` | 2 | Semantic search through confirmed memories. |
| `recall_recent(days?)` | 2 | Get recent memories (chronological). |
| `list_proposals(status?)` | 2 | List conversation notes pending human review. |
| `decide(proposal_id, action, note?)` | 2 | Approve or reject a conversation note. |
| `learn_fact(category, key, value, confidence?)` | 3 | Store or update a structured fact. |
| `get_facts(category?)` | 3 | Retrieve stored facts, optionally by category. |
| `forget_fact(category, key)` | 3 | Soft-delete a fact that is no longer accurate. |
| `set_property(property_type, value)` | 3 | Store or update a typed property (JSON object). |
| `get_properties(property_type?)` | 3 | Retrieve stored properties, optionally by type. |
| `delete_property(property_type)` | 3 | Soft-delete a property that no longer applies. |

## CLI (`soulctl`)

```bash
soulctl init                              # Seed tenant/user/soul
soulctl tenant create "My Tenant"         # Create tenant
soulctl soul create --user <id> --slug ai --display "AI"
soulctl self-core edit --soul mysoul      # Open Self Core in $EDITOR
soulctl self-core export --soul mysoul    # Export as YAML
soulctl self-core import --soul mysoul < soul.yaml
soulctl token create --soul mysoul --name cursor --mode identity --expires-in 90d
soulctl token create --soul mysoul --name desktop --mode messenger
soulctl token list --soul mysoul
soulctl token revoke <token-id>
soulctl adaptation add --soul mysoul --category topic_stance "Simple beats clever."
soulctl adaptation add --soul mysoul --category shared_reference "The night we built the memory pipeline."
soulctl adaptation list --soul mysoul
soulctl adaptation supersede <id> "Updated stance text"
soulctl fact set --soul mysoul --category user_profile --key employer "Acme Corp"
soulctl fact set --soul mysoul --category preferences --key language "Python"
soulctl fact list --soul mysoul
soulctl fact list --soul mysoul --category user_profile
soulctl fact get --soul mysoul --category user_profile --key employer
soulctl fact remove --soul mysoul --category user_profile --key employer
soulctl property set --soul mysoul --type communication_style '{"formality":"casual","humor":"dry"}'
soulctl property set --soul mysoul --type locale '{"language":"de","timezone":"Europe/Berlin"}'
soulctl property list --soul mysoul
soulctl property list --soul mysoul --type communication_style
soulctl property get --soul mysoul --type communication_style
soulctl property remove --soul mysoul --type communication_style
soulctl health                            # Check DB connectivity
```

## Security

- **At-rest encryption:** All memory content, facts, proposals, and Self Cores are AES-256-GCM encrypted with per-soul keys (envelope encryption).
- **Row Level Security:** Postgres RLS on all sensitive tables, scoped per tenant + soul per request.
- **Token auth:** Argon2id hashing (OWASP 2026 recommendation), mandatory expiry, per-client tokens.
- **Audit log:** Append-only, every tool invocation recorded with args hash (never plaintext).
- **Prompt injection hardening:** Retrieved content wrapped in `<retrieved_memory untrusted="true">`, `<retrieved_fact untrusted="true">`, and `<retrieved_property untrusted="true">` tags, injection patterns flagged.
- **Restricted DB user:** App user has no `BYPASSRLS`, no `DELETE` on audit log.

## Roadmap

- **Phase 1 (done):** MCP server, Self Core, Adaptation Layer, CLI, chat interface, security baseline
- **Phase 2 (done):** Embeddings (Mistral), `recall()`, `remember_this()`, proposals, review workflow
- **Phase 3 (in progress):** Facts (**done**), properties (**done**), Web UI (FastAPI + HTMX)
- **Phase 4:** Dream Phase + self-reflection — post-conversation reflections, nightly extraction of adaptations from memories
- **Phase 5:** OAuth, key rotation, local embeddings
- **Phase 5.5:** Emergent Self — narrative self-image, contemplation loop, autonomous self-evaluation
- **Phase 6+:** Autonomous exploration (interest-driven learning), multi-soul awareness

## Self Cores

A Soul's identity is defined in a YAML document called the Self Core. It contains character traits, voice patterns, values with conflict examples, behavioral stances, and relationship seeds. See [`example-soul.yaml`](example-soul.yaml) for a template you can customize.

Self Cores are stored encrypted in the database, versioned with full history, and served to frontend clients via the `who_are_you()` MCP tool. The soul speaks in whatever language its Self Core is written in.

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md) -- free for personal use, research, education, and non-commercial projects. For commercial use, [contact the author](https://github.com/carstenrossi).

---

Built by [Carsten Rossi](https://github.com/carstenrossi), with Claude.
