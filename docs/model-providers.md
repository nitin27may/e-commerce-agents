# Model providers

How to point the stack at each model provider, what has actually been exercised, and what has not.
The variable reference lives in [configuration.md](configuration.md); this page is the practical
side: copy a block, know what breaks.

Everything below is selected by `LLM_PROVIDER` in `.env`. Embeddings are a separate setting group
and are covered [on their own](#embeddings).

## Verified versus untested

Read this table before choosing a provider. "Verified" means a test in this repo exercises it, and
the second column says how. Nothing in CI calls a live model; the only live calls ever made were
recording sessions against Azure OpenAI.

| Provider | How to select | Verified how | Not verified |
|---|---|---|---|
| `replay` | `LLM_PROVIDER=replay` | Every CI eval and tutorial run; 102 fixtures recorded on `gpt-4.1-2025-04-14` | Anything about a model other than the one recorded |
| `azure` | `LLM_PROVIDER=azure` | Fixtures were recorded through it | Current model availability for new subscriptions (see caveats) |
| `openai` | `LLM_PROVIDER=openai` | Stubbed-transport tests of the client wiring | No recorded run in this repo used api.openai.com |
| `anthropic` | `LLM_PROVIDER=anthropic` | Stubbed `httpx2` transport (Python) and stub handler (.NET), `tests/test_anthropic_provider.py` | **Never run against the live API.** Tool-choice quality, prompt caching, real error shapes, streaming edge cases |
| OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, OpenRouter, LiteLLM) | `LLM_PROVIDER=openai` + `LLM_BASE_URL` | Ollama and the 2-tool loop on a few local models were tried by hand (see the note in `.env.example`); the wiring has stubbed tests | vLLM, OpenRouter and LiteLLM have not been run by this repo at all |
| Gemini compatible API | `openai` + Gemini's `LLM_BASE_URL` + `LLM_API=chat_completions` | `tests/test_gemini_compat_endpoint.py`, stubbed | No live call to Google |

## replay (no key, no network)

```dotenv
LLM_PROVIDER=replay
```

Serves recorded fixtures keyed by the exact request (messages, tool schemas, instructions). It is
what CI, the keyless demo and the tutorials use. A request with no fixture fails with a replay
miss, not a model answer, so it is the wrong choice for anything you have not recorded. The key
ignores the model name, so fixtures replay under any provider setting. See
`agents/python/evals/fixtures/README.md` for recording.

## Azure OpenAI

```dotenv
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_KEY=...
AZURE_OPENAI_DEPLOYMENT=<your deployment name>
AZURE_OPENAI_API_VERSION=2025-03-01-preview
```

Azure is always Chat Completions on the Python stack. The deployment name is what you called the
deployment, not necessarily the model id. For production, prefer managed identity over a key; the
Azure deployment path in [azure-deployment.md](azure-deployment.md) does that.

### Foundry

The `azure` provider pointed at an Azure AI Foundry (AIServices) account is Foundry for OpenAI models. The account the Bicep creates (`infra/`, see [azure-deployment.md](azure-deployment.md#foundry-project-optional)) is exactly that, so no separate provider exists and we do not add `LLM_PROVIDER=foundry`. A Foundry project (evaluations, tracing) is an optional extra there, not a different way of calling the model. A provider would only be warranted for a non-OpenAI catalogue model or a project-scoped client.

## OpenAI

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4.1
```

The Python stack uses the Responses API here (`LLM_API=responses`, the default). The .NET stack
always uses Chat Completions.

## Anthropic (Claude)

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-5-5
EMBEDDING_PROVIDER=openai          # Claude has no embeddings API
```

`LLM_MODEL` is ignored for this provider. Claude is wired on both stacks and covered by
stubbed-transport tests only. Cost figures are meaningless until prices are added to
`shared/cost.py`: Claude models fall back to the default entry. Full table in
[configuration.md](configuration.md#anthropic-claude-python-and-net-stacks).

## Any OpenAI-compatible endpoint

One mechanism covers all of these: `LLM_PROVIDER=openai`, `LLM_BASE_URL` pointing at the server, a
non-empty `OPENAI_API_KEY` (many servers ignore its value), and `LLM_API=chat_completions` when the
server has no `/responses` route. A 404 on `/responses` is the symptom of a missing
`LLM_API=chat_completions`.

| Server | `LLM_BASE_URL` | Notes |
|---|---|---|
| Ollama | `http://localhost:11434/v1` | Start with `OLLAMA_CONTEXT_LENGTH=64000`; the 4K default silently truncates agent loops. Inside a container use `http://host.docker.internal:11434/v1` |
| LM Studio | `http://localhost:1234/v1` | Load a model in the server tab first |
| vLLM | `http://<host>:8000/v1` | Start it with tool calling enabled for your model (its tool-call parser flag is model specific; check vLLM's docs) |
| OpenRouter | `https://openrouter.ai/api/v1` | Key in `OPENAI_API_KEY`; the model id is OpenRouter's `vendor/model` form |
| LiteLLM proxy | `http://localhost:4000/v1` | See below |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | Gemini key in `OPENAI_API_KEY`; set `EMBEDDING_*` explicitly |

Choose a model that does tool calling reliably. Every specialist reads the database through tools;
a model with weak function calling does not error, it stops calling tools and answers from memory.
If answers stop mentioning real products or orders, check that first.

### LiteLLM

LiteLLM is a proxy that speaks the OpenAI API in front of many providers, which makes it the way
to reach a provider this repo has no code for. This repo ships no LiteLLM compose profile; run it
however you run other services and point the stack at it:

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:4000/v1      # http://host.docker.internal:4000/v1 from a container
LLM_API=chat_completions
OPENAI_API_KEY=<the proxy's key, or any non-empty string if it has none>
LLM_MODEL=<a model_name from the proxy's config>
```

Untested here. Two things to check yourself: that the routed model supports tool calls, and the
cache-token caveat below.

## Gateways

Behind an AI gateway (APIM, LiteLLM, OpenRouter, any proxy) the gateway sees only HTTP, so every model call
carries `x-agent-name`, `x-run-id` and `x-eval-run-id` for it to attribute tokens and apply limits per agent and
per run. It is on by default (`GATEWAY_HEADERS_ENABLED`), covers every provider shape on both stacks and
embeddings, and sends no user identity or secret; details in
[configuration.md](configuration.md#gateway-attribution-headers-gateway_headers_enabled-eval_run_id).
Known limits: the run id is minted per process (not forwarded over A2A), and a header asserted by the caller is
not proof of who called, which is why per-app identities belong in the gateway's own authentication. Untested
against a real gateway: only stub transports have seen these headers. Name the gateway in `EVAL_GATEWAY` so an
eval record says so.

## Embeddings

Embeddings are configured separately from chat, and defaulting to the chat provider is often wrong:

```dotenv
EMBEDDING_PROVIDER=openai_compatible   # openai | azure | openai_compatible | replay
EMBEDDING_BASE_URL=http://localhost:11434/v1
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIM=768
```

- Anthropic has no embeddings API. With no embedding provider configured, the stack starts and
  semantic search falls back to its full-text arm.
- A Gemini or other compatible chat base URL is not an embeddings endpoint. Left empty,
  `EMBEDDING_PROVIDER` follows the chat provider and sends `text-embedding-3-small` requests there.
- The dimension is a database property (`vector(1536)` in the baseline). Changing it is an explicit
  step: `uv run --project agents/python python scripts/reset_embeddings.py --dim N`, which discards
  stored vectors. Details in [configuration.md](configuration.md#embeddings-are-configured-separately).

## Known caveats

- **Cache-token fields can be remapped by a proxy.** Usage and cost accounting reads the cached
  input count from the provider's response (`shared/run_usage.py` accepts MAF's
  `cache_read_input_token_count` and the wire's `cached_input_token_count`). A proxy such as
  LiteLLM or OpenRouter may rename or drop those fields when it translates between providers, so
  cached tokens can read as zero and cost can look higher than the bill. The mechanism for a cached
  price exists; no cached-input rate has been entered (E-2d).
- **Anthropic has never run live.** Treat it as a configured, stub-tested option, not a supported
  one, until a keyed smoke run is recorded.
- **Model availability moves.** The default `gpt-4.1` and its Azure deployment name are what the
  fixtures were recorded on. Per the maintainer, gpt-4.1 is closed to new Azure customers, and the
  gpt-5.x family rejects a `temperature` parameter. Neither claim was re-checked against Azure
  documentation for this page. The stack sends `LLM_TEMPERATURE` (default 0.2) on every run, so a
  model that rejects it fails until a change that lets `LLM_TEMPERATURE` be left empty (not sending
  the parameter) lands; that change is in flight in another pull request. Until then, do not point
  the stack at a model that rejects temperature.
- **Per-agent models** (`model:` block in the prompt YAML) need that provider's own credentials,
  and the global `LLM_BASE_URL` is not inherited by an agent that switches provider. See
  [configuration.md](configuration.md#choosing-a-model-per-agent-model-in-the-prompt-yaml).
- **Replay scores do not transfer.** The eval scorecard (`docs/eval-scorecard.md`) describes a recorded
  gpt-4.1 session. Switching provider changes behaviour; it does not change those numbers. Measuring
  another model needs a fresh, keyed recording, which is human-dispatch only.

## Gotchas checklist

1. 404 on `/responses`: set `LLM_API=chat_completions`.
2. `LLM_MODEL` set but ignored: you are on `anthropic`, which reads `ANTHROPIC_MODEL`.
3. Semantic search empty after switching provider: set `EMBEDDING_*` explicitly.
4. Answers with no real data: the model is not calling tools.
5. Cost looks wrong on a proxy or on Claude: unpriced model or remapped cache fields.
