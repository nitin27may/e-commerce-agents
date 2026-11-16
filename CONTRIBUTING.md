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

Requires Docker, `uv` and `pnpm`. Tests and the eval `smoke` suite run with no key on
`LLM_PROVIDER=replay` (committed recordings of real model responses, available on both
stacks). A real `OPENAI_API_KEY` (or `AZURE_OPENAI_*`) in `.env` is only needed for live
runs against a model.

## Conventions

Conventions live in [`CLAUDE.md`](./CLAUDE.md) (the canonical guide). Highlights:

- **Python**: type hints everywhere, `async`, `asyncpg` (no ORM), `httpx` (not
  `requests`), Pydantic Settings, ContextVars for request state, MAF `@tool`
  decorators, YAML prompts (no hardcoded prompt strings). Lint with `ruff`.
- **Frontend**: Next.js 16 App Router, Tailwind 4 + shadcn/ui, OKLCH **theme
  tokens** (never hardcoded slate/white — it breaks dark mode), Zod for runtime
  validation. Read `node_modules/next/dist/docs/` before touching framework code.
- Working artifacts (the consolidated plan, agent definitions) live under the repo-local `.claude/`.

## Testing (a hard requirement)

Every change ships with tests.

- **Python**: `cd agents/python && uv run pytest`. Integration tests use
  **testcontainers** (real Postgres); the LLM is not hand-mocked — tests use
  recorded `LLM_PROVIDER=replay` fixtures. CI enforces
  coverage on the unit-testable surface via `.coveragerc.ci` (the full 70% bar is
  in `pyproject.toml` for local/integration runs).
- **Frontend**: `cd web && pnpm test` (vitest) + `pnpm exec playwright test`
  (E2E; `ui-smoke.spec.ts` is backend-free).

## Definition of done (run before opening a PR)

CI gates all of this; run the parts your change touches.

```bash
# Python (CI: tests.yml)
cd agents/python && uv sync --all-packages --extra dev
cd agents/python && uv run ruff check . && uv run ruff format --check . && uv run pytest
# Frontend (CI: tests.yml)
cd web && pnpm lint && pnpm exec tsc --noEmit && pnpm test && pnpm build
# .NET (CI: tests.yml)
cd agents/dotnet && dotnet test ECommerceAgents.sln
# Tutorials (CI: tutorials.yml)
uv run --project tutorials pytest tutorials/ -m "not integration"
# Docs checks (CI: tutorials.yml, jekyll-gh-pages.yml)
python3 scripts/build_docs_site.py --check
python3 scripts/check_tutorial_readmes.py --check
python3 scripts/check_tutorial_coverage.py --check
```

- **Replay eval smoke** (CI: `evals.yml`, on PRs that touch `evals/`, `shared/`, the
  orchestrator or an agent): if you changed a prompt, a tool schema or a tool's output,
  run the recipe in
  [`agents/python/evals/fixtures/README.md`](agents/python/evals/fixtures/README.md)
  and confirm zero fixture misses. It needs no key.
- **UI smoke** (CI: `web-ui-smoke` in `tests.yml`): `cd web && pnpm exec playwright test
  e2e/ui-smoke.spec.ts e2e/not-found.spec.ts e2e/a11y.spec.ts` against `pnpm dev` or a
  production build. Backend-free. These three specs run on both the desktop and the
  `mobile-chromium` project (a Pixel 7 preset); the `a11y` spec fails on any
  serious/critical axe violation. The rest of `e2e/` runs on the desktop project only.
- Security scanning (CodeQL, dependency review) runs on its own in CI.

## Good first issues

New here? Start with the issues labelled
[`good first issue`](https://github.com/nitin27may/e-commerce-agents/labels/good%20first%20issue).
Each one is scoped to a single area, names the files to start from, and says how to
check the result — all of it runs with no API key. Comment on the issue before you
start so two people do not pick up the same one.

## PRs

- Branch off `main`; keep PRs focused.
- Describe what changed and how you verified it (commands + results).
- Update docs when behavior changes; add/adjust tests.
- CI (`.github/workflows/tests.yml`) must be green.
