"""Self-hosted OAuth2 Authorization Server (AUTH_MODE=oauth).

Issues RS256 access/refresh tokens for user login (orchestrator-brokered
Resource Owner Password Credentials), inter-agent A2A calls, and MCP
resource access (client credentials) — no external identity provider.
See ``docs/security-guide.md`` for the design; remaining OAuth work is in
``.claude/plans/remaining-work.md``.
"""

import os

# authlib refuses to build a request over a non-"secure" URI by default
# (its own definition: https:// or http://localhost) — see
# authlib.common.security.is_secure_transport. This platform runs every
# internal service, including this one, over plain HTTP within a private
# Docker/AKS network (no pod-to-pod TLS today — see the "Enable HTTPS
# everywhere" row in docs/security-guide.md's hardening checklist), so this
# is authlib's own documented escape hatch, not a workaround. Set at
# package import time so it applies uniformly whether this runs under
# uvicorn, docker-compose, or pytest.
os.environ.setdefault("AUTHLIB_INSECURE_TRANSPORT", "1")
