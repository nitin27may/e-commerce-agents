## What changed

<!-- What this does, and why. If it fixes an issue, "Fixes #N". -->

## How it was verified

<!-- Commands you ran and what they printed. "Tests pass" on its own is not
     verification — this repo has a documented history of changes that passed
     every test while being completely broken in a live run. -->

```
```

## Checklist

- [ ] Branched off `main` and kept focused to one concern
- [ ] Tests added or adjusted — every change ships with tests
- [ ] Docs updated if behaviour changed (`docs/` is the source for the site)
- [ ] Definition of done passes locally (CI gates every line; run what your change touches):

```bash
cd agents/python && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd web && pnpm lint && pnpm exec tsc --noEmit && pnpm test && pnpm build
cd agents/dotnet && dotnet test ECommerceAgents.sln
uv run --project tutorials pytest tutorials/ -m "not integration"
python3 scripts/build_docs_site.py --check
python3 scripts/check_tutorial_readmes.py --check
python3 scripts/check_tutorial_coverage.py --check
```

- [ ] Adding eval results for your own provider? Put the file under `agents/python/evals/community/`, run
      `python -m evals.community_validate <file>` and regenerate the scorecard
      (`agents/python/evals/community/README.md`)
- [ ] Changed a prompt, tool schema or tool output? Replay eval smoke re-run with zero
      fixture misses (recipe in `agents/python/evals/fixtures/README.md`)

## Stacks affected

<!-- Parity between Python and .NET is enforced by a dual-backend gate. If this
     lands on only one, say why. -->

- [ ] Python
- [ ] .NET
- [ ] Frontend
- [ ] Docs / tutorials only

## Exercised against a running stack?

- [ ] Yes — how: <!-- e.g. ./scripts/dev.sh --demo, then asked "..." -->
- [ ] No — unit-testable change only
