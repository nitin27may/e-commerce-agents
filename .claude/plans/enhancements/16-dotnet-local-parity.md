# Plan 16 — Make the local Docker Compose work for both stacks

**Status:** DONE (2026-08-27) — F1–F5 all fixed and live-verified · **Target:** v1.3.0
> Executed as Wave 1 of [`20-close-out.md`](20-close-out.md). F1's root cause was broader than this
> plan predicted: not one mis-registered tool but 39 of 46, all registered under C# names the shared
> prompt corpus never uses. Two of this plan's three F1 candidates were refuted by measurement.
**Parent:** [`../audit-2026-08-25-adoption-and-azure.md`](../audit-2026-08-25-adoption-and-azure.md)

## Scope, agreed up front

- **Local `docker compose` must work for Python *and* .NET.** That is the goal.
- **The demo path stays Python-only.** A visitor running `--demo` is there to see the features,
  and the backend behind them is not the point. No .NET images will be published.
- This is investigation-led: every finding below was reproduced against a running stack today,
  not read out of the source.

---

## What I actually found

I built and ran `./scripts/dev.sh --dotnet` end to end. It builds, all 12 containers start, the UI
serves, and login works. **Then every question fails.**

| # | Finding | Severity | Reproduced |
|---|---|---|---|
| [F1](#f1--the-net-orchestrator-cannot-reach-any-specialist) | `CallSpecialistAgent` never receives `agentName`; no specialist is reachable | **P0** | Yes |
| [F2](#f2--a-stale-volume-silently-breaks-search-on-either-stack) | Stale volume → `search_vector` missing → search dies with a friendly lie | **P1** | Yes |
| [F3](#f3--net-docker-builds-are-laxer-than-ci-and-unversioned) | .NET Dockerfiles never copy `Directory.Build.props` | **P2** | Yes |
| [F4](#f4--the-two-stacks-cannot-run-at-the-same-time) | Both compose files bind identical ports | **P2** | Yes |
| [F5](#f5--the-net-stack-is-not-actually-a-net-stack) | .NET compose builds `seeder` and `auth-server` from the *Python* Dockerfile | **P3** | Yes |

---

## F1 — The .NET orchestrator cannot reach any specialist

**FIXED 2026-08-26.** Verified against a live stack: `tool.invoked name=call_specialist_agent
error=-`, `agents_involved: ["orchestrator","order-management"]`, and all five orchestration modes
returning real answers. The root cause was broader than this finding described — see the diagnosis
below and `20-close-out.md` Wave 1a.

**This was the whole problem. Everything else on this page is secondary.**

Every routed question comes back apologetic:

> "I tried searching the catalog for running shoes and their prices, but there was an error
> retrieving the results."
> "I tried to retrieve the status of your most recent order, but there was an error accessing the
> order information."

`agents_involved` is `['orchestrator']` every time — no specialist is ever invoked. The
orchestrator log says why:

```
tool.invoked name=CallSpecialistAgent elapsed_ms=2.7
  error=ArgumentException: The arguments dictionary is missing a value for the
        required parameter 'agentName'. (Parameter 'arguments')
```

The model calls the tool and the binder rejects the call because `agentName` is absent from the
arguments dictionary. The specialists are healthy and idle — nothing ever arrives.

### What is known vs. what is guessed

**Known:** the parameter is declared as `agentName` (camelCase) in
`agents/dotnet/src/ECommerceAgents.Orchestrator/Agent/OrchestratorTools.cs:30`, registered through
`AIFunctionFactory.Create(CallSpecialistAgent, nameof(CallSpecialistAgent))` at line 25. The Python
equivalent declares `agent_name` (snake_case) at `agents/python/orchestrator/agent.py:52`. The
shared YAML prompts under `agents/python/config/prompts/` never name the parameter either way, so
the model can only be working from the generated tool schema.

**Not yet known — and the plan must not assume it:** *why* the argument goes missing. Candidates,
in the order I would test them:

1. A JSON naming policy applied when the schema is generated but not when arguments are bound, so
   the model is told one name and the binder expects another.
2. The MAF .NET version in use changed how `AIFunctionFactory` derives parameter names.
3. The model genuinely emits `agent_name` — plausible if anything in the shared prompt corpus or
   the conversation history primes snake_case — and .NET does no normalisation where Python needs
   none.

Capture the raw tool-call payload the model emits before changing any code. Guessing between these
three and patching the wrong one produces a fix that works on one model and breaks on the next.

### Why the test suite is green

`OrchestratorToolsTests.cs` exists and passes. It invokes the method directly, which bypasses
schema generation and argument binding entirely — the two things that are actually broken. This is
the same shape as the failure already recorded in `remaining-work.md`:

> "The .NET orchestrator could never route — `EcommerceContextProvider` returned a fresh
> `AIContext`, discarding the caller's messages and clearing every tool — and 418 tests passed
> while it was broken. It was found by pointing `AZURE_OPENAI_ENDPOINT` at a logging proxy."

That is this bug's second occurrence in the same component, found the same way: by running it.
Whatever the fix turns out to be, **it needs a test that goes through the real tool-invocation path**,
or this will recur a third time.

### Diagnosis — 2026-08-26, two candidates refuted, one confirmed by mechanism

Reproduced statically, with no stack and no API key. Recreating the tool exactly as
`OrchestratorTools.All()` does, against `Microsoft.Agents.AI` **1.18.0** (the pinned version):

```
NAME: CallSpecialistAgent
SCHEMA ADVERTISED TO THE MODEL:
{ "type": "object",
  "properties": {
    "agentName": { "description": "Name of the specialist agent to call", "type": "string" },
    "message":   { "description": "The message to send to the specialist agent", "type": "string" } },
  "required": [ "agentName", "message" ] }

BIND 'agentName'  -> OK: called product-discovery with 'hello'
BIND 'agent_name' -> ArgumentException: The arguments dictionary is missing a value for
                     the required parameter 'agentName'. (Parameter 'arguments')
```

**Candidates 1 and 2 are refuted.** The generated schema advertises `agentName` and the binder
expects `agentName` — they agree exactly, so there is no naming-policy mismatch between schema
generation and argument binding, and `AIFunctionFactory` in 1.18.0 derives the name correctly.

**Candidate 3 is confirmed as the mechanism.** Binding `agent_name` reproduces the *exact* error
text seen in production, character for character. The model is emitting snake_case.

**Why it emits snake_case — the part that makes this a design bug, not a typo.** The .NET
Dockerfiles ship the Python prompt corpus verbatim:

```dockerfile
# Runtime — ship the shared YAML prompts alongside the binary so
# PromptLoader finds them without any volume mount.
COPY --chown=dotnet:dotnet agents/python/config ./agents/python/config
```

And `config/prompts/orchestrator.yaml` — the .NET orchestrator's own system prompt — says:

> You have access to these specialists via the `call_specialist_agent` tool:

So the .NET orchestrator is told, in its system prompt, that its tool is named
`call_specialist_agent`, which is **Python's** name for it (`orchestrator/agent.py:51`, parameters
`agent_name` / `message`). The tool actually registered is `CallSpecialistAgent(agentName, message)`.

The prompt and the tool contract disagree, and the prompt is the shared one. A model primed
throughout its system prompt with Python's snake_case idiom emits snake_case arguments. Nothing in
.NET's own code is wrong in isolation — which is exactly why the C# unit tests are green.

| | Python | .NET | Shared prompt says |
|---|---|---|---|
| Tool name | `call_specialist_agent` | `CallSpecialistAgent` | `call_specialist_agent` |
| Parameter | `agent_name` | `agentName` | — |

**Still unproven:** that the live model emits `agent_name` specifically, rather than failing some
other way. That is now a one-line confirmation rather than an open investigation — the first Work
item below still stands, but it is now confirming a single hypothesis instead of choosing between
three.

**Fix at the shared-contract layer, not in C#.** The prompt corpus is deliberately shared — it is
the single source of truth for both stacks — so the .NET side should conform to it rather than the
corpus growing per-stack variants, which would defeat the sharing. That means registering the tool
as `call_specialist_agent` and exposing the parameter as `agent_name`:

```csharp
AIFunctionFactory.Create(CallSpecialistAgent, "call_specialist_agent")
```

with the parameter renamed or JSON-named to match. Renaming the *prompt* to PascalCase would be the
wrong direction: it would break the Python stack, which is the one that currently works.

**This also predicts a second latent bug.** If a shared prompt naming a Python symbol can silently
break the .NET stack, every other Python-idiom name in that corpus is a candidate. Worth grepping
the corpus for tool and parameter names as part of the fix, rather than fixing this one and
waiting for the next.

### Work

- [ ] Confirm the model emits `agent_name` on the live .NET path (one log line; the three-way
      investigation is already resolved above)
- [ ] Identify which of the three candidates above is actually responsible
- [ ] Fix at the correct layer — schema generation, binding, or the declaration
- [ ] Add a test that exercises schema generation and argument binding, not the C# method directly
- [ ] Re-run the full five-mode check (below) against a live stack

---

## F2 — A stale volume silently breaks search on either stack

First run of `--dotnet` failed every product query with:

```
PostgresException: 42703: column p.search_vector does not exist
```

`pgdata-dotnet` predated the FTS migration, and Postgres only executes
`docker/postgres/init.sql` on an **empty** data directory. Dropping the volume and restarting fixed
it — confirmed, the column is present afterwards.

The FTS commit documents this ("Upgrading an existing database"), so it is a known gotcha rather
than a surprise. What makes it worth fixing is the failure mode:

- `dev.sh` already has a stale-volume check, but it only tests **credentials** — "Database auth
  failed — stale Docker volume detected" — not **schema**. Schema drift sails straight through.
- The user-visible result is not an error. It is a fluent, confident message saying the assistant
  could not retrieve results, which reads like a transient upstream problem rather than a broken
  local database.
- It applies to `docker-compose.yml` exactly as much as to the .NET one. Anyone with a Python
  volume from before the FTS merge has the same latent breakage.

### Work

- [ ] Extend `dev.sh` / `dev.ps1`'s existing stale-volume probe to assert schema, not just auth —
      a cheap `information_schema` query for a column the current `init.sql` guarantees
- [ ] On mismatch: refuse to start and print the two options (`--clean`, or apply in place), rather
      than starting into a stack that will fail every query
- [ ] Decide whether the probe is a hardcoded column or derived from `init.sql`. Hardcoded is
      honest and one line; derived is clever and will drift
- [ ] Note: with the F4 decision (switching stacks recreates volumes), a correctly-performed switch
      never hits this. The probe is for the case where someone pulls new commits and restarts the
      *same* stack without recreating — which is the more common way to land here anyway
- [ ] Same check in `docker-compose.demo.yml`'s path, since demo users are the least equipped to
      diagnose it

---

## F3 — .NET Docker builds are laxer than CI, and unversioned

All seven .NET Dockerfiles copy `Directory.Packages.props` and **not** `Directory.Build.props`:

```dockerfile
COPY agents/dotnet/Directory.Packages.props ./
COPY agents/dotnet/ECommerceAgents.sln ./
COPY agents/dotnet/src/ ./src/
COPY agents/dotnet/tests/ ./tests/
```

The build still succeeds, because every `.csproj` happens to restate `TargetFramework`, `Nullable`
and `ImplicitUsings` itself. That duplication is what hides the omission. What is silently lost:

| Property | Consequence in a Docker build |
|---|---|
| `TreatWarningsAsErrors` | **Docker builds accept warnings CI rejects** |
| `WarningsNotAsErrors` | The deliberate NuGet-audit exemptions do not apply |
| `LangVersion` | Not `latest` |
| `InvariantGlobalization` | Culture behaviour differs from CI |
| `VersionPrefix` | Assemblies ship **unversioned** — added in v1.2.0 and not reaching any image |

So the artefact you run locally is compiled under different rules from the artefact CI validates.
That is a small change to fix and a genuinely confusing class of bug to debug if it ever bites.

### Work

- [ ] Add `COPY agents/dotnet/Directory.Build.props ./` to all seven Dockerfiles
- [ ] Consider removing the now-redundant `TargetFramework` / `Nullable` / `ImplicitUsings` from the
      `.csproj` files, so the props file is unambiguously the single source. **Do this second and
      separately** — it is exactly the duplication currently keeping the build alive
- [ ] Verify a built image reports the expected assembly version

---

## F4 — The two stacks cannot run at the same time

Both compose files bind identical host ports (3000, 5432, 6379, 8080–8085, 8090, 18888). The README
states this. Volumes are already separate (`pgdata` vs `pgdata-dotnet`), so data does not collide —
only ports do.

The user's question was whether two compose files are needed. **They already exist and that split is
right.** The open question is narrower: should both be runnable *simultaneously*?

**Recommendation: no, not by default.** Sequential use is the normal workflow — you compare stacks
by switching, not by running both. Simultaneous operation would need a second set of published
ports, a second frontend build (`NEXT_PUBLIC_API_URL` is inlined at build time, so it cannot point
at two orchestrators), and would double local resource use for a case that arises rarely.

What is worth doing instead is failing clearly. Today, starting the second stack produces a raw
Docker port-binding error.

**Decided:** no simultaneous runs. Switching stacks means bringing the current one down first, and
recreating volumes to start fresh is an accepted part of that workflow — which also sidesteps F2
whenever the switch is done properly.

That makes the switch itself the thing worth smoothing, since it is now the normal path rather than
an edge case.

### Work

- [ ] Detect the other stack's containers at startup and stop with a plain message naming the
      conflict and the exact command to clear it — not a raw Docker port-binding error
- [ ] Add `--switch` (or make `--dotnet` / no-flag do it automatically): bring the other stack down,
      drop its volumes, start this one clean. This is the workflow anyway; scripting it removes the
      chance of half-doing it and landing in F2
- [ ] Document the "one at a time" constraint in `docs/configuration.md`, next to the port table
- [ ] **Not doing:** a simultaneous-run mode. It would need a second published port set and a second
      frontend build (`NEXT_PUBLIC_API_URL` is inlined at build time, so one image cannot address
      two orchestrators), for a case that does not arise in normal use

---

## F5 — The .NET stack is not entirely a .NET stack — **decided: keep, and say so**

`docker-compose.dotnet.yml` builds `seeder` and `auth-server` from **`./agents/python`**. There is
no .NET seeder or auth-server project at all — `agents/dotnet/src/` contains the orchestrator, five
specialists, the MCP host, `Shared` and `Evals`, and nothing else.

This is deliberate, and the compose file already records why:

> `# ── Seeder (reuses the Python script; single source of truth) ──`

> `# Same Python auth_server image used by docker-compose.yml — this stack doesn't share a network`
> `# with the Python compose project, so it gets its own co-located instance (own Postgres, own`
> `# signing key). Seeded client_ids/secrets are identical across both stacks since both derive`
> `# from the same OAUTH_SEED_KEY.`

Expanded, the reasoning holds up:

- **The seeder is the single source of demo data.** `scripts/seed.py` is deterministic
  (`random.seed(42)`). A second implementation would have to produce byte-identical rows or the two
  stacks would diverge in catalogue content — and the dual-backend Playwright suite asserts against
  seeded data, so divergence there quietly destroys the parity gate that is the point of having two
  backends.
- **OAuth2 is protocol-standard, not stack-specific.** Both stacks validate tokens against a JWKS
  endpoint; the issuer's implementation language is invisible to the consumer. That is the whole
  argument for using a standard.
- **Neither is an agent.** The "two complete backends" claim is about the agent layer — orchestrator,
  five specialists, MCP host — and every one of those *is* .NET. A data-loading script and a token
  issuer demonstrate nothing about Microsoft Agent Framework, which is what a reader is here for.

The honest cost, which is why it belongs in the parity matrix rather than being left implicit:

- The .NET stack cannot start unless the Python image builds.
- Python Dockerfile changes affect it. The v1.2.0 fix that added `workflows/` and five specialist
  packages made the .NET stack's `seeder` and `auth-server` images larger for no benefit.

### Work

- [ ] Add a row to `docs/parity-matrix.md` stating that `seeder` and `auth-server` are shared Python
      images on both stacks, with the reasoning above compressed to two lines
- [ ] Optional: scope the Python Dockerfile's extra `COPY` lines so non-orchestrator targets do not
      carry `workflows/` and the specialist packages. Small win, only worth doing if that Dockerfile
      is being touched anyway
- [ ] **Not doing:** porting either service to .NET

---

## Ordering

**Wave 1 — make `--dotnet` actually work.** F1 alone. Nothing else on this page matters while the
orchestrator cannot reach a specialist; the stack starts, looks healthy, and answers nothing.

**Wave 2 — stop the silent failures.** F2, then F3. Both are cases where the system misleads rather
than errors.

**Wave 3 — the honest documentation pass.** F4 and F5. Small changes, mostly writing down what is
already true.

---

## Definition of done

`--dotnet` is not done because it starts. It is done when, against a freshly built stack:

- [ ] Login succeeds
- [ ] A product question routes to `product-discovery` and returns real catalogue data
- [ ] An order question routes to `order-management` and returns a real order
- [ ] All five orchestration modes return a real answer, matching the check applied to the Python
      stack in v1.2.0 — `tool`, `handoff`, `workflow:pre-purchase`, `workflow:return-replace`,
      `group-chat`
- [ ] `/`, `/login` and `/shop` all return 200
- [ ] A run started from a stale volume fails fast with a clear message instead of answering wrongly
- [ ] The dual-backend Playwright suite passes against the .NET stack

The middle four are the ones that would have caught F1. Container health checks would not have —
every container was healthy the entire time it was broken.

---

## Release framing

v1.2.0 is tagged and waiting on approval. **Ship it.** F1 is pre-existing — it is not a regression
introduced by v1.2.0, and v1.2.0 contains real fixes for the Python stack, which is what the demo
path uses.

This work lands as **v1.2.1**, and its changelog entry should say plainly that the .NET stack could
not route to any specialist and for how long, in the register the existing entries use.

One caveat worth deciding on separately: the README and `CHANGELOG` describe "two complete, working
backends". While F1 stands, that claim is not true of `main`. Either fix F1 before the next release
that repeats the claim, or qualify the claim until it is fixed.
