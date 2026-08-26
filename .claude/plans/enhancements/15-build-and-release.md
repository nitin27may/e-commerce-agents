# Plan 15 — Build Gating and Release Process

**Repo:** `~/workspace/e-commerce-agents` · **Date:** 2026-08-25
**Status:** in progress · branch `ci/build-gating-and-release`


**Related:** [`14-pre-azure.md`](14-pre-azure.md) — this plan revises items 1 and 2 of that plan.
**Blocks:** plan 13 (Azure), which will reuse this release machinery to tag deployments.

---

## Context

Plan 14 item 1 proposed publishing container images to GHCR on every push to `main`, so visitors
stop rebuilding eight images from source before they see anything. Writing that raised a question
the repo has never answered: **what has to be true before an image is published?**

Today, nothing. The five workflows are completely independent:

| Workflow | PR | push `main` | tag `v*.*.*` | schedule |
|---|---|---|---|---|
| `tests.yml` | yes | yes | **no** | no |
| `build-images.yml` | smoke, no push | smoke, no push | **pushes images** | no |
| `evals.yml` | smoke (path-filtered) | path-filtered | no | weekly full |
| `tutorials.yml` | yes | yes | **no** | no |
| `jekyll-gh-pages.yml` | check only | deploys | no | no |

`build-images.yml` resolves `should_push=true` on a semver tag with **no `needs:` on anything**.
GitHub Actions has no cross-workflow `needs:`, so the tests and the image build run in parallel and
never see each other. **A tag pushed onto a red commit publishes images today**, and the tag trigger
runs no tests at all.

Three more things surfaced while reading the workflows:

- **v1.1 was never tagged.** `gh release list` shows one release, `v1.0.0` (2026-08-20), and
  `git tag -l` shows one tag. Meanwhile `README.md` says "This is v1.1" and
  `remaining-work.md` says "after v1.1.0". The version that shipped does not exist as an artifact.
- **There is no `CHANGELOG.md`.**
- **Version state is incoherent across three places.** `agents/python/pyproject.toml` says
  `version = "0.1.0"`, the git tag says `v1.0.0`, the README says v1.1.

The outcome this plan produces: a pull request builds and tests but never publishes; a push to
`main` publishes rolling images only after the test suite is green; a version tag runs the full
gate, waits for a human, then publishes versioned images and a GitHub release with curated notes.

### Decisions taken

| Decision | Choice |
|---|---|
| `docker-compose.demo.yml` image tag | `:latest` — the last tagged release, not tip of main |
| The missing v1.1 | Tag current `main` as `v1.1.0`; backfill CHANGELOG for v1.0.0 and v1.1.0 |
| Gate on image publish | `tests.yml` only (Python, MCP, .NET, web) |
| Release trigger | Automatic on tag push, **manual approval** before images and release publish |

---

## Design

Reusable workflows (`workflow_call`) are the mechanism, because they are the only way to express
"job B runs after job A" across what are currently separate workflow files.

```
pull_request ──> tests.yml        (4 test jobs)
             └─> build-images.yml (build 10 images, load, smoke-test, DISCARD)

push main ─────> tests.yml
                   └─ publish-main ──uses──> build-images.yml (publish :main, :sha-xxxxxxx)
                      needs: all 4 test jobs

tag v*.*.* ────> release.yml
                   ├─ gate     ──uses──> tests.yml            (full re-run on the tag)
                   ├─ approve  (GitHub environment "release" — human clicks)
                   ├─ publish  ──uses──> build-images.yml     (:vX.Y.Z, :latest)
                   └─ release  (gh release create, notes from CHANGELOG.md)
```

### Image tag policy

| Tag | Produced by | Meaning | Consumed by |
|---|---|---|---|
| `:sha-<7>` | push to `main` | Immutable, one per commit | debugging, rollback |
| `:main` | push to `main` | Rolling tip of main, tests green | contributors wanting newest |
| `:vX.Y.Z` | version tag | Immutable release | pinned deployments, Azure |
| `:latest` | version tag | Newest release | **`docker-compose.demo.yml`** |

`:latest` deliberately tracks the newest *release*, not `main`. A first-time visitor's experience
should ride on something that passed the full gate and a human check, not on whatever merged an
hour ago.

---

## Changes

### 1. `.github/workflows/build-images.yml` — becomes reusable, never self-publishes

**Triggers.** Remove `push: branches: [main]` and `push: tags: ["v*.*.*"]`. Replace with:

- `pull_request: branches: [main]` — build, load, smoke-test, discard. Never pushes.
- `workflow_call` with inputs `publish` (boolean), `tag_mode` (`main` | `release`), `version` (string)
- `workflow_dispatch` with a `publish` input, kept as an escape hatch

Deleting the tag trigger is the fix for the stated problem: this workflow can no longer publish
anything on its own initiative.

**Matrix — extend from 6 images to 10.** The current matrix covers `orchestrator` plus the five
specialists. `docker-compose.yml` needs four more, in three different build shapes:

| Image | Context | Dockerfile | Build args |
|---|---|---|---|
| `orchestrator`, `product-discovery`, `order-management`, `pricing-promotions`, `review-sentiment`, `inventory-fulfillment` | `./agents/python` | default | `AGENT_NAME`, `AGENT_PORT` |
| **`auth-server`** | `./agents/python` | default | `AGENT_NAME=auth_server`, `AGENT_PORT=8090` |
| **`mcp-product`** | `./agents/python` | `Dockerfile.mcp` | `MCP_PACKAGE=ecommerce-mcp-product`, `MCP_DIR=packages/mcp-product`, `MCP_MODULE=ecommerce_mcp_product.server:app` |
| **`mcp-inventory`** | `./agents/python` | `Dockerfile.mcp` | mirror of the above |
| **`frontend`** | `./web` | default | — |

Give each matrix entry explicit `context`, `dockerfile` and `build_args` fields so all three shapes
coexist. Without the four in bold, `docker-compose.demo.yml` still builds locally and plan 14
items 1–2 achieve nothing.

**Platforms.** PR path stays `linux/amd64` only — `load: true` **cannot load a multi-platform
image**, and the existing import smoke-test depends on `load`. Publish path gets
`platforms: linux/amd64,linux/arm64`. Apple Silicon readers currently get QEMU emulation on an
amd64-only image; arm64 under QEMU roughly triples build time, which is acceptable on `main` and
not on every PR.

**Preserve** the existing per-agent import smoke-test on the PR path, and the
`cache-from`/`cache-to` GHA scopes.

**Add** `org.opencontainers.image.source` and `.licenses` labels so each GHCR package page shows
the README and the MIT licence instead of an empty page.

### 2. `.github/workflows/tests.yml` — gains the publish job

Add `workflow_call:` to the trigger list so `release.yml` can invoke it, and append one job:

```yaml
publish-main:
  needs: [python-tests, mcp-package-tests, dotnet-tests, web-tests]
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  permissions:
    contents: read
    packages: write
  uses: ./.github/workflows/build-images.yml
  with:
    publish: true
    tag_mode: main
  secrets: inherit
```

A job with `uses:` supports `needs:` and its own `permissions:` block. The top-level `permissions:`
in `tests.yml` stays `contents: read` / `pull-requests: write` — only this job gets
`packages: write`.

**Do not** add `evals.yml` or `tutorials.yml` to `needs`. Per the decision above, they gate the pull
request; they do not gate the image. Tutorial CI in particular has nothing to do with the contents
of these images, and coupling them means a broken tutorial chapter blocks a security fix from
shipping.

### 3. `.github/workflows/release.yml` — new

Trigger: `push: tags: ["v*.*.*"]`. Four jobs, in sequence:

1. **`gate`** — `uses: ./.github/workflows/tests.yml`, `secrets: inherit`. Re-runs the full suite
   against the tagged commit. This is the missing piece: today the tag path runs nothing.
2. **`version-check`** — fails if `github.ref_name` does not match the version in
   `agents/python/pyproject.toml`. Cheap, and it stops the drift that produced the current
   `0.1.0` vs `v1.0.0` vs "v1.1" situation.
3. **`approve`** — `needs: [gate, version-check]`, `environment: release`. A no-op job whose only
   purpose is to block on the environment's required reviewer. You see the gate results, then click.
4. **`publish`** — `needs: approve`, `uses: ./.github/workflows/build-images.yml` with
   `publish: true`, `tag_mode: release`, `version: ${{ github.ref_name }}`.
5. **`release`** — `needs: publish`. Extracts the section for this version from `CHANGELOG.md` and
   runs `gh release create` with it. Fails if the changelog has no matching section, so a release
   can never ship with empty notes.

Pre-releases (`v1.2.0-rc.1`) publish `:v1.2.0-rc.1` and **do not move `:latest`**. Gate that on
whether the tag contains a hyphen.

### 4. Versioning — one source of truth

The git tag is authoritative. Everything else is synced to it.

**New `scripts/bump_version.py`** — takes a version, updates:

- `agents/python/pyproject.toml` (currently the stale `0.1.0`)
- `web/package.json`
- the .NET version property — **locate it first**; if the solution has no
  `Directory.Build.props`, add one rather than editing every `.csproj`
- opens a new `CHANGELOG.md` section skeleton

Run it, review the diff, commit, tag. The `version-check` job in `release.yml` is what makes
forgetting to run it a build failure rather than a silent inconsistency.

### 5. `CHANGELOG.md` — new, hand-written, backfilled

Keep a Changelog format. Curated prose, not generated commit lists — the repo's writing voice is a
genuine differentiator and an auto-generated bullet dump would throw it away.

**The backfill is nearly free.** `README.md` already contains "Shipped in v1" and "Shipped in v1.1"
sections written in exactly the right register ("no promotion had ever applied correctly",
"specialists received *no* conversation history on any browser-originated turn"). Lift those into
`CHANGELOG.md` under `[1.0.0]` and `[1.1.0]`, then have the README link to the changelog instead of
duplicating it — which also serves plan 14 item 8's README cut.

`gh release create --generate-notes` is useful as a first draft only. The repo's commit messages are
unusually descriptive, so the draft will be decent; it still gets edited before shipping.

### 6. Release checklist — new `docs/releasing.md`

Short, and written for the person doing it at 11pm:

1. `main` is green — check `tests.yml`, `evals.yml`, `tutorials.yml`
2. `uv run python scripts/bump_version.py X.Y.Z`
3. Write the `CHANGELOG.md` section; delete anything that isn't user-visible
4. Commit, push, wait for `main` CI
5. `git tag vX.Y.Z && git push origin vX.Y.Z`
6. Approve the `release` environment when `release.yml` asks
7. Verify: `docker logout ghcr.io && docker pull ghcr.io/nitin27may/e-commerce-agents/orchestrator:latest`
8. Update the "Where the project is" section on the docs site home

---

## Consequences for plan 14

**Item 1** is superseded by sections 1–2 above. The push-resolution change described there is
replaced by the reusable-workflow design, and the matrix grows to ten with three build shapes.

**Item 2 changes tag and gains an ordering dependency.** `docker-compose.demo.yml` pins `:latest`,
not `:main` — which means **it cannot be tested until the first gated release exists**. Revised
order for plan 14:

```
15 (this plan) → tag v1.1.0 → :latest exists → 14 item 2 (demo compose) → 14 item 3 (clip)
```

Items 4–8 of plan 14 are unaffected and can proceed in parallel.

---

## Manual setup — cannot be automated, and each fails silently

1. **Create the `release` GitHub environment** with required reviewers (yourself). Without it,
   `environment: release` is a no-op and the approval gate does not exist — the release proceeds
   automatically with no error and no indication anything is missing.
2. **Set all ten GHCR packages to public** after the first publish. They are created private by
   default; anonymous `docker compose pull` then fails with an authentication error that reads like
   a network problem. This is the most likely way the whole effort silently fails.
3. **Add a package retention policy**, or `:sha-*` tags accumulate without limit.

---

## Files

**New:** `.github/workflows/release.yml` · `CHANGELOG.md` · `scripts/bump_version.py` ·
`docs/releasing.md` · `docker-compose.demo.yml` (plan 14 item 2)

**Modified:** `.github/workflows/build-images.yml` (triggers, matrix, platforms, labels) ·
`.github/workflows/tests.yml` (`workflow_call` + `publish-main` job) ·
`agents/python/pyproject.toml` (version) · `web/package.json` (version) ·
`README.md` (link the changelog rather than duplicating it)

**Read before writing:** `agents/dotnet/ECommerceAgents.sln` and its `.csproj` files, to find where
the .NET version currently lives.

---

## Verification

Each step is checkable, and several would otherwise fail silently.

**PR path — builds, never publishes.**
```bash
gh pr create --draft --title "ci: verify no publish on PR"
gh api /user/packages?package_type=container --jq '.[].name'   # unchanged
```
Confirm `build-images.yml` built all ten and the run log shows `push: false`.

**Gate actually gates.** On a throwaway branch, break one assertion in a Python test, merge to a
scratch branch configured like `main`. Confirm `publish-main` is **skipped**, not failed-then-run.
This is the assertion that matters most — an unenforced gate looks identical to an enforced one
until the day it doesn't.

**main path.**
```bash
docker manifest inspect ghcr.io/nitin27may/e-commerce-agents/orchestrator:main
```
Confirm `:main` and `:sha-<7>` for all ten, and that the manifest lists both `linux/amd64` and
`linux/arm64`.

**Tag path.** `git tag v1.1.0 && git push origin v1.1.0`. Confirm: the full suite re-runs on the
tag; the run **pauses** for approval; after approval, `:v1.1.0` and `:latest` appear; a GitHub
release exists whose body matches the `[1.1.0]` section of `CHANGELOG.md`.

**Anonymous pull.**
```bash
docker logout ghcr.io
docker pull ghcr.io/nitin27may/e-commerce-agents/frontend:latest
```
Must succeed. If it does not, the package visibility step was missed.

**End to end, on a clean machine.**
```bash
git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents && cp .env.minimal .env    # set OPENAI_API_KEY
time docker compose -f docker-compose.demo.yml up
```
Target: a signed-in chat turn with product cards in **under two minutes**, no local build. Time it
and record the number — it is the metric plan 14 item 2 exists to move.

**Pre-release does not move `:latest`.** Tag `v1.2.0-rc.1`, confirm `:latest` still resolves to
`v1.1.0`.

---

## Addendum — environment variable strategy

Raised during implementation: is the repo-root `.env` in the right place, and should everything
move to one location?

**Investigated, and the answer is no change.** The root `.env` is already the single source of
truth, and it already works from any working directory. Full write-up in
[`docs/configuration.md`](../../docs/configuration.md); the reasoning in short:

- **Compose only auto-loads a file named `.env` from the project directory.** Moving it to, say,
  `config/.env` would require `--env-file` on every `docker compose` invocation, which breaks the
  bare `docker compose up` the README advertises.
- **`shared/config.py` already resolves it absolutely**, from `config.py`'s own location rather
  than the CWD (`_resolve_repo_root`, `_ENV_FILE`). That is why
  `cd agents/python && uv run uvicorn ...` picks up the root file. Two bugs are already fixed
  there and documented in the source: `parents[2]` silently resolved to `<repo>/agents` so
  `env_file` loading never fired, and `parents[3]` raised `IndexError` inside the image where
  `config.py` is copied flatly to `/app/shared/`. **Do not change that resolution.**
- **Containers do not read `.env` at all.** Compose uses it for `${VAR}` interpolation into
  `environment:` blocks, and a container receives only what its block names. A variable added to
  `.env` but not to the compose block is silently absent in every container — the most common
  configuration mistake in this repo, and it yields a default rather than an error.
- **The frontend does not read it either**, and does not need to: `web/src/lib/api.ts:1` falls back
  to `http://localhost:8080`, matching the compose default, so `pnpm dev` works unconfigured.

The real friction was never the location — it is that a new variable must be declared in four to
six places (`config.py`, `.env.example`, both compose files, the .NET settings, the docs table).
`docs/configuration.md` now documents that checklist, which is the thing that was actually missing.

Shipped alongside: **`.env.minimal`** (one variable, the quick-start default) with `.env.example`
demoted to reference material. Verified not caught by `.gitignore`.

## Status — 2026-08-25

Branch `ci/build-gating-and-release`.

- [x] `build-images.yml` — reusable, `push`/tag triggers removed, matrix 6 → 10, multi-arch on
      publish, OCI labels, frontend smoke-test, `build-images-ok` aggregate job
- [x] `tests.yml` — `workflow_call` + `publish-main` gated on all four test jobs
- [x] `release.yml` — gate → version-check → approve → publish → release
- [x] `scripts/bump_version.py` + `--check`, ruff clean, exercised
- [x] `CHANGELOG.md` — backfilled 1.0.0 and 1.1.0, notes extraction verified
- [x] `docs/releasing.md`, `docs/configuration.md`, registered in the site generator (87 pages, no
      broken links)
- [x] `.env.minimal`; versions synced to 1.1.0 across all three files
- [ ] **Manual, and each fails silently:** create the `release` environment with required
      reviewers; set the ten GHCR packages public after first publish; add a retention policy
- [ ] Tag `v1.1.0` — only after this branch merges, since `version-check` reads `main`
