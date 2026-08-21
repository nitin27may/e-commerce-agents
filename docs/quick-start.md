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
cp .env.example .env
```

On Windows PowerShell, the last line is `copy .env.example .env`.

Then open `.env` and set a model provider. Any one of these works — see
[Run without a paid API key](#run-without-a-paid-api-key) if you would rather not use a paid one:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

## 2. Run it

### macOS and Linux

```bash
./scripts/dev.sh
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

Nothing here needs a Linux shell — `docker compose` is the same command on every platform. These
three lines are exactly what `dev.sh` does, minus the health-check polling and the closing summary.
Run them from the repo root in **PowerShell**:

```powershell
# 1. Get the code and configure it
git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents
Copy-Item .env.example .env
notepad .env                  # set OPENAI_API_KEY, then save and close

# 2. Start infrastructure, seed, then start the app
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
| `./scripts/dev.sh --clean` | `docker compose down -v` then re-run the three commands |
| `./scripts/dev.sh --seed-only` | `docker compose --profile seed run --rm seeder` |
| `./scripts/dev.sh --infra-only` | `docker compose up -d db redis aspire` |
| `lsof -i :3000` (port conflicts) | `netstat -ano \| findstr :3000` |
| `docker compose logs -f orchestrator` | identical — Compose commands don't change |

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
# GitHub Models — free with a GitHub PAT
LLM_PROVIDER=openai
LLM_BASE_URL=https://models.inference.ai.azure.com
OPENAI_API_KEY=<a GitHub PAT with the models:read scope>
LLM_MODEL=gpt-4o

# Ollama — fully local, no account, no key
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama          # any non-empty string — Ollama doesn't check it
LLM_MODEL=llama3.1:8b          # must be a tool-calling-capable model
```

{: .warning }
> **Check that your local model can actually call tools.** Every specialist here depends on
> tool-calling, and the failure is quiet: a model with unreliable function-calling stops calling
> tools and starts inventing product names and prices instead of erroring. Llama 3.1+, Qwen2.5 and
> tool-tagged Mistral builds are known to work. If answers look plausible but the agent timeline
> shows no tool calls, that is the symptom.

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
