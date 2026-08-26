# Releasing

How a version gets cut, and what has to be true before an image reaches anyone.

## What runs when

| Trigger | Tests | Images | Published as |
|---|---|---|---|
| Pull request | Full suite, plus evals and tutorials | Built, loaded, smoke-tested, **discarded** | nothing |
| Push to `main` | Full suite | Built and pushed **after** all four test jobs pass | `:main`, `:sha-<7>` |
| Tag `vX.Y.Z` | Full suite, re-run against the tagged commit | Built and pushed after tests **and a human approval** | `:vX.Y.Z`, `:latest` |
| Tag `vX.Y.Z-rc.N` | Same | Same | `:vX.Y.Z-rc.N` only — `:latest` does not move |

`.github/workflows/build-images.yml` has no `push` or tag trigger of its own. It exposes
`workflow_call`, and publishing is granted by whoever calls it — `tests.yml` for `main`,
`release.yml` for a tag. That is what makes the gate real: before this, a semver tag published
images with no dependency on any test job, so a tag on a red commit shipped.

## Image tags

| Tag | Meaning | Use it for |
|---|---|---|
| `:sha-<7>` | One immutable image per commit on `main` | Debugging, rollback |
| `:main` | Rolling tip of `main`, tests green | Trying the newest work |
| `:vX.Y.Z` | An immutable release | Pinned deployments |
| `:latest` | The newest **release** | `docker-compose.demo.yml` |

`:latest` deliberately tracks the newest release rather than the tip of `main`. A first-time
visitor running the demo compose file should land on something that passed the full gate and a
human check, not on whatever merged an hour ago.

## Cutting a release

1. **`main` is green.** Check `tests.yml`, `evals.yml` and `tutorials.yml` on the commit you intend
   to tag.

2. **Set the version everywhere.**

   ```bash
   python scripts/bump_version.py 1.2.0
   ```

   This updates `agents/python/pyproject.toml`, `web/package.json` and
   `agents/dotnet/Directory.Build.props`, and opens an empty `CHANGELOG.md` section.

3. **Write the changelog entry.** Fill in the section the script opened. Delete anything that is
   not user-visible — a refactor with no behavioural change does not belong here. Write it the way
   the existing entries are written: say what was broken and for how long, not "improved
   reliability".

4. **Commit and push.**

   ```bash
   git commit -am "chore: release 1.2.0"
   git push
   ```

   Wait for `main` CI. It will publish `:main` images; that is expected.

5. **Tag.**

   ```bash
   git tag v1.2.0 && git push origin v1.2.0
   ```

6. **Approve.** `release.yml` re-runs the full suite against the tag, checks that the tag matches
   `pyproject.toml` and that `CHANGELOG.md` has a matching section, then waits on the `release`
   environment. Review the results and approve.

7. **Verify the images are pullable anonymously.**

   ```bash
   docker logout ghcr.io
   docker pull ghcr.io/nitin27may/e-commerce-agents/orchestrator:latest
   docker manifest inspect ghcr.io/nitin27may/e-commerce-agents/orchestrator:latest \
     | grep architecture
   ```

   Expect both `amd64` and `arm64`. A failure here almost always means a new package was created
   private — see below.

8. **Update the docs site.** The "Where the project is" section on the home page names the current
   version.

## One-time setup

Each of these fails **silently** if skipped — nothing errors, the wrong thing just happens quietly.

### The `release` environment

Settings → Environments → New environment → `release`, with **Required reviewers** set to yourself.

Without it, `environment: release` in `release.yml` is a no-op. The approval job succeeds
immediately and the release publishes with no human in the loop, with nothing in the log to
indicate a gate was expected.

### Package visibility

GHCR packages are created **private**. The first publish creates ten of them, and anonymous
`docker compose pull` then fails with an authentication error that reads like a network problem.

For each package: Packages → *package* → Package settings → Change visibility → Public.

Ten packages: `orchestrator`, `product-discovery`, `order-management`, `pricing-promotions`,
`review-sentiment`, `inventory-fulfillment`, `auth-server`, `mcp-product`, `mcp-inventory`,
`frontend`.

### Retention — already automated

GHCR has no built-in retention setting, so `.github/workflows/package-cleanup.yml` is the policy.
It runs weekly across all ten packages, keeps the most recent 20 versions, and never touches
`:latest`, `:main`, or any `:vX.Y.Z` tag.

Two things would otherwise accumulate without limit: one `:sha-<7>` tag per commit on `main`, and
untagged versions — a multi-arch build pushes a manifest list plus one image manifest per platform,
and the platform manifests are orphaned when the tag moves. The second is the larger source by
volume and is invisible in the packages UI unless you go looking.

Run it once by hand before trusting it. The default for a manual run is a dry run:

```bash
gh workflow run package-cleanup.yml -f dry_run=true
gh run watch
```

## Versioning

Semantic versioning. The **git tag is the source of truth**; the three version files are synced to
it by `scripts/bump_version.py`, and `release.yml`'s `version-check` job fails the release if they
drift.

That check exists because they did drift: `pyproject.toml` said `0.1.0`, the only tag said
`v1.0.0`, and the README said v1.1 — a version that shipped but was never tagged, and therefore
never existed as an artifact anyone could pull.

CI runs the same check:

```bash
python scripts/bump_version.py 1.2.0 --check
```

## When a release goes wrong

**Bad tag, nothing published yet** — delete it and start over.

```bash
git tag -d v1.2.0 && git push --delete origin v1.2.0
```

**Bad tag, images already published** — do not delete or overwrite a published tag. Ship
`v1.2.1`. Anyone who pulled `v1.2.0` has it; silently changing what that tag points to is worse
than a bad release.

**`:latest` points at a bad release** — cut the fix as a new release. `:latest` moves forward with
the next successful release; it is never moved backwards by hand.

## Related

- [Configuration](configuration.md) — where settings come from
- [Deployment](deployment.md) — running the stack
- [`.claude/plans/enhancements/15-build-and-release.md`](https://github.com/nitin27may/e-commerce-agents/blob/main/.claude/plans/enhancements/15-build-and-release.md) — the design and why each piece exists
