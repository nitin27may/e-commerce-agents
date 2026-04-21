# Implementation Plans

Sub-plans linked from the top-level master plan. Each file here is the detailed, reviewable plan for one unit of work — a chapter, a .NET port module, a refactor step, or a publishing task.

## Tracks

- **Tutorial chapters** — `tutorials/<chapter>/PLAN.md` (not under this directory; co-located with chapter code).
- **.NET port** — [`dotnet-port/`](./dotnet-port/) (7 sub-plans).
- **Refactor** — [`refactor/`](./refactor/) (13 sub-plans).
- **Publishing** — [`publishing/`](./publishing/) (1 sub-plan).

## Order of work

See the *Execution Phases* section of the master plan. At a glance:

1. **Phase 0** — scaffolding (this PR).
2. **Phases 1–5** — tutorial chapters, one PR per chapter, in dependency order.
3. **Phase 6** — .NET port, one PR per module (can run parallel to Phase 2+).
4. **Phase 7** — refactor, one PR per sub-plan in the order in `refactor/README.md`.
5. **Phase 8** — capstone polish.

Every PR ships green tests per the coverage floors in the master plan.
