# Contributing

Thanks for your interest. This is a showcase repo for the Microsoft Agent
Framework (MAF) v1 multi-agent pattern; PRs that improve clarity, tests, or the
demo experience are welcome.

This page is enough to make a first contribution: what to install, how to run the tests
without an API key, what a PR must pass, and where to find a first issue.

## Prerequisites

| Tool | Version | Needed for |
|------|---------|------------|
| Docker with Compose v2 | 24+ | The full stack, and the Postgres containers the Python integration tests start |
| `uv` | latest | Everything Python (3.12) |
| Node.js and `pnpm` | 22+ and 9+ | The frontend |
| .NET SDK | 10.0 | Only if you touch `agents/dotnet/` or a tutorial's .NET half |

No API key is needed to build, test or open a PR.

## Setup

```bash
./scripts/dev.sh            # full stack (Postgres + Redis + Aspire + agents + web)
./scripts/dev.ps1           # same, for PowerShell (Windows, or pwsh 7 on macOS/Linux)
# or piecemeal:
cd agents/python && uv sync --extra dev      # Python (uv, not pip/poetry)
cd web && pnpm install                        # frontend (pnpm, not npm/yarn)
```

Tests and the eval `smoke` suite run with no key on `LLM_PROVIDER=replay` (committed
recordings of real model responses, available on both stacks). A real `OPENAI_API_KEY`
(or `AZURE_OPENAI_*`) in `.env` is only needed for live runs against a model.

## Run the tests without an API key

```bash
cd agents/python && uv sync --all-packages --extra dev && uv run pytest   # Python
uv run --project tutorials pytest tutorials/ -m "not integration"         # tutorials
cd web && pnpm test                                                       # frontend
cd agents/dotnet && dotnet test ECommerceAgents.sln                       # .NET
docker compose up -d db redis && scripts/replay-evals.sh --setup          # eval suites, replayed
```

None of these calls a model. [`docs/replay-mode.md`](docs/replay-mode.md) explains how the
recordings work and when one has to be re-made.

## Conventions

The highlights are below. [`CLAUDE.md`](./CLAUDE.md) holds the detailed architecture
notes: it is written for the AI coding assistant used on this repo, and it is useful to
people too when you need to know why a module is shaped the way it is.

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
- Security scanning runs on its own in CI: CodeQL (`codeql.yml`) and a gitleaks secret scan
  (`secret-scan.yml`, allowlist in `.gitleaks.toml`). A secret-scan hit on a genuine test
  fixture is allowlisted in `.gitleaks.toml`, not ignored. A dependency-review workflow
  needs the repository's dependency graph and Dependabot alerts enabled, so it is not added yet.

Landing Dependabot pull requests, resolving lockfile and changelog conflicts, and cleaning up
parallel worktrees are covered in [`docs/contributing-dependencies.md`](docs/contributing-dependencies.md).

## Good first issues

New here? Start with the issues labelled
[`good first issue`](https://github.com/nitin27may/e-commerce-agents/labels/good%20first%20issue).
Each one is scoped to a single area, names the files to start from, and says how to
check the result — all of it runs with no API key. Comment on the issue before you
start so two people do not pick up the same one.

## PRs

Checklist before you ask for review:

- [ ] Branched off `main`; the PR does one thing.
- [ ] Tests added or adjusted for the change.
- [ ] The definition-of-done commands above pass for the parts you touched.
- [ ] If you changed a prompt, a tool schema or a tool's output: the replay eval suite
      runs with zero fixture misses.
- [ ] Docs updated when behavior changes.
- [ ] The description says what changed and how you verified it (commands + results).
- [ ] CI (`.github/workflows/tests.yml`) is green.
