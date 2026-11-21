# DevUI

A development-only, Python-only way to try one specialist or workflow without the orchestrator in front of it.
[MAF DevUI](https://learn.microsoft.com/agent-framework/) is Microsoft Agent Framework's local web harness: pick an
agent or workflow, chat with it, and watch the traces and tool calls live. `scripts/devui.py` registers this repo's five
specialists and two workflows in it.

```bash
./scripts/dev.sh --infra-only          # database and redis
./scripts/dev.sh --seed-only           # demo data the tools read
uv run --project agents/python --with agent-framework-devui==1.0.0b261112 python -m scripts.devui
# http://127.0.0.1:8090
```

Port 8090 is also the host port the compose `auth-server` publishes (the `agents` profile). With that stack running,
add `--port 8091` (any free port) to the command.

What you get: `product-discovery`, `order-management`, `pricing-promotions`, `review-sentiment`,
`inventory-fulfillment`, and the `pre-purchase` and `return-and-replace` workflows with their real tools.
Browsing and the workflows need no model key (the return workflow makes no model call); chatting with an agent does.

## Why `--with`, not a dependency

DevUI requires `fastapi<0.163`. Adding it to `agents/python`'s lock (as an extra or a dev dependency) downgrades the
orchestrator's production FastAPI to satisfy a development tool, because the lock is shared. `uv run --with` adds it to
the run only and touches neither `pyproject.toml` nor `uv.lock`. The version is the one `tutorials/pyproject.toml`
pins; Dependabot bumps that file, so move this command and the script's message with it.

## Identity

The tools read who is asking from ContextVars, which the orchestrator sets from each request. DevUI has no such request,
so one identity is bound for the whole process:

| Variable | Default | |
|---|---|---|
| `DEVUI_USER_EMAIL` | `alice.johnson@gmail.com` | a seeded customer |
| `DEVUI_USER_ROLE` | `customer` | `customer`, `power_user`, `seller`, `support` or `admin` |

## Gotchas

- **The identity is real.** With `admin` the tools can do what an admin can, to the database in `DATABASE_URL`. Use a
  database you can throw away.
- **Loopback only by default.** A non-loopback `--host` is refused unless `DEVUI_AUTH_TOKEN` is set, because the
  entities run real tools as the bound identity.
- **One identity per process.** To act as someone else, restart with another `DEVUI_USER_*`.
- **The script drives uvicorn itself** rather than calling `devui.serve()`: the asyncpg pool belongs to the event loop
  that created it, so the pool and the server have to share one.
- **No .NET equivalent.** Recorded in the parity matrix (row 42).
