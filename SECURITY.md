# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.1.x   | Yes |
| 1.0.x   | Security fixes only |
| < 1.0   | No |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting:
[Report a vulnerability](https://github.com/nitin27may/e-commerce-agents/security/advisories/new).
It is private between you and the maintainer until a fix is published.

Useful things to include: which stack (Python or .NET), which auth mode
(`AUTH_MODE=local` or `oauth`), whether MCP was enabled, and the smallest
reproduction you have.

Expect an acknowledgement within a few days. This is a personal open-source
project, not a funded product — there is no paid triage rota and no bounty.

## Scope

This is a **demonstration and teaching repository**. It is built to show how a
multi-agent system is structured, and it is not hardened for production use as
shipped. Some deliberate choices would be wrong in production and are not
vulnerabilities here:

- `.env.example` and `.env.minimal` ship placeholder secrets. `shared/config.py`
  rejects them whenever `ENVIRONMENT` is not `development`.
- The default `AUTH_MODE=local` issues its own JWTs with a shared secret between
  agents. `AUTH_MODE=oauth` is the realistic path.
- Guardrails default to observe-and-log (`GUARDRAILS_FAIL_OPEN=true`) rather than
  blocking, because false-positive rates have not been measured across
  environments. `GUARDRAILS_BLOCK_ON_INJECTION=true` turns blocking on.
- `docker-compose.yml` binds Postgres and Redis to localhost with well-known
  development credentials.

What **is** in scope: anything that breaks an invariant the code claims to hold.
Tenant or user isolation being bypassed, prompt injection defeating a control
that is switched on, an approval gate being skippable, an idempotency key not
preventing a double refund, or a `user_email` scoping check that can be evaded.

## Handling

Confirmed issues get a fix on `main`, a patch release, and a note in
[CHANGELOG.md](CHANGELOG.md). Credit is given unless you would rather not be
named.
