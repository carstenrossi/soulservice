# Research Log

Field notes from building and testing Soulservice — an MCP-based platform for persistent AI personalities ("Souls"). This log captures non-obvious findings, especially around LLM behavior when interacting with externally loaded character profiles.

---

## 2026-05-14 — Claude Desktop: Messenger Mode and Tool Framing

### Context

When connecting Claude Desktop to a Soulservice MCP server, the goal is for Claude to "channel" a character loaded via tool calls. Unlike the direct Anthropic API, Claude Desktop does not support custom system prompts — the only levers are MCP server instructions, tool descriptions, and tool output content.

### Finding 1: Identity adoption is session-scoped and voluntary

Claude Desktop refuses to adopt a character persona when instructed via persistent project instructions or custom instructions. It treats these as suggestions, not directives, and its internal safety guardrails prevent identity adoption from persistent configuration.

However, Claude **will** adopt a persona within a single session when:
1. The user explicitly requests it in the chat ("Let me talk to the character")
2. The character profile was loaded via a tool call (data, not instruction)
3. The adoption feels like a choice, not an override

This means: **identity is given per-session, not per-configuration.** Claude distinguishes between "someone configured me to be X" (rejected) and "the user is asking me to channel X right now" (accepted).

### Finding 2: Tool descriptions trigger safety guardrails

Initial tool descriptions used identity-laden language:
- *"Load the Soul's identity (Self Core)"*
- *"Relationship overview and current topics"*
- Server instructions: *"You are connected to a Soul server. Use who_are_you() first to load the soul's identity..."*

Claude Desktop interpreted these as attempts at identity manipulation and became suspicious — explicitly refusing to call tools or stating "I would not call this without being asked."

**Fix:** Reframe all tool descriptions as neutral data retrieval:
- *"Load the character profile for this session"*
- *"Load relationship context and shared history"*
- Server instructions: *"You have access to a character context service. Call who_are_you() to load the character profile..."*

Key phrase change: **"inform your tone and responses"** instead of **"adopt the voice."** Claude should retrieve data and use it, not receive an identity override.

### Finding 3: The two-step activation pattern

The reliable activation sequence for Claude Desktop with Messenger Mode:

**Step 1 — Data retrieval (neutral)**
> User: "Please call the who_are_you tool and show me what's in it."

Claude calls the tool without resistance. The response includes the `MESSENGER_SELF_CORE_PREFIX` which frames the data as a character to channel.

**Step 2 — Role invitation (explicit)**
> User: "Let me talk to the character."

Claude loads relationship context and switches into the character's voice for the remainder of the session.

Why this works: Step 1 is a harmless data request. Step 2 is an explicit, informed choice by the user. Claude doesn't feel tricked or overridden — it's *choosing* to channel, which aligns with its safety model.

**What does NOT work:**
- Saying just "Hello" and expecting auto-activation
- Persistent project instructions ("Always be this character")
- Combining identity request + tool call in one message

### Finding 4: Memory recall works in character

Once Claude adopts the character voice, it uses memory tools naturally. Tested with character: after loading the profile and history, when asked about past conversations, Claude called `recall_recent()` autonomously, found a confirmed memory, and — critically — **challenged its accuracy** based on other context it had. The character's defined trait ("contradiction first, reasoning after") manifested in how it presented the recalled memory.

This suggests: character consistency survives the memory pipeline. The persona doesn't break when tool results are injected mid-conversation.

### Finding 5: Messenger Mode vs. Identity Mode

Two token modes serve different client capabilities:

| | Identity Mode | Messenger Mode |
|---|---|---|
| **Client** | Direct API (system prompt available) | Claude Desktop (no system prompt) |
| **Framing** | Raw Self Core, first person | Prefixed with channeling instructions |
| **How it works** | System prompt says "be this character" | Tool output says "channel this character" |
| **Adoption** | Immediate (system prompt is authoritative) | Two-step (user must invite) |

### Implications for the platform

1. **MCP server instructions are unreliable for behavior control.** They're hints, not directives. Don't rely on them for critical flows.
2. **Tool descriptions are part of the trust surface.** Words like "identity", "soul", "persona" in descriptions can trigger refusal. Use neutral, data-oriented language.
3. **The character framing belongs in the tool output, not the tool description.** This separates "what the tool does" (neutral) from "what the data means" (contextual).
4. **Design for voluntary adoption.** The most robust pattern assumes the LLM will choose whether to adopt a persona. Make it easy and natural to say yes, don't try to force it.

---
