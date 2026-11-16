# Configuration

Every configurable value in this repository comes from **one file: `.env` at the repository root.**
There is no second location, no per-service env file, and no per-stack override file.

This page explains how that one file reaches four very different consumers, why it lives at the
root and not somewhere tidier, and what you have to touch when you add a new variable.

## The three files

| File | Committed | Purpose |
|---|---|---|
| `.env.minimal` | Yes | What a first run needs. One variable. Copy it to `.env` and set your key. |
| `.env.example` | Yes | The complete surface — every variable, grouped, with defaults and commentary. Reference material, not a starting point. |
| `.env` | **No** (`.gitignore`) | Yours. Created by copying one of the above. |

```bash
cp .env.minimal .env      # then set OPENAI_API_KEY
```

The file must be named exactly `.env` and must sit at the repository root. Docker Compose only
auto-loads a file with that name from the project directory — anything else requires passing
`--env-file` on every single `docker compose` invocation, which would break the plain
`docker compose up` that the quick start advertises.

## How one file reaches four consumers

```mermaid
%%{init: {'theme':'base', 'themeVariables': {
  'primaryColor': '#2563eb','primaryTextColor': '#ffffff','primaryBorderColor': '#1e40af',
  'lineColor': '#64748b','secondaryColor': '#f59e0b','tertiaryColor': '#10b981',
  'background': 'transparent'}}}%%
flowchart TB
  classDef core     fill:#2563eb,stroke:#1e40af,color:#ffffff
  classDef external fill:#f59e0b,stroke:#b45309,color:#000000
  classDef success  fill:#10b981,stroke:#047857,color:#ffffff
  classDef infra    fill:#64748b,stroke:#334155,color:#ffffff

  ENV[".env<br/>repository root"]

  COMPOSE["Docker Compose<br/>variable interpolation"]
  PYD["Pydantic Settings<br/>shared/config.py"]

  CONTAINERS["Containers<br/>orchestrator, agents, MCP, frontend"]
  HOSTPY["Host-run Python<br/>uvicorn, seed, evals"]
  HOSTWEB["Host-run frontend<br/>pnpm dev"]
  HOSTNET["Host-run .NET<br/>dotnet run"]

  ENV --> COMPOSE
  ENV --> PYD
  COMPOSE -->|"environment: blocks"| CONTAINERS
  PYD --> HOSTPY
  HOSTWEB -.->|"does not read .env<br/>falls back to a hardcoded default"| ENV
  HOSTNET -.->|"does not read .env<br/>needs exported vars"| ENV

  class ENV success
  class COMPOSE,PYD core
  class CONTAINERS,HOSTPY infra
  class HOSTWEB,HOSTNET external
```

### 1. Docker Compose — variable interpolation

Compose reads `.env` from the project directory and uses it to expand `${VAR}` references **inside
the compose file**. It does not inject the file into containers.

```yaml
environment:
  OPENAI_API_KEY: ${OPENAI_API_KEY:-}
  LLM_MODEL: ${LLM_MODEL:-gpt-4.1}
```

**A container only receives what its `environment:` block names.** A variable you add to `.env`
but not to the compose file will be silently absent inside every container — this is the single
most common configuration mistake in this repo, and it produces a default-valued setting rather
than an error.

Most of the agent services inherit one shared block via a YAML anchor:

```yaml
environment: &agent-env     # declared on the orchestrator
  ...

environment:
  <<: *agent-env            # every specialist merges it
  OTEL_SERVICE_NAME: ecommerce.product-discovery
```

So a variable added to `&agent-env` reaches the orchestrator and all five specialists at once. The
MCP servers and `auth-server` have their own blocks and do not inherit it.

### 2. Pydantic Settings — host-run Python

`shared/config.py` resolves the same file by **absolute path**, computed from the location of
`config.py` itself rather than from the current working directory:

```python
_REPO_ROOT = _resolve_repo_root(Path(__file__))
_ENV_FILE = _REPO_ROOT / ".env"
```

This is why `cd agents/python && uv run uvicorn product_discovery.main:app` picks up the root
`.env` even though the working directory is two levels down. It is deliberate, and it carries two
fixed bugs worth knowing about, both documented in the source:

- The repo root is **three** levels above `config.py`, not two. An earlier `parents[2]` resolved to
  `<repo>/agents`, which contains no `.env`, so Pydantic's `env_file` loading never fired and every
  setting silently fell back to its default even with a real `.env` present.
- Inside the Docker image, `config.py` is copied flatly to `/app/shared/config.py` — the build
  context is `./agents/python`, so that path depth does not exist. `parents[3]` raised `IndexError`
  and crashed every container at import time. `_resolve_repo_root` falls back to the immediate
  parent there, which contains no `.env` — correct, because containers get their config from the
  compose `environment:` block.

**Do not change how this path is resolved without re-reading those comments.**

### 3. The frontend — does not read `.env`

Next.js reads `web/.env.local`, not the repository root, so `pnpm dev` outside Docker sees nothing
from the root `.env`. It does not need to: `ORCHESTRATOR_URL` has a fallback that matches the
compose default.

```ts
// web/src/app/api/[...path]/route.ts
return (process.env.ORCHESTRATOR_URL ?? "http://localhost:8080").replace(/\/+$/, "");
```

So `cd web && pnpm dev` works with no configuration at all, as long as the orchestrator is on
`:8080`. Create `web/.env.local` only if you need it somewhere else.

**The browser never calls the orchestrator directly.** It calls the frontend's own origin, and
`web/src/app/api/[...path]/route.ts` forwards `/api/*` to `ORCHESTRATOR_URL`. That variable is
server-side and read per request, so changing it means restarting the container, not rebuilding the
image — and the orchestrator needs no public ingress and no CORS configuration.

This used to be `NEXT_PUBLIC_API_URL`, which Next **inlines at build time**. Two consequences are
recorded elsewhere in this repo and both are now gone: a second `next dev` started against a warm
build directory served the *first* one's API URL, and a cloud deployment could not know its own API
URL before the infrastructure that assigns it existed. `NEXT_PUBLIC_API_URL` is still honoured by
`web/src/lib/api.ts` as an escape hatch for calling an orchestrator directly, which works and
brings CORS back with it.

### Browser-facing edge: CORS, client address, rate limits

- **CORS.** `CORS_ALLOWED_ORIGINS` (comma-separated, default `http://localhost:3000`) on both
  orchestrators; credentials are never allowed (auth is a Bearer header). The web app goes through
  its same-origin proxy and needs nothing; set it only when a browser calls the orchestrator
  directly (`NEXT_PUBLIC_API_URL`). `curl`, scripts, evals and Playwright's `request` are not
  subject to CORS.
- **Auth rate limits.** Signup, login and refresh have their own buckets, separate from chat, in two
  layers so one caller cannot starve the others. The tight budget, `RATE_LIMIT_AUTH_MAX_REQUESTS` (10)
  per `RATE_LIMIT_AUTH_WINDOW_SECONDS` (60), is per **(client address, target email)** for login and
  signup — one target cannot be brute-forced, yet a script hammering one account leaves every other
  account's login alone — and per **user** for refresh, read from the verified token (an invalid or
  expired token never reaches it; opaque OAuth-mode tokens are keyed by their own digest). Over all
  three routes sits a much looser address-only ceiling, `RATE_LIMIT_AUTH_IP_MAX_REQUESTS` (300 per
  window), which is what actually bounds a single flooding source. Emails and subjects are hashed
  before they become Redis keys and are never logged. Chat, `/api/orchestration/compare` and
  `/resume` use the chat bucket. All fail open (with an error log) when Redis is down. The web client
  treats only a 401/403 from refresh as a dead session; a 429, 5xx or network error keeps the user
  signed in and surfaces a retryable error. Local Playwright runs log in repeatedly as a few accounts
  from one address, so raise both limits (e.g. 1000 and 10000) in `.env` before running them.
- **Client address.** The orchestrator takes the entry `RATE_LIMIT_TRUSTED_PROXY_HOPS` (default 1)
  places from the **right** of `X-Forwarded-For`; anything to its left is caller-supplied and ignored.
  The Next proxy writes that entry. **Limitation:** a Next 16 route handler cannot know the client
  address — `NextRequest.ip` was removed in 15 and the socket is not exposed; Next only fills
  `X-Forwarded-For` from the socket when the header is absent, so a browser-supplied value is
  indistinguishable from a real one (and behind Docker's published port the socket is the bridge
  gateway anyway). The proxy therefore strips the browser's `X-Forwarded-For`/`X-Real-IP`/`Forwarded`
  and forwards an address only when `WEB_TRUSTED_PROXY_HOPS` (default 0) says that many proxies you
  run sit in front of Next and append to the header. With the default, no address is forwarded and
  the orchestrator sees the frontend container: **all anonymous callers share one address-level auth
  ceiling and one chat bucket** (the per-account and per-user auth budgets are unaffected). Behind a load balancer or ingress that appends the client address, set
  `WEB_TRUSTED_PROXY_HOPS=1` (the orchestrator's default of 1 then needs no change). An orchestrator
  port reachable directly by clients (it is loopback-bound in compose) can have its header forged.

### 4. The .NET stack — containers yes, host no

`docker-compose.dotnet.yml` interpolates from the same root `.env` in exactly the same way, so the
containerised .NET stack needs no separate configuration.

Running .NET on the host (`dotnet run`) reads environment variables and `appsettings.json` — not
`.env`. Export what you need first:

```bash
set -a && source .env && set +a      # bash/zsh
dotnet run --project agents/dotnet/src/ECommerceAgents.Orchestrator
```

## Choosing the OpenAI wire API (`LLM_API`)

`LLM_PROVIDER=openai` talks to the endpoint in `LLM_BASE_URL` (default: api.openai.com). By default
the Python stack uses OpenAI's **Responses API** (`POST /responses`). Many OpenAI-compatible
servers implement only **Chat Completions** (`POST /chat/completions`) and answer `/responses`
with a 404. Set:

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/   # e.g. Gemini
LLM_API=chat_completions
```

| `LLM_API` | Client | Request path | Notes |
|---|---|---|---|
| `responses` (default) | `OpenAIChatClient` | `/responses` | Unchanged behaviour; replay fixtures are not affected |
| `chat_completions` | `OpenAIChatCompletionClient` | `/chat/completions` | For endpoints with no Responses route |

It applies to `LLM_PROVIDER=openai` only — `azure` is always Chat Completions, and `replay` serves
recorded fixtures. With `RECORD=true`, the recorder uses the same switch, so a fixture recorded
against a Chat-Completions-only endpoint is requested the way that endpoint accepts. The .NET
stack always uses Chat Completions and has no such setting. All three compose files forward
`LLM_BASE_URL` to the agents, and the two that run the Python stack forward `LLM_API` as well, so
the setting works the same in a container as it does for host-run Python.

## One stack at a time

The Python and .NET stacks publish the same host ports — 3000, 5432, 6379, 8080–8085, 8090, 18888 —
so **only one runs at a time**. This is deliberate rather than a limitation waiting to be fixed.

Running both simultaneously would need a second published port set. It no longer needs a second
frontend build — one image addresses either orchestrator through `ORCHESTRATOR_URL` — but the port
collision is reason enough for a case that does not arise in normal use: you compare the stacks by
switching, not by running both.

Each stack is its own Compose project (`e-commerce-agents`, `e-commerce-agents-dotnet`,
`e-commerce-agents-demo`), set explicitly with `name:` at the top of each file. Without that they
all inherit the directory name, share container names, and `docker compose down` with one file
leaves the other's containers running as orphans that only `docker rm -f` clears.

`dev.sh` / `dev.ps1` detect the other stack at startup and stop with the command to fix it:

```bash
./scripts/dev.sh --dotnet --switch      # tear the other stack down, volumes included, then start
./scripts/dev.ps1 -Dotnet -Switch       # PowerShell equivalent
```

`--switch` drops the other stack's volumes as part of the change. That is intentional: a switch
that leaves the old volume behind is how you end up running against a database seeded for the other
stack, which is the stale-schema failure the startup probe exists to catch.

## Adding a new variable

A new setting has to be declared in more than one place. This is the actual reason `.env.example`
runs to 210 lines — not the location of the file.

1. **`agents/python/shared/config.py`** — add the field to `Settings`, with a default and a comment
   explaining what it does and what happens when it is wrong.
2. **`.env.example`** — add it to the right group, with the default and the commentary.
3. **`docker-compose.yml`** — add it to the `environment:` block of every service that needs it, as
   `${VAR:-default}`. Use the `&agent-env` anchor when all agents need it.
4. **`docker-compose.dotnet.yml`** — the same, if the .NET stack honours it.
5. **The .NET side** — `AgentSettings` / `appsettings.json`, if it applies there.
6. **`docs/deployment.md`** — add a row to the environment table.

Steps 1 and 3 are the ones that break independently: a field added to `Settings` but not to the
compose block works perfectly on the host and silently uses the default in every container.

Do **not** add it to `.env.minimal`. That file exists to stay at one variable.

## What must never go in `.env`

`.env` is gitignored, but it is still a plaintext file on a development machine. Production secrets
belong in a secret manager — Key Vault, or your platform's equivalent — injected at runtime.

`shared/config.py` fails fast on weak or placeholder secrets when `ENVIRONMENT` is not
`development`, and warns loudly even when it is. The placeholders shipped in `.env.example` are
explicitly rejected outside development, so copying it verbatim into a deployed environment cannot
work by accident.

## Related

- [Quick Start](quick-start.md) — the fastest path to a running stack
- [Deployment](deployment.md) — the full environment variable table, per service
- [Security Guide](security-guide.md) — secret handling and the threat model
- [Releasing](releasing.md) — how versions and images are cut
