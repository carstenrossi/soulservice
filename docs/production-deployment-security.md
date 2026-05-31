# Production Deployment Security Essentials

Soulservice stores highly sensitive, encrypted personal data. The application-level
controls (envelope encryption, RLS, Argon2id token hashing, append-only audit log,
rate limiting) are necessary but **not sufficient** on their own. This document lists
the deployment- and infrastructure-level measures that MUST be in place before real
sensitive data is stored in a production environment.

Each item states the risk, the current state in this repo, and the required action.

> Scope note: application-code hardening that is tracked separately (per-tool scope
> enforcement, AES-GCM associated data) is covered by its own implementation plan and
> is intentionally not duplicated here.

---

## 1. TLS / transport encryption (blocker)

**Risk:** Without TLS, Bearer tokens and decrypted Soul content travel in plaintext.
Anyone on the network path (Wi-Fi, ISP, intermediate hops) can read or tamper with
them. This is the single most exposed surface for a remote deployment.

**Current state:** The MCP server runs plain `uvicorn` on `0.0.0.0:8000`
([src/soulservice/mcp/server.py](../src/soulservice/mcp/server.py)), published as
`6001` in [docker-compose.yml](../docker-compose.yml). No TLS anywhere. The README
claims "MCP over HTTPS", but nothing terminates TLS.

**Required action:**
- Put a TLS-terminating reverse proxy in front of the MCP server (Caddy, nginx,
  Traefik) or use a managed load balancer that terminates TLS.
- Use certificates from a trusted CA (Let's Encrypt is free and automatable with Caddy
  or Traefik).
- Redirect/refuse plain HTTP; serve only HTTPS.
- Keep the `uvicorn` app bound to a private interface/network so it is only reachable
  through the proxy, never directly.
- Enforce a modern TLS baseline (TLS 1.2+; prefer 1.3) and HSTS at the proxy.

**Acceptance:** External clients can only connect via HTTPS; a plain-HTTP request is
refused or redirected; the app port is not directly reachable from the internet.

---

## 2. Master key management (blocker)

**Risk:** A single static master key decrypts every per-Soul DEK and therefore every
record. If the key leaks, all data is compromised. Storing it in a plaintext `.env`
on the same host as the database means one host compromise exposes both ciphertext
and key.

**Current state:** `SOULSERVICE_MASTER_KEY` is read from the environment / `.env`
([src/soulservice/core/config.py](../src/soulservice/core/config.py),
[src/soulservice/core/crypto.py](../src/soulservice/core/crypto.py)). No KMS, no
rotation, no separation from the data host.

**Required action:**
- Store the master key in a dedicated secrets manager / KMS (AWS KMS, GCP KMS,
  Azure Key Vault, or HashiCorp Vault). Prefer a KMS that can wrap/unwrap the key so
  the raw key never lands on disk.
- Inject the key into the process at runtime (memory only); never write it to a
  plaintext file on the data host.
- Define and document a **key rotation** procedure. Because DEKs are encrypted by the
  master key, rotation means re-wrapping each DEK with the new master key (the bulk
  per-Soul content does not need re-encryption).
- Separate trust zones: the host/role that can read the master key should not be the
  same as the one holding database backups.

**Acceptance:** The raw master key is not present in any file on the data host;
rotation is a documented, tested procedure.

---

## 3. Least-privilege database credentials

**Risk:** Running the app or migrations as a superuser owner means a single
compromised connection has full database control and bypasses RLS.

**Current state:** The owner role `soulservice` is a **superuser with BYPASSRLS**
(verified via `pg_roles`). The RLS-owner fix introduced a restricted runtime role
`soulservice_app`, but the owner is still a superuser and the app/migrate dev
passwords are hardcoded placeholders in [infra/init.sql](../infra/init.sql).

**Required action:**
- Use three distinct roles with three distinct, strong, externally-managed passwords:
  - `soulservice_migrate` (DDL only, used by Alembic),
  - `soulservice_app` (runtime DML, non-owner, subject to RLS),
  - an owner/admin role used only for break-glass admin tasks.
- Do not run the application runtime as a superuser or as the table owner.
- Override the dev passwords (`soulservice_app_pw`, `soulservice_migrate_pw`) with real
  secrets sourced from the secrets manager; never rely on the init.sql defaults.
- Restrict network access to Postgres to the app/migrate hosts only.

**Acceptance:** The runtime connection is a non-superuser, non-owner role; passwords
come from the secrets manager; Postgres is not reachable from untrusted networks.

---

## 4. DEK cache exposure

**Risk:** Decrypted DEKs are cached in process memory (1h TTL,
[src/soulservice/core/crypto.py](../src/soulservice/core/crypto.py)). A memory dump or
process compromise exposes the cached DEKs. Python cannot reliably zero secret bytes.

**Required action / accept consciously:**
- Treat the application process memory as sensitive; restrict who can attach a debugger
  or read process memory on the host.
- Consider a shorter `dek_cache_ttl_seconds` for highly sensitive deployments (trade-off
  against master-key decrypt frequency).
- Ensure crash dumps / core dumps are disabled or access-restricted on production hosts.

**Acceptance:** Core dumps are disabled or protected; TTL is set deliberately for the
data sensitivity level.

---

## 5. Rate limiting across instances

**Risk:** The rate limiter is in-memory per process
([src/soulservice/core/ratelimit.py](../src/soulservice/core/ratelimit.py)). With more
than one server instance behind a load balancer, the limits no longer hold globally,
weakening brute-force and abuse protection.

**Required action:**
- For multi-instance deployments, back the rate limiter with a shared store (e.g.,
  Redis) so limits are enforced globally.
- Keep per-token limits aligned with expected legitimate usage.

**Acceptance:** Rate limits are enforced consistently regardless of instance count.

---

## 6. Backups and disaster recovery

**Risk:** Database backups contain ciphertext (good), but are useless or dangerous if
mishandled: a backup bundled with the master key defeats encryption; a backup without a
recoverable key path means permanent data loss.

**Required action:**
- Encrypt backups at rest and in transit; store them in a separate trust zone from the
  master key.
- Never include `SOULSERVICE_MASTER_KEY` or `.env` in database backups.
- Test restore procedures regularly, including the key-availability path.
- Define retention and secure deletion policies.

**Acceptance:** Restores are tested; backups and the master key are stored separately;
no secrets are present in backup artifacts.

---

## 7. Secrets hygiene and logging

**Risk:** Accidental logging of tokens, decrypted content, or key material turns logs
into a data-leak vector.

**Current state:** A `gitleaks` pre-commit hook and `.gitignore` for `.env*` are in
place. Code review must keep secrets out of logs.

**Required action:**
- Never log token values, decrypted content, DEKs, or the master key (not even
  partially). Keep audit-log entries metadata-only (tool name, sizes, hashes), as the
  current `audit_log` design does.
- Scrub or restrict access to application logs; treat them as sensitive.
- Keep dependency supply chain pinned (`uv.lock`) and patched.

**Acceptance:** No secret values appear in any log; log access is restricted.

---

## Pre-production checklist

- [ ] TLS terminates in front of the MCP server; app port not publicly reachable
- [ ] Master key in KMS/Vault, never in a plaintext file on the data host
- [ ] Documented and tested key rotation procedure
- [ ] Runtime DB role is non-superuser, non-owner; real passwords from secrets manager
- [ ] Postgres reachable only from app/migrate hosts
- [ ] Core dumps disabled/protected; DEK cache TTL set deliberately
- [ ] Shared-store rate limiting if running multiple instances
- [ ] Backups encrypted, key stored separately, restore tested
- [ ] No secrets in logs; log access restricted
