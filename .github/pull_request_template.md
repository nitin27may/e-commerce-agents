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
- [ ] Definition of done passes locally:

```bash
cd agents/python && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd web && pnpm lint && pnpm exec tsc --noEmit && pnpm test && pnpm build
```

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
