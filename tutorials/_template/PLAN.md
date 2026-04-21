# Chapter Plan Template

> Copy this file into a new `tutorials/<chapter>/PLAN.md` when scaffolding a chapter.

## Goal

<One sentence: the MAF concept this chapter teaches.>

## Article mapping

- **Supersedes / updates / references**: <link to existing article on nitinksingh.com, or "none">
- **New slug**: `/posts/maf-v1-<slug>/`

## Teaching strategy

- [ ] Refactor existing code in this repo (name the file + line range)
- [ ] Partial refactor + new example
- [ ] New mini-example (app doesn't have an equivalent)

## Deliverables

### `python/`
- `pyproject.toml` — pinned `agent-framework` version
- `main.py` — minimal runnable example (< 200 lines)
- `tests/test_main.py` — ≥ 3 tests (happy path, edge case, concept assertion)

### `dotnet/`
- `<Project>.csproj` — pinned `Microsoft.Agents.AI*` prerelease
- `Program.cs` (or `<Feature>.cs`) — equivalent example
- `tests/<Project>.Tests.csproj` — xUnit + FluentAssertions tests

### Article
- `README.md` filled from `tutorials/_template/README.md`
- Cover image: `img/posts/maf-v1-<slug>.jpg` (1200×630)
- Video demo recorded by author after code lands

## Verification

- `cd python && uv run python main.py` succeeds and prints `<expected>`.
- `cd dotnet && dotnet run` prints the same thing.
- `cd python && uv run pytest` passes.
- `cd dotnet && dotnet test` passes.
- Hugo front matter validates (`draft: true` until published).

## How this maps into the capstone

<Point at the file:line where this pattern is used in the live app. For brand-new concepts, name the sub-plan in `plans/refactor/` that will add the concept to the app.>

## Out of scope

- <Things readers might expect but that belong in a later chapter>
