# Attestation

| Field | Value |
|---|---|
| **Full name** | Navya Paluri |
| **Email address** | palurinavya19@gmail.com |
| **Assignment title** | AI-Assisted Audit Log Service |
| **Date started** | 2026-09-18 |
| **Date submitted** | 2026-09-21 |

I, Navya Paluri, attest that this submission is my own individual work, completed on my own machine and accounts, and that it honestly reflects my development process and use of AI.

---

## AI assistance disclosure

I used Claude (Anthropic) as a coding assistant during development. Specifically:

- **Scaffolding:** initial project structure, boilerplate wiring (FastAPI lifespan, SQLAlchemy async session factory, Alembic setup)
- **Iteration:** reviewing draft implementations, catching edge cases, suggesting test scenarios
- **Documentation:** drafting README sections and ADR prose, which I reviewed and edited

AI was not used to substitute for design judgment. The core choices — hashing `payload_hash` instead of raw payload JSON to enable chain-safe redaction, cursor-based pagination over offset, soft-delete archival, `pg_advisory_xact_lock` for concurrent-append serialisation — were made by me and are explained in [`DECISIONS.md`](DECISIONS.md).

A full log of AI interactions is in [`AI_LOG.md`](AI_LOG.md).
