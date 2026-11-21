# Chapter Plan Template

> Fill this in while planning a new chapter, in the issue or pull request that adds it. Chapters do
> not ship a `PLAN.md` of their own; the chapter's `README.md` is the lasting artifact.

## Goal

<One sentence: the MAF concept this chapter teaches.>

## Teaching strategy

- [ ] Tour code the application already runs (name the file and the symbol)
- [ ] Standalone example of a pattern the application uses
- [ ] Standalone example of a pattern the application does not have (say so in the capstone section)

## Deliverables

### `tutorials/<NN-slug>/python/`

- `main.py`: a minimal runnable example. It builds its chat client through
  `tutorials/_shared/llm.py::build_chat_client`, so it runs with no key on recorded fixtures.
- `tests/test_<name>.py`: at least three tests (happy path, edge case, and one that proves the
  concept was exercised). Tests that call a live model are marked `@pytest.mark.integration`.
- `tests/fixtures/replay/*.json`: the recorded model responses the keyless run and the replay tests
  use. Record them with `LLM_PROVIDER=replay RECORD=true` and a key (see the docstring of
  `tutorials/_shared/replay_client.py`), and commit them. A fixture is keyed by the exact request,
  so re-record after changing a prompt, a tool schema or a tool's output.
- No `pyproject.toml`. Every chapter uses the one shared project, `tutorials/pyproject.toml`; add a
  dependency there if the chapter needs one. Framework versions are pinned in that file to the same
  versions `agents/python/pyproject.toml` uses, so do not pin anything per chapter.

### `tutorials/<NN-slug>/dotnet/`

- `<Project>.csproj`: references packages by name only. Versions come from
  `tutorials/Directory.Packages.props`, which pins the same stable `Microsoft.Agents.AI` line as
  `agents/dotnet/Directory.Packages.props`. No prerelease pins.
- `Program.cs`: the same example as `main.py`. `dotnet run` calls a live model and needs a key.
- `tests/<Project>.Tests.csproj` and `tests/<Project>Tests.cs`: xUnit and FluentAssertions. Tests
  that involve a model run against the scripted chat client, linked (not copied) from
  `tutorials/_shared/dotnet/ScriptedChatClient.cs`, so `dotnet test` needs no key and makes no
  network call.

### `tutorials/<NN-slug>/README.md`

- Start from `tutorials/_template/README.md` and keep its section headings;
  `scripts/check_tutorial_readmes.py` checks for them.
- The capstone section points into the application by path plus symbol, not by line number.

## Wiring the chapter in

- `python3 scripts/check_tutorial_nav.py --write` regenerates the previous/next footers.
- `python3 scripts/check_tutorial_coverage.py --write` regenerates the status table in
  `tutorials/README.md`.
- Add the chapter to a learning path in `tutorials/README.md` and to `TIERS` in
  `scripts/build_docs_site.py` in the same commit.
- If `main.py` cannot run without a key, add the chapter to `SKIP` in
  `scripts/run_tutorials_keyless.py` with the reason.

## Verification

Run from the repo root. None of these needs a key.

- `uv run --project tutorials python tutorials/<NN-slug>/python/main.py` prints `<expected>`.
- `uv run --project tutorials pytest tutorials/<NN-slug>/python/tests -m "not integration"` passes.
- `dotnet test tutorials/<NN-slug>/dotnet/tests/<Project>.Tests.csproj` passes.
- `python3 scripts/check_tutorial_readmes.py --check`, `python3 scripts/check_tutorial_nav.py --check`,
  `python3 scripts/check_tutorial_coverage.py --check` and `python3 scripts/build_docs_site.py --check`
  pass.
- `uv run --project tutorials ruff check tutorials/` passes.

## How this maps into the capstone

<The file and the symbol where the application uses this pattern. If it does not use it, say that,
and name the nearest thing it does have.>

## Out of scope

- <Things readers might expect but that belong in a later chapter>
