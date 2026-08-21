# Adding a Specialist Agent

This is a step-by-step checklist for adding a new specialist agent to the platform. Each specialist is an independent microservice that the orchestrator reaches via A2A.

Before starting, read [Architecture §3](architecture.md#3-agent-architecture) for the four-file structure every specialist follows, and [MAF Best Practices](maf-best-practices.md) for `@tool` patterns.

---

## 1. Scaffold the four-file module

Create a directory under `agents/python/` named after your agent (snake_case):

```
agents/python/your_agent/
├── __init__.py
├── agent.py      # create_your_agent() -> Agent
├── tools.py      # @tool-decorated async functions
├── prompts.py    # SYSTEM_PROMPT loaded from YAML
└── main.py       # A2AAgentHost entry point
```

### main.py

```python
from agent_framework_a2a import A2AAgentHost
from shared.telemetry import setup_telemetry
from shared.db import create_pool
from .agent import create_your_agent

app = A2AAgentHost(
    agent_factory=create_your_agent,
    host="0.0.0.0",
    port=8086,   # pick the next available port
).app

@app.on_event("startup")
async def startup():
    setup_telemetry(service_name="ecommerce.your-agent")
    await create_pool()
```

### agent.py

```python
from agent_framework import Agent
from shared.agent_factory import create_chat_client
from shared.context_providers import ECommerceContextProvider
from .tools import YOUR_TOOLS
from .prompts import SYSTEM_PROMPT

def create_your_agent() -> Agent:
    return Agent(
        client=create_chat_client(),
        system_prompt=SYSTEM_PROMPT,
        tools=YOUR_TOOLS,
        context_providers=[ECommerceContextProvider()],
    )
```

### tools.py

```python
from typing import Annotated
from agent_framework import tool
from shared.db import get_pool
from shared.context import current_user_email, current_user_role

@tool
async def my_tool(
    param: Annotated[str, "Description of the parameter"],
) -> dict:
    """One-line description shown to the LLM."""
    email = current_user_email.get()
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT ... FROM ... WHERE user_email = $1 AND ...",
            email,
        )
    return {"result": row}

YOUR_TOOLS = [my_tool]
```

Key rules (see [MAF Best Practices](maf-best-practices.md)):
- All tools must be `async`.
- Read user identity from ContextVars (`current_user_email`, `current_user_role`) — never accept it as a parameter.
- Use `Annotated` type hints for the `@tool` decorator. Do not use Pydantic input models.
- Parameterized SQL only — `$1, $2` syntax, no f-strings in queries.

### prompts.py

```python
from shared.prompt_loader import load_prompt

SYSTEM_PROMPT = load_prompt("your-agent")
```

---

## 2. Create the prompt YAML

Add `agents/python/config/prompts/your-agent.yaml`:

```yaml
role: |
  You are the Your Agent for E-Commerce Agents. You handle [domain].

instructions: |
  - Use my_tool to [do something].
  - Always scope queries to the authenticated user.
  - [Agent-specific rules]

tools:
  - name: my_tool
    when_to_use: "When the user asks about [topic]"
```

The `load_prompt()` function in `shared/prompt_loader.py` composes this with the shared grounding rules, schema context, and tool examples from `config/prompts/_shared/`.

---

## 3. Assign a port

Pick the next unused port from the port map (currently 8080–8085 are taken). Add it to:

- `agents/python/your_agent/main.py` — `port=8086`
- `docker-compose.yml` — new service entry (see step 4)
- `docs/deployment.md` port map table
- `README.md` Port Map section

---

## 4. Add the Docker Compose service

In `docker-compose.yml`, add under the `agents` profile:

```yaml
your-agent:
  build:
    context: ./agents/python
    args:
      AGENT_NAME: your_agent
      AGENT_PORT: "8086"
  ports:
    - "8086:8086"
  environment:
    - DATABASE_URL=${DATABASE_URL}
    - OPENAI_API_KEY=${OPENAI_API_KEY}
    - LLM_PROVIDER=${LLM_PROVIDER}
    - AGENT_SHARED_SECRET=${AGENT_SHARED_SECRET}
    - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT}
    - OTEL_SERVICE_NAME=ecommerce.your-agent
  depends_on:
    db:
      condition: service_healthy
    aspire:
      condition: service_started
  profiles: ["agents"]
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8086/health"]
    interval: 15s
    timeout: 5s
    retries: 3
    start_period: 30s
```

---

## 5. Register with the orchestrator

The orchestrator discovers specialists via the `AGENT_REGISTRY` environment variable — a JSON map of agent name to internal URL.

**In `docker-compose.yml`**, add your agent to the orchestrator's `AGENT_REGISTRY`:

```yaml
orchestrator:
  environment:
    AGENT_REGISTRY: >-
      {
        "product-discovery": "http://product-discovery:8081",
        "order-management": "http://order-management:8082",
        "pricing-promotions": "http://pricing-promotions:8083",
        "review-sentiment": "http://review-sentiment:8084",
        "inventory-fulfillment": "http://inventory-fulfillment:8085",
        "your-agent": "http://your-agent:8086"
      }
```

**In the orchestrator prompt YAML** (`config/prompts/orchestrator.yaml`), add a routing rule so the LLM knows when to call your agent:

```yaml
agents:
  - name: your-agent
    when_to_route: "When the user asks about [domain topic]"
    description: "Handles [what it handles]"
```

---

## 6. Write tests

Add `agents/python/tests/test_your_agent.py`. The existing specialist tests in `tests/` are the pattern to follow — they use `pytest-asyncio`, mock the DB pool, and assert tool outputs without calling the LLM.

```python
import pytest
from unittest.mock import AsyncMock, patch
from your_agent.tools import my_tool
from shared.context import current_user_email

@pytest.mark.asyncio
async def test_my_tool_returns_result():
    current_user_email.set("alice@example.com")
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {"id": "abc", "result": "value"}
    with patch("your_agent.tools.get_pool") as mock_pool:
        mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await my_tool(param="test")
    assert result["result"] == "value"
```

Run with:

```bash
cd agents/python && uv run pytest tests/test_your_agent.py -v
```

---

## 7. Verify end-to-end

```bash
# Build and start with your new agent
./scripts/dev.sh --clean             # PowerShell: ./scripts/dev.ps1 -Clean

# Confirm the agent is healthy
curl http://localhost:8086/health

# Send a test message that should route to your agent
curl -X POST http://localhost:8080/api/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "test message for your domain"}'

# Confirm routing in Aspire Dashboard
open http://localhost:18888
# Look for a trace with your agent name in the spans
```

---

## Gotchas

- **ContextVars reset between requests** — don't store user identity in module-level state; always read it fresh from `current_user_email.get()` per tool call.
- **Connection pool per agent** — each agent has its own pool (initialized in the lifespan). Don't share pools between processes.
- **Prompt YAML is loaded per-request** — `load_prompt()` re-reads the YAML on each call, making prompts hot-reloadable without restarts. This means YAML syntax errors surface as runtime errors, not startup failures.
- **Health endpoint is free** — `A2AAgentHost` registers `/health` automatically; you don't need to add it.
- **Agent names in `AGENT_REGISTRY` use hyphens** — the orchestrator prompt uses `your-agent` (hyphenated), while the Python module uses `your_agent` (underscored). These are different things; don't mix them.

---

## Related

- [`docs/architecture.md §3`](architecture.md#3-agent-architecture) — four-file agent structure diagram
- [`docs/maf-best-practices.md`](maf-best-practices.md) — `@tool`, middleware, prompt YAML, ContextVars patterns
- [`docs/agent-flows.md`](agent-flows.md) — how multi-agent collaboration works in practice
- [`docs/api-reference.md`](api-reference.md) — orchestrator REST API your agent integrates with
- [Project README](../README.md)
