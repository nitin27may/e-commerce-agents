# Contributing

Thanks for your interest. This is a showcase repo for the Microsoft Agent
Framework (MAF) v1 multi-agent pattern; PRs that improve clarity, tests, or the
demo experience are welcome.

## Setup

```bash
./scripts/dev.sh            # full stack (Postgres + Redis + Aspire + agents + web)
./scripts/dev.ps1           # same, for PowerShell (Windows, or pwsh 7 on macOS/Linux)
# or piecemeal:
cd agents/python && uv sync --extra dev      # Python (uv, not pip/poetry)
cd web && pnpm install                        # frontend (pnpm, not npm/yarn)
```

Requires Docker, `uv`, `pnpm`, and a real `OPENAI_API_KEY` (or `AZURE_OPENAI_*`)
in `.env` — the LLM is never mocked.

## Conventions

Conventions live in [`CLAUDE.md`](./CLAUDE.md) (the canonical guide). Highlights:

- **Python**: type hints everywhere, `async`, `asyncpg` (no ORM), `httpx` (not
  `requests`), Pydantic Settings, ContextVars for request state, MAF `@tool`
  decorators, YAML prompts (no hardcoded prompt strings). Lint with `ruff`.
- **Frontend**: Next.js 16 App Router, Tailwind 4 + shadcn/ui, OKLCH **theme
  tokens** (never hardcoded slate/white — it breaks dark mode), Zod for runtime
  validation. Read `node_modules/next/dist/docs/` before touching framework code.
- Working artifacts (memory, rules, plans) live under the repo-local `.claude/`.

## Testing (a hard requirement)

Every change ships with tests.

- **Python**: `cd agents/python && uv run pytest`. Integration tests use
  **testcontainers** (real Postgres) and **never mock the LLM**. CI enforces
  coverage on the unit-testable surface via `.coveragerc.ci` (the full 70% bar is
  in `pyproject.toml` for local/integration runs).
- **Frontend**: `cd web && pnpm test` (vitest) + `pnpm exec playwright test`
  (E2E; `ui-smoke.spec.ts` is backend-free).

## Definition of done (run before opening a PR)

```bash
# Python
cd agents/python && uv run ruff check . && uv run ruff format --check . && uv run pytest
# Frontend
cd web && pnpm lint && pnpm exec tsc --noEmit && pnpm test && pnpm build
```

## PRs

- Branch off `main`; keep PRs focused.
- Describe what changed and how you verified it (commands + results).
- Update docs when behavior changes; add/adjust tests.
- CI (`.github/workflows/tests.yml`) must be green.
