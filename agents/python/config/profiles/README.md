# Run profiles

One file per provider setup. `./scripts/dev.sh --profile <name>` (or `-Profile` on PowerShell) applies a
profile, `uv run --project agents/python python -m scripts.doctor --profile <name>` checks it, and
`docs/run-profiles.md` is the table.

## Rules

- A profile sets **chat, embeddings and the model together**, so a stale value left in `.env` from an earlier
  provider cannot half-apply. It never holds a secret: keys stay in `.env` (or the shell).
- Precedence, high to low: a variable exported in your shell, then the profile, then `.env`, then the
  defaults in `shared/config.py`. Where a profile overrides a different value from `.env`, the tool says so.
- A value of `<set-me>` means "you must supply this". The profile leaves `.env` and the shell alone for that
  variable, and the tool refuses to continue if neither has a value.
- Lines are `KEY=VALUE`, nothing else on the line, no quotes. Header lines are `# @name: text`.
- Every key must exist in `Settings` and `.env.example`, and be passed through both compose files
  (`LLM_API` only in the Python one). `tests/test_run_profiles.py` enforces all of it, and that no secret-looking
  value is present.

## Header fields

`@tier`, `@summary`, `@key` (what you must supply), `@cost`, `@dotnet` (does the .NET stack support this
setup), `@verified` (how it has actually been exercised: say so when the answer is "it has not"),
`@evals` (which evals apply), `@judge` and `@judge-env` (a SUGGESTED judge, not calibrated; decision D-7 fixes
the reference judge in M3).
