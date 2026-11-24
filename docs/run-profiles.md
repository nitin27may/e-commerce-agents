# Run profiles

A run profile is one provider setup, ready to apply: chat, embeddings and the model set together, with no
secret in it. You pick one, put your key in `.env`, and start the stack. The doctor then tells you whether it
works.

```bash
./scripts/dev.sh --profile ollama                                  # PowerShell: ./scripts/dev.ps1 -Profile ollama
uv run --project agents/python python -m scripts.doctor --profile ollama
```

Profiles live in `agents/python/config/profiles/<name>.env`. The header of each file is the source for the
columns below, so the file and this page cannot say different things (a test checks that every profile is
listed here).

## Choosing one

| Tier | You need | You get |
|---|---|---|
| 0 Keyless | Docker | The app on recorded answers, replay regression, detector recall |
| 1 Any provider, local | A profile plus a key, or a local model | The app on that model; live quality and safety evals through the eval CLI |
| 2 Azure | A subscription | Tier 1 plus the Azure pieces: gateway, Foundry (planned, see the table) |

## The profiles

"Verified" says what has actually been exercised. Nothing here is a claim that a provider works unless the
cell says how it was shown. **Stub-tested** means a test with a faked HTTP transport, not a live call.

| Profile | Tier | Python | .NET | Key you supply | Cost | Verified how | Evals that apply |
|---|---|---|---|---|---|---|---|
| `keyless` | 0 | yes | no (fixtures are Python request hashes) | none | free | Every pull request: replay evals, tutorials, the no-key compose overlay | Replay regression, detector recall. Not a quality measure |
| `openai` | 1 | yes | yes | `OPENAI_API_KEY` | per token | Stub-tested wiring. No live or recorded run through api.openai.com. `gpt-5.1` is unmeasured here | Live quality and safety, capability check, cost |
| `azure-key` | 1 | yes | yes | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY` | per token | The replay fixtures were recorded through Azure on gpt-4.1. Capability probes passed on gpt-5.2, 5.4, 5.4-mini and 5.6 at API version 2025-01-01-preview ([results](model-providers.md#capability-check-results)); gpt-5.1 and this profile's API version were not probed. The 5.6 deployments reject temperature 0.2, so keep `LLM_TEMPERATURE=` empty | Live quality and safety, capability check, cost |
| `azure-identity` | 1 | yes | yes | `AZURE_OPENAI_ENDPOINT`; role "Cognitive Services OpenAI User"; `az login` | per token | Stub-tested with a fake credential. Never run with a real Entra token | Live quality and safety, capability check, cost |
| `anthropic` | 1 | yes | yes | `ANTHROPIC_API_KEY`, plus `EMBEDDING_API_KEY` (an OpenAI key) | per token, two accounts | **Stub-tested only.** Never run against the live API | Live quality and safety, capability check, cost |
| `gemini` | 1 | yes | yes | Gemini key as `OPENAI_API_KEY`, plus `EMBEDDING_API_KEY` | per token (free tier exists); cost shows null, no Gemini prices ship | **Stub-tested only.** No live call to Google | Live quality and safety, capability check |
| `ollama` | 1 | yes | yes | none (`ollama pull qwen2.5:14b nomic-embed-text`) | free | A two-tool loop tried by hand on three local models in 2026-08; capability probes ran on `qwen2.5:7b` (forced tool choice failed). The 768-wide embedding path is documented, not exercised. Not in CI | Live quality and safety, capability check. Expect lower scores |
| `llama-cpp` | 1 | yes | yes | none (two `llama-server` instances) | free | **Not verified.** Documented from llama.cpp only | Live quality and safety, capability check |
| `openrouter` | 1 | yes | yes | OpenRouter key as `OPENAI_API_KEY` and `EMBEDDING_API_KEY` | OpenRouter prices; cost shows null | **Not verified.** Generic compatible path; OpenRouter never called. Embeddings endpoint availability unchecked | Live quality and safety, capability check |
| `openai-compatible` | 1 | yes | yes | whatever your server wants; set the three `<set-me>` values | depends | **Not verified.** vLLM, LM Studio, LiteLLM never run here | Live quality and safety, capability check |
| `azure-gateway` | 2 | yes | yes | `AZURE_OPENAI_KEY` (the gateway's key), `AZURE_OPENAI_ENDPOINT` (the gateway URL) | tokens plus the gateway | **Not verified, and the gateway is not built** (milestone M6). Today it is `azure-key` at another URL | Live quality and safety, capability check, cost |
| `foundry` | 2 | yes | yes | `AZURE_OPENAI_ENDPOINT`; identity as above | per token | **Not verified.** Same wire as `azure-identity`. Foundry project, evaluations, tracing and hosted agents are not built (M5 to M7) | Live quality and safety, capability check, cost |

`LLM_API` (`responses` or `chat_completions`) is Python-only. The .NET stack always speaks Chat Completions, so a
profile that sets `chat_completions` for Python gives .NET the same behaviour it always had.

The suggested judge in each header is a starting point, never the model under test, and **not calibrated**. The
reference judge is fixed once, after calibration, in the eval work.

## The two commands per profile

Start the stack, then check it. The doctor needs no running stack for its model, database and Redis checks; add
`--services` to make an unreachable specialist a failure.

| Profile | Start | Check |
|---|---|---|
| `keyless` | `./scripts/dev.sh --profile keyless` | `uv run --project agents/python python -m scripts.doctor --profile keyless` |
| `openai` | `./scripts/dev.sh --profile openai` | `... scripts.doctor --profile openai` |
| `azure-key` | `./scripts/dev.sh --profile azure-key` | `... scripts.doctor --profile azure-key` |
| `azure-identity` | `./scripts/dev.sh --profile azure-identity` | `... scripts.doctor --profile azure-identity` |
| `anthropic` | `./scripts/dev.sh --profile anthropic` | `... scripts.doctor --profile anthropic` |
| `gemini` | `./scripts/dev.sh --profile gemini` | `... scripts.doctor --profile gemini` |
| `ollama` | `./scripts/dev.sh --profile ollama` | `... scripts.doctor --profile ollama` |
| `llama-cpp` | `./scripts/dev.sh --profile llama-cpp` | `... scripts.doctor --profile llama-cpp` |
| `openrouter` | `./scripts/dev.sh --profile openrouter` | `... scripts.doctor --profile openrouter` |
| `openai-compatible` | `./scripts/dev.sh --profile openai-compatible` | `... scripts.doctor --profile openai-compatible` |
| `azure-gateway` | `./scripts/dev.sh --profile azure-gateway` | `... scripts.doctor --profile azure-gateway` |
| `foundry` | `./scripts/dev.sh --profile foundry` | `... scripts.doctor --profile foundry` |

`...` stands for `uv run --project agents/python python -m`. On PowerShell use `./scripts/dev.ps1 -Profile <name>`.
Add `--dotnet` to `dev.sh` (or `-Dotnet`) for the .NET stack, except with `keyless`. For a live eval of the same
setup: `cd agents/python && uv run python -m evals.run_evals --profile <name> ...`.

## Precedence

From strongest to weakest:

1. A variable **exported in your shell**.
2. The **profile**.
3. **`.env`**.
4. The defaults in `shared/config.py`.

A profile beats `.env` on purpose. A `.env` copied from `.env.example` carries provider values you never chose,
and a half-applied profile (new model, old base URL) fails in confusing ways. When a profile replaces a value
from `.env`, `dev.sh` says which. To keep your own value, export it in the shell.

Secrets are never in a profile: keys and tokens stay in `.env`. The profile for a local server does set a dummy
`OPENAI_API_KEY` (`ollama`, `not-needed`), which also keeps your real OpenAI key from being sent to a local host.

A value of `<set-me>` means the profile cannot know it (your server's URL, your model name). It leaves `.env` and
the shell alone for that variable, and `dev.sh` and the doctor refuse to continue while neither has a value.

## What the doctor checks

```bash
uv run --project agents/python python -m scripts.doctor --profile ollama
uv run --project agents/python python -m scripts.doctor --json     # for scripts; exit 1 on any hard failure
```

It prints the effective configuration with secrets shown only as `set` or `empty` and the source of each value
(shell, profile, `.env`, default), then checks:

- required settings for the provider, and the embedding settings (including a chat key that would travel to the
  wrong host);
- the model endpoint and, when separate, the embedding endpoint, with a `GET /models`-style request. No tokens are
  spent. A rejected key is a failure; a 404 on `/models` is a warning, because compatible servers differ;
- the price of the model in `config/pricing/models.yaml`, and whether the pricing paths exist;
- PostgreSQL (and that the embedding columns match `EMBEDDING_DIM`), Redis, and each `AGENT_REGISTRY` entry;
- the profile's "verified how" line.

The capability check (tool calling, streaming, JSON output, context length) is the eval work's
`evals.provider_check`. Until that exists the doctor prints a line marked `STUB`.

Every service logs one line at startup with the same facts, with no secret, so a log is enough to see what a
service ran with:

```text
startup service=product-discovery stack=python profile=ollama provider=openai model=qwen2.5:14b llm_api=chat_completions llm_host=host.docker.internal:11434 embedding_provider=openai_compatible embedding_model=nomic-embed-text auth_mode=local pricing=shipped budget_mode=observe
```

## Gotchas

- **Local servers from containers.** The containers reach your machine as `host.docker.internal`. `dev.sh` rewrites
  `localhost` URLs from a profile for the containers; the doctor and the eval CLI run on the host and use
  `localhost`. If you export your own `LLM_BASE_URL`, `dev.sh` does not rewrite it.
- **Embedding width.** `ollama` and `llama-cpp` use 768-wide `nomic-embed-text` vectors, and the database column is
  `vector(1536)`. `dev.sh --profile` runs `scripts/reset_embeddings.py --if-needed` before seeding, which deletes
  stored vectors when the width changes. The seeder never calls an embedding API, so run
  `uv run python -m scripts.generate_embeddings` (from `agents/python`) once to embed the catalogue; until then
  semantic search uses full-text only. Switching back to a 1536 profile reverses it. `--demo --profile ...` does
  not run this step.
- **`replay-evals.sh` is the keyless gate.** It accepts `--profile keyless` and refuses any other profile instead of
  ignoring it: a green replay run says nothing about another provider.
- **Windows.** An environment variable cannot be empty on Windows, so a profile line such as `LLM_BASE_URL=` cannot
  blank a stale value left in `.env`. Edit `.env` if one gets through. `dev.ps1` has not been run by the author of
  this feature (no PowerShell was available); treat its first run as a test.
- **Pricing in containers.** The pricing directory is mounted at `/pricing`. Set `PRICING_OVERRIDES_FILE` to a path
  under it (for example `/pricing/overrides.yaml`) to price a local model at 0 or add a negotiated rate; a host
  path means nothing inside a container. Restart, no rebuild.
- **Anthropic, Gemini, OpenRouter, llama.cpp, vLLM, LM Studio, LiteLLM, the gateway and Foundry have never
  been run live from this repository.** Treat their profiles as configured, not supported, until the table says
  otherwise.

## Adding a profile

Copy the closest file, keep the header fields (`@tier`, `@summary`, `@key`, `@cost`, `@dotnet`, `@verified`,
`@evals`, `@judge`, `@judge-env`), set chat, embeddings and model together, add it to the table above, and run
`uv run pytest tests/test_run_profiles.py`. The test fails if a key is missing from `Settings`, `.env.example` or
the compose files, or if a value looks like a secret.
