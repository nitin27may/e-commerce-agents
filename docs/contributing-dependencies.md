# Dependency updates and parallel-lane hygiene

This page is for maintainers and anyone landing Dependabot pull requests. It records the policy that
is actually configured in [`.github/dependabot.yml`](../.github/dependabot.yml) (the file's own header
explains each choice), how to handle the awkward cases, and how to clean up after parallel work.

## The policy

Dependabot runs weekly, on Monday, for six ecosystems: `uv` (`/agents/python`, `/tutorials`), `npm`
(`/web`, pnpm), `nuget` (`/agents/dotnet`, `/tutorials`), `docker`, `docker-compose` and
`github-actions`.

| Kind of update | What Dependabot opens | What we do |
|---|---|---|
| Patch and minor | One grouped PR per ecosystem (`python-minor-patch`, `web-minor-patch`, `dotnet-minor-patch`, `docker-minor-patch`, `compose-minor-patch`, `actions`) | Merge when CI is green. No separate review beyond reading the changelog entries it lists. |
| Major | One PR per dependency, never grouped | One human-reviewed PR. Expect code changes, not just a lockfile edit. |
| MAF, MCP, A2A SDK, `httpx2` | The `maf` group (Python and .NET), never mixed into the routine PR | A deliberate step: gate on `scripts/replay-evals.sh`, possibly re-record fixtures, move Python, .NET and the tutorials together. |
| Runtime-coupled majors (`Microsoft.Extensions.*`, `Microsoft.AspNetCore.*`, `System.*`, the `python`, `node` and `dotnet/*` images) | Ignored on purpose | The runtime line is chosen with the code. Changing it is its own change. |

### Majors: one reviewed change each

A major bump is where breakage lives, so it is never folded into the weekly group and never merged on
green CI alone. Two examples from this repo:

- **Aspire 13 to 17.** The AppHost packages moved as a set, and Aspire 17 obsoletes
  `Aspire.AppHost.Sdk` and `AddProject<T>` (an error under warnings-as-errors). The AppHost had to
  switch SDK and call `AddDotnetProject`, and its tests had to assert the new resource type. Four
  separate Dependabot PRs could not each pass CI alone, so they were closed and superseded by one
  hand-made change.
- **`@types/node` 26 against a Node 20/22 runtime.** The types package versions with Node. Taking 26
  would type-check against APIs that a Node 20 or 22 runtime does not have (`web/package.json` pins
  `@types/node` to `^20`, and the Docker `node` major is ignored on purpose). A major here is declined (close the PR, or add an `ignore` entry with a comment saying
  why) until the runtime moves.

When several related majors arrive together, close the individual PRs with a pointer to the combined one.

### Merging a green group PR

Do not merge on a glance. `scripts/merge-when-green.sh <pr>` squash-merges only when the PR head is
unchanged since the script started, at least 20 checks exist and all are complete with none failing
(skipped is fine), and the PR is mergeable. It polls twice, 45 seconds apart, merges with
`gh pr merge --squash --match-head-commit <sha>`, and never passes `--admin`. Otherwise it exits non-zero
and says why. It exists because a watcher once merged at an old head before a late push registered and
shipped a PR without its final fix. Tests: `agents/python/tests/test_merge_watcher.py` (a fake `gh` on
`MWG_GH`; `MWG_POLL_INTERVAL`, `MWG_MIN_CHECKS` and `MWG_POLLS` are overridable).

```bash
scripts/merge-when-green.sh 250
MWG_MIN_CHECKS=12 scripts/merge-when-green.sh 250   # only if the workflow set really is smaller
```

## When a Dependabot PR conflicts

1. Ask Dependabot to rebase: comment `@dependabot rebase` on the PR. It force-pushes a fresh branch.
   Wait for the new run; do not merge on the old run's results.
2. If it still conflicts, or Dependabot says it cannot (it stops updating a PR you have pushed to),
   do not hand-edit its branch. Close it and `@dependabot recreate`, or make the change yourself on a
   branch from fresh `origin/main`.
3. After any rebase the head SHA is new. Re-run the checks you were watching against the new SHA
   (this is exactly what `merge-when-green.sh` enforces).

### Lockfile conflicts

Never resolve `agents/python/uv.lock` or `web/pnpm-lock.yaml` by editing conflict
markers. Take `main`'s file and regenerate:

```bash
git checkout origin/main -- agents/python/uv.lock
cd agents/python && uv lock          # or: uv lock --upgrade-package <name> for the one being bumped
git diff --stat uv.lock              # should touch only the packages you meant to change
```

Use the same `uv` that CI installs (`astral-sh/setup-uv`, see `.github/workflows/tests.yml`). A
different local `uv` rewrites unrelated markers across the lock; if the diff is bigger than the intended
bump, restore from `main` and edit the one `version =` line instead (see `.claude/HANDOVER.md`). For the
frontend use `pnpm install --lockfile-only` in `web/`. Then run the ecosystem's tests before pushing.

## CHANGELOG.md and docs/parity-matrix.md: keep both

Every lane edits `CHANGELOG.md` (the `[Unreleased]` section) and `docs/parity-matrix.md`, so they
conflict constantly. The rule is **keep both sides**. Never pick one with `--ours` or `--theirs`, which
silently drops another lane's entry.

- `CHANGELOG.md`: keep every bullet from both sides under the right heading (`Added`, `Changed`,
  `Fixed`). If both sides added a heading, merge into one. Check that no `<<<<<<<`, `=======` or
  `>>>>>>>` line remains.
- `docs/parity-matrix.md`: keep both rows. If both sides took the same new row number, renumber yours
  and fix anything that cites it (CLAUDE.md refers to rows by number).
- Merge `main` just before pushing to keep the window small.

## Runbook: cleaning up after parallel lane work

Parallel lanes each get a worktree and a branch. Clean up only what you created.

```bash
git worktree list                              # every worktree, including other sessions'
git worktree remove .claude/worktrees/<mine>   # only a worktree you created; add --force only for your own dirty one
git worktree prune                             # drop records of worktrees whose directory is already gone
git branch -d <my-merged-branch>               # local; -d refuses an unmerged branch, which is the point
git push origin --delete <my-merged-branch>    # only after the PR is merged
git fetch --prune                              # forget deleted remote branches
```

Rules:

- **Never touch another session's worktree, branch or containers.** If a path or branch is not one you
  created in this task, leave it, even if it looks stale. Ask its owner.
- Confirm a branch is merged before deleting it (`gh pr view <n> --json state,mergedAt`). A squash merge
  leaves the branch looking unmerged to `git branch -d`; check the PR, then use `-D` deliberately.
- Do not stop services by name (`pkill`). The specialists bind fixed ports 8081 to 8085, so find the
  process you started by its PID or by `docker compose -p <your project> ...`, and stop that.
- Containers and volumes from your own compose project: `docker compose -p <your project> down -v`.

## Gotchas

- A grouped Dependabot PR that is green can still be wrong for the MAF group: `fixture_drift` exiting 0
  does not prove replay fixtures match; `scripts/replay-evals.sh` is the gate.
- `merge-when-green.sh` refuses while any check is still running. That is intentional; run it again
  later rather than lowering `MWG_MIN_CHECKS`.
- `mergeable` can read `UNKNOWN` for a few seconds after a push. The script treats that as a refusal;
  retry.
