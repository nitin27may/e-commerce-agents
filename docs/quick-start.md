# Quick Start

Get the whole platform — six agents, Postgres, Redis, and the web UI — running locally.
**Docker is the only hard requirement.** You do not need Python, .NET, Node, or a paid API key
installed to run it.

Pick the section for your machine. macOS and Linux can use the helper script; **Windows should use
the Docker Compose commands directly**, since `scripts/dev.sh` is a bash script.

## 1. Get the code and configure it

Identical everywhere:

```bash
git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents
cp .env.minimal .env
```

On Windows PowerShell, the last line is `copy .env.minimal .env`.

`.env.minimal` is one variable. `.env.example` is the complete surface — every auth mode, MCP,
OAuth, telemetry and guardrail setting — and is reference material rather than a starting point.
[Configuration](configuration.md) explains how that one file reaches containers, host-run Python,
the frontend and the .NET stack, which do not all read it the same way.

Then open `.env` and set a model provider. Any one of these works — see
[Run without a paid API key](#run-without-a-paid-api-key) if you would rather not use a paid one:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

## 2. Run it

There are two paths, and the difference is roughly a minute versus twelve.

| | `--demo` | from source |
|---|---|---|
| Where the images come from | Pulled from GHCR | Built on your machine |
| First run | ~1 minute | ~12 minutes |
| Architectures | `linux/amd64` and `linux/arm64` | whatever you are on |
| Use it when | You want to see the thing work | You have changed code |

### The fast path — pull prebuilt images

```bash
./scripts/dev.sh --demo          # macOS and Linux
./scripts/dev.ps1 -Demo          # Windows PowerShell
```

This pulls the ten released images, seeds the database, starts everything, and waits until the
orchestrator and frontend actually answer before printing the summary. Nothing is compiled.

`--demo` uses `docker-compose.demo.yml`, which pins `:latest` — the newest tagged release, gated by
the full test suite. To run the tip of `main` instead:

```bash
IMAGE_TAG=main ./scripts/dev.sh --demo
```

Plain `docker compose` works too, if you would rather not use the script:

```bash
docker compose -f docker-compose.demo.yml up
```

See [Releasing](releasing.md) for what each image tag means.

### From source

```bash
./scripts/dev.sh                 # macOS and Linux
./scripts/dev.ps1                # Windows PowerShell
```

The script builds the images, waits for Postgres to become healthy, runs the seeder as a one-shot
job, then starts the agents and the frontend. It also prints a summary of every URL at the end.

### Windows

`scripts/dev.sh` is a bash script and will not run in PowerShell or `cmd`. You have two paths, and
they are not equal — pick based on whether you want WSL2 on your machine.

#### Recommended: WSL2

**[WSL2](https://learn.microsoft.com/windows/wsl/install) gives the best experience**, and if you
already run Docker Desktop you are most likely using its WSL2 backend anyway, so this adds no new
moving parts. Everything works exactly as documented for macOS and Linux — the helper script, the
`--clean`/`--seed-only` flags, all of it:

```bash
wsl                                   # drop into your Linux distro
git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents
cp .env.example .env
./scripts/dev.sh
```

Two things to get right:

- **Clone inside the Linux filesystem** (`~/e-commerce-agents`), not under `/mnt/c/`. Bind-mounting
  across the Windows filesystem boundary is dramatically slower and is the usual answer to "why is
  my container so slow on WSL2".
- **Enable WSL integration in Docker Desktop** — *Settings → Resources → WSL Integration* — for the
  distro you are using, or `docker` will not be found inside WSL.

Git Bash (bundled with [Git for Windows](https://git-scm.com/download/win)) also runs the script,
but only WSL2 gives you a real Linux filesystem, so container startup is much faster there.

#### Not using WSL2? PowerShell works fine

There is a PowerShell script that does everything `dev.sh` does — same profiles, same ordering,
same health checks, same flags:

```powershell
git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents
Copy-Item .env.example .env
notepad .env                  # set OPENAI_API_KEY, then save and close

./scripts/dev.ps1
```

If PowerShell refuses to run it (`running scripts is disabled on this system`), that is the
execution policy, not the script. Either allow local scripts once —
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` — or bypass it for this run alone with
`powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1`.

**Or skip the script entirely.** Nothing here needs one; `docker compose` is the same command on
every platform, and these three lines are what the script does, minus the health polling and the
closing summary:

```powershell
docker compose up -d db redis aspire
docker compose --profile seed run --rm seeder
docker compose --profile agents --profile frontend up -d --build
```

Then open <http://localhost:3000>.

You do not need to wait between those commands: the seeder declares
`depends_on: db: {condition: service_healthy}`, so Compose blocks it until Postgres passes its
health check. The first run builds images, so expect a few minutes before anything responds.

**PowerShell differences worth knowing:**

| Instead of | Use |
|---|---|
| `cp .env.example .env` | `Copy-Item .env.example .env` |
| `\` at end of line (continuation) | a backtick `` ` ``, or put it all on one line |
| `./scripts/dev.sh --clean` | `./scripts/dev.ps1 -Clean` — or `docker compose down -v`, then re-run |
| `./scripts/dev.sh --seed-only` | `./scripts/dev.ps1 -SeedOnly` — or `docker compose --profile seed run --rm seeder` |
| `./scripts/dev.sh --infra-only` | `./scripts/dev.ps1 -InfraOnly` — or `docker compose up -d db redis aspire` |
| `./scripts/dev.sh --dotnet` | `./scripts/dev.ps1 -Dotnet` |
| `lsof -i :3000` (port conflicts) | `netstat -ano \| findstr :3000` |
| `docker compose logs -f orchestrator` | identical — Compose commands don't change |

`dev.ps1` needs **PowerShell 7+** on macOS and Linux
([install docs](https://learn.microsoft.com/powershell/scripting/install/installing-powershell));
on Windows the built-in Windows PowerShell 5.1 is enough. On macOS and Linux `dev.sh` remains the
more idiomatic choice — the two are interchangeable.

To stop everything: `docker compose down`. To stop and wipe the database as well:
`docker compose down -v`.

{: .note }
> If you use Git Bash rather than PowerShell and `./scripts/dev.sh` fails with
> `bad interpreter: /bin/bash^M`, that is Git's `core.autocrlf` rewriting the script to CRLF on
> checkout, not a broken script. The repo ships a `.gitattributes` pinning `*.sh` to LF, so a fresh
> clone is fine; an older clone needs `git rm --cached -r . && git reset --hard` to pick it up.

### One-liner, any platform

If you would rather not run the steps separately, this starts everything including the seeder:

```bash
docker compose --profile seed --profile agents --profile frontend up --build
```

## 3. Open it

| What | URL |
|------|-----|
| **Web app** | <http://localhost:3000> |
| Orchestrator API | <http://localhost:8080> |
| Aspire dashboard (traces) | <http://localhost:18888> |

Sign in with any seeded account — `alice.johnson@gmail.com` / `customer123` is a customer with
order history, which makes the demo scenarios more interesting than a fresh account. The full list
is in the [README's Test Users table](https://github.com/nitin27may/e-commerce-agents#test-users).

You can also browse the catalog and use the shopping assistant at `/shop` **without signing in** —
product discovery is served anonymously.

## Run the .NET backend instead

Same database, same prompts, same frontend — a different compose file. Only one stack can run at a
time, because both bind the same ports.

```bash
# macOS / Linux
./scripts/dev.sh --dotnet

# Any platform, Compose directly
docker compose -f docker-compose.dotnet.yml \
  --profile seed --profile agents --profile mcp --profile frontend up --build
```

On Windows PowerShell, put the whole command on one line, or use a backtick (`` ` ``) for line
continuation instead of the backslash.

## Run without a paid API key

Nothing above requires an OpenAI subscription. Any OpenAI-compatible endpoint works through the
same code path — set `LLM_BASE_URL` and leave `LLM_PROVIDER=openai`:

```dotenv
# Ollama — fully local, no account, no key, no rate limit
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama          # any non-empty string — Ollama doesn't check it
LLM_MODEL=qwen2.5:14b          # must be a tool-calling-capable model — see below
```

Start the model first, and **raise the context window** — this is the single most common cause of
a local run behaving worse than a hosted one:

```bash
ollama pull qwen2.5:14b
OLLAMA_CONTEXT_LENGTH=64000 ollama serve
```

Ollama defaults to a 4K context on machines with under 24 GiB of VRAM. An agent loop accumulating
tool results passes 4K within a few turns, and Ollama then **silently discards the oldest messages
— starting with the system prompt** — with no error and nothing in the response to tell you. The
symptom is a confident, well-formed, wrong answer. Ollama's own documentation recommends at least
64000 tokens for agent workloads.


{: .warning }
> **Check that your local model can actually call tools.** Every specialist here depends on
> tool-calling, and the failure is quiet: a model with unreliable function-calling stops calling
> tools and starts inventing product names and prices instead of erroring.
>
> Measured 2026-08-21 on a 2-tool, multi-turn loop (check stock, compute the shortfall, restock to
> the reorder point): **`qwen2.5:14b`**, **`gemma4:12b`** and **`qwen3.5:9b`** all passed — correct
> tool sequence, correct arithmetic, clean termination. That is one scenario, not a benchmark:
> treat it as evidence that the 9B-and-up class is viable here, not as a ranking.
>
> If answers look plausible but the agent timeline shows no tool calls, that is the symptom of a
> model that cannot hold up.

{: .warning }
> **The second silent failure: reasoning models can answer with nothing at all.**
> `qwen2.5:14b` is the recommended default because it emits no thinking trace. Reasoning models
> interleave a long internal monologue *before* the answer, and it is billed against the same
> output budget — so a small `max_tokens` gets spent on thinking and the reply comes back **empty**,
> with `finish_reason: "length"` rather than an error.
>
> Measured on the same prompt and the same 1,024-token cap:
>
> | Model | Thinking trace | finish_reason | Latency | Answer |
> |---|---|---|---|---|
> | `qwen2.5:14b` | none | `stop` | ~10 s | present |
> | `gemma4:12b` | ~1,000 chars | `stop` | ~39 s | present |
> | `qwen3.5:9b` | ~3,957 chars | **`length`** | ~65 s | **empty** |
>
> Note that smaller is not faster here — `qwen3.5:9b` is the smallest of the three and 6.5x slower
> than the largest, because it spends the time thinking. If a model returns blank content, check
> `finish_reason` before concluding it cannot do tool calls; raise `max_tokens` (4096 is a safe
> floor) or pick a non-reasoning model.

On Ollama, the endpoint must be reachable *from inside the container*: use
`http://host.docker.internal:11434/v1` on Docker Desktop (macOS/Windows) rather than
`localhost`.

## Other commands

```bash
./scripts/dev.sh --clean        # nuke volumes and rebuild from scratch
./scripts/dev.sh --infra-only   # just db, redis, aspire
./scripts/dev.sh --seed-only    # re-run the seeder against an existing DB
./scripts/dev.sh --dotnet       # the .NET stack instead of Python
```

The Compose equivalents are `docker compose down -v`, `docker compose up -d db redis aspire`, and
`docker compose --profile seed run --rm seeder`.

## If something breaks

Start with [Troubleshooting](./troubleshooting.md) — it covers every first-run failure we know
about, including port conflicts, the seeder racing the database, and missing embeddings.

The two most common:

- **Port 3000 or 8080 already in use.** Another service owns it. `docker compose down` does not
  help if the conflict is outside Compose — check with `lsof -i :3000` (macOS/Linux) or
  `netstat -ano | findstr :3000` (Windows).
- **The chat answers but never calls a tool.** Almost always the model, not the code — see the
  warning above.

## Where to go next

- [Concepts](./concepts/) — what an agent is, why more than one, what a graph means here
- [Tutorials](../tutorials/) — 34 chapters, Python and .NET, each runnable without an API key
- [Architecture](./architecture.md) — how the whole system fits together
- [Deployment](./deployment.md) — configuration reference, profiles, environment variables
