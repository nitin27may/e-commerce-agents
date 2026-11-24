"""Pydantic Settings for E-Commerce Agents.

Every env var used anywhere in the codebase is declared here. Downstream
modules never call `os.environ` directly — they import `settings` or one
of the factories in `shared.factory`.

Env-var aliases:
- `AZURE_OPENAI_KEY` (current) and `AZURE_OPENAI_API_KEY` (MAF convention)
  both bind to the same field.
- `AZURE_OPENAI_DEPLOYMENT` (current) and `AZURE_OPENAI_DEPLOYMENT_NAME`
  (MAF convention) both bind to the same field.
Existing names take precedence; the aliases exist so MAF docs read naturally
without breaking our current `.env` files.
"""

import logging
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Secrets below this length are considered unsafe for HS256 and are also
# the shape of the shipped `.env.example` placeholders. 32 bytes (256 bits)
# matches both HS256's minimum key size and MAF's .NET validator.
_MIN_SECRET_BYTES = 32
_UNSAFE_SECRET_DEFAULTS = {
    "change-me-in-production",
    "change-me-generate-a-random-256-bit-key",
    "change-me-in-production-use-random-256-bit-key",
    "agent-internal-secret",
    "agent-internal-shared-secret",
    "dev-oauth-seed-change-me",
}

# Inbound injection-detection providers that are actually implemented today.
# "azure_content_safety" is a reserved/planned value — see docs/security-guide.md
# ("Azure AI Content Safety — Optional Integration") — but
# shared/guardrails/azure_shield.py does not exist yet, so selecting it must
# fail fast rather than silently falling back to the regex provider.
_SUPPORTED_INJECTION_PROVIDERS = {"regex"}
_NOT_YET_IMPLEMENTED_INJECTION_PROVIDERS = {"azure_content_safety"}
_SUPPORTED_GROUNDING_MODES = {"off", "observe", "annotate", "enforce"}
_SUPPORTED_COST_BUDGET_MODES = {"off", "observe", "enforce"}
_SUPPORTED_OUTPUT_MODERATION_MODES = {"off", "observe", "enforce"}

# Resolve .env once, relative to the repo root, so the eval/seed scripts pick
# it up regardless of the cwd they're launched from. Inside the Docker image
# there is no .env at the repo root — containers get their config from the
# compose `environment:` block — so a missing file is fine.
# config.py lives at <repo>/agents/python/shared/, so the repo root is 3 levels
# up — parents[2] stops at <repo>/agents and silently resolved to a path with no
# .env, which meant pydantic's env_file loading never fired and Settings fell back
# to defaults even when a real .env was present.


def _resolve_repo_root(config_path: Path) -> Path:
    """Repo root 3 levels above this file — but inside the Docker image,
    config.py is copied flatly to /app/shared/config.py (the agent
    Dockerfile's build context is ./agents, so agents/python/ never exists
    as a path component in the image), leaving only 2 real parents. Indexing
    parents[3] there raises IndexError and crashes every container at import
    time. Fall back to the immediate parent in that case; it won't contain a
    .env either, which is exactly the "missing file is fine" behavior above.
    """
    parents = config_path.resolve().parents
    return parents[3] if len(parents) > 3 else config_path.resolve().parent


_REPO_ROOT = _resolve_repo_root(Path(__file__))
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    # ── Database ────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents"

    # ── Redis ───────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"

    # ── Rate limiting (shared/rate_limit.py, Phase 6.3) ────────────────
    # Redis was provisioned here from day one but had no consumer until
    # this. Defaults on, matching this repo's posture for the other
    # always-on safety features (GUARDRAILS_ENABLED, HITL_ENABLED default
    # True too) — an agentic chat endpoint reachable by anonymous
    # storefront traffic with zero rate limit is a real cost-abuse vector,
    # not a hypothetical one.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_MAX_REQUESTS: int = 30
    RATE_LIMIT_WINDOW_SECONDS: float = 60.0
    # Credential endpoints (signup / login / refresh) get their own buckets,
    # separate from chat: a password-guessing or account-spam loop should not
    # spend the chat budget, and a chat user hitting the chat limit should not
    # be locked out of logging in. Two layers, so one caller cannot starve others:
    #   * RATE_LIMIT_AUTH_MAX_REQUESTS - the tight budget, per (client address,
    #     target email) for login and signup (a single target cannot be brute
    #     forced) and per user for refresh (read from the verified token).
    #   * RATE_LIMIT_AUTH_IP_MAX_REQUESTS - a much looser address-only ceiling
    #     over all three routes. Behind the default web proxy every browser user
    #     shares one address, so this must stay far above what a crowd of real
    #     users produces; it only stops a single script from flooding the service.
    RATE_LIMIT_AUTH_MAX_REQUESTS: int = 10
    RATE_LIMIT_AUTH_IP_MAX_REQUESTS: int = 300
    RATE_LIMIT_AUTH_WINDOW_SECONDS: float = 60.0
    # How many reverse proxies in front of the orchestrator append to
    # X-Forwarded-For and are trusted to do so. The client address is the entry
    # that many places from the RIGHT of the header (1 = the right-most entry,
    # i.e. what the Next.js proxy appended). Entries further left were supplied
    # by the caller and are never trusted. 0 (the code default, safe for a
    # directly exposed orchestrator) ignores the header entirely and uses the
    # TCP peer address. The shipped compose files and Bicep set it for the
    # stack they ship.
    RATE_LIMIT_TRUSTED_PROXY_HOPS: int = 0
    # Optional peer gate: comma-separated IPs / CIDRs allowed to set
    # X-Forwarded-For. When set, the header is honoured only if the TCP peer is
    # in the list; any other peer is keyed by its own address, so a caller that
    # reaches the orchestrator directly cannot choose its bucket. Empty = no
    # gate (hops alone decide). A value that parses to nothing trusts nobody.
    RATE_LIMIT_TRUSTED_PROXIES: str = ""

    # ── CORS ────────────────────────────────────────────────────────
    # Comma-separated list of origins allowed to call the orchestrator from a
    # browser. The web app reaches the API through its own same-origin proxy
    # (web/src/app/api/[...path]/route.ts), so the default only needs to cover
    # `NEXT_PUBLIC_API_URL` direct-call setups. Credentials (cookies) are never
    # allowed: auth is a Bearer token in a header.
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"

    # ── LLM ─────────────────────────────────────────────────────────
    LLM_PROVIDER: str = "openai"  # openai | azure | anthropic | replay
    LLM_MODEL: str = "gpt-4.1"
    # Optional base_url override for the OpenAI-compatible `openai` provider —
    # points OpenAIChatClient at any OpenAI-compatible endpoint (GitHub Models,
    # OpenRouter, vLLM, LM Studio, Azure AI Foundry's OpenAI-compatible route)
    # instead of api.openai.com. Unset by default; only takes effect when
    # LLM_PROVIDER=openai. See tutorials/00-setup/README.md for the GitHub
    # Models path, which is free with a GitHub PAT.
    LLM_BASE_URL: str = ""
    # Which OpenAI wire API the `openai` provider speaks. `responses` (default)
    # uses the Responses API (`POST /responses`), which api.openai.com supports on
    # every model. `chat_completions` uses `POST /chat/completions` — set it for
    # OpenAI-compatible endpoints that have no /responses route (Gemini's
    # compatible endpoint, some vLLM / Ollama versions). The `azure` provider
    # always uses Chat Completions and ignores this.
    LLM_API: Literal["responses", "chat_completions"] = "responses"
    # The eval judge (evals/judge.py). Same vocabulary as the chat provider: JUDGE_PROVIDER is
    # openai | azure | anthropic | replay, JUDGE_MODEL the model (an Azure deployment for azure),
    # JUDGE_BASE_URL an OpenAI-compatible endpoint (or the Azure endpoint), JUDGE_API_KEY its key
    # (inherited only when the judge uses the same provider as the model under test), JUDGE_API
    # responses | chat_completions for openai (empty follows LLM_API). Evals refuse to default the
    # judge to the model under test: set these, or JUDGE_ALLOW_SELF=true to accept a "self-judged" run.
    JUDGE_PROVIDER: str = ""
    JUDGE_MODEL: str = ""
    JUDGE_BASE_URL: str = ""
    JUDGE_API_KEY: str = ""
    JUDGE_API: Literal["", "responses", "chat_completions"] = ""
    JUDGE_ALLOW_SELF: bool = False
    # Attribution headers (x-agent-name, x-run-id, x-eval-run-id) on every model call, for an AI gateway
    # (shared/gateway_headers.py). EVAL_RUN_ID is the run id an eval process stamps on its calls.
    GATEWAY_HEADERS_ENABLED: bool = True
    EVAL_RUN_ID: str = ""
    # replay-provider settings — see shared/replay_client.py. RECORD=true makes
    # a missing fixture fall through to a real call (via REPLAY_RECORD_PROVIDER)
    # and persist the result instead of raising.
    REPLAY_RECORD_PROVIDER: str = "openai"  # openai | azure | anthropic — used only when RECORD=true
    REPLAY_FIXTURES_DIR: str = "evals/fixtures/replay"  # relative to the process cwd (agents/python)
    RECORD: bool = False
    # The no-key demo tour (#86): suggested prompts the web UI offers as chips, and
    # `POST /api/demo/reset` for the tour user. Off by default, so a normal install
    # serves 404 on both and the UI shows nothing. DEMO_TOUR_PATH is relative to
    # agents/python (an absolute path is used as is).
    DEMO_TOUR_ENABLED: bool = False
    DEMO_TOUR_PATH: str = "config/demo/tour.yaml"
    # Sampling temperature for every agent run. Defaults LOW for consistent,
    # reproducible answers — at the provider default (~1.0) the same query can
    # yield categorically different results (e.g. finding vs. not finding a
    # borderline-priced product). Raise toward 1.0 for more varied phrasing.
    # An EMPTY value means "send no temperature at all": gpt-5.x models reject the parameter.
    LLM_TEMPERATURE: float | None = 0.2
    # asyncpg pool bounds per process. A managed Postgres has a hard max_connections, and every
    # service holds its own pool, so Azure sets these low (demo 1/4). Defaults are the old literals.
    DB_POOL_MIN_SIZE: int = Field(default=5, ge=0)
    DB_POOL_MAX_SIZE: int = Field(default=20, ge=1)
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    # Embeddings are decoupled from the chat provider (#96). Empty EMBEDDING_PROVIDER follows
    # LLM_PROVIDER, exactly as before; otherwise openai | azure | openai_compatible | replay.
    # EMBEDDING_BASE_URL / EMBEDDING_API_KEY, when set, replace LLM_BASE_URL / OPENAI_API_KEY for
    # embeddings only. EMBEDDING_DIM must equal the vector(N) column (see scripts/reset_embeddings.py).
    EMBEDDING_PROVIDER: str = ""
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_DIM: int = 1536
    OPENAI_API_KEY: str = ""
    # LLM_PROVIDER=anthropic (Claude), through MAF's agent-framework-anthropic connector. The model is
    # ANTHROPIC_MODEL, never LLM_MODEL: the compose files forward LLM_MODEL with a gpt-4.1 default that
    # Anthropic would reject. The default id matches the .NET stack's AgentSettings.DefaultAnthropicModel.
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-5-5"

    AZURE_OPENAI_ENDPOINT: str = ""

    # Accept both the repo's existing name (AZURE_OPENAI_KEY) and the MAF-docs
    # name (AZURE_OPENAI_API_KEY). Pydantic prefers the first alias listed
    # when both are set.
    AZURE_OPENAI_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_OPENAI_KEY", "AZURE_OPENAI_API_KEY"),
    )

    AZURE_OPENAI_DEPLOYMENT: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME"),
    )

    AZURE_OPENAI_API_VERSION: str = "2025-03-01-preview"
    # key (default): AZURE_OPENAI_KEY. identity: no key at all, a Microsoft Entra token for the managed identity
    # (AZURE_CLIENT_ID picks a user-assigned one; empty uses DefaultAzureCredential). Needs the
    # "Cognitive Services OpenAI User" role on the resource.
    AZURE_OPENAI_AUTH: Literal["key", "identity"] = "key"
    AZURE_CLIENT_ID: str = ""
    AZURE_EMBEDDING_DEPLOYMENT: str = ""

    # ── Auth ────────────────────────────────────────────────────────
    JWT_SECRET: str = "change-me-in-production"
    AGENT_SHARED_SECRET: str = "agent-internal-secret"

    # ── OAuth2 (self-hosted Authorization Server, optional) ──────────
    # AUTH_MODE=local (default) keeps today's HS256 JWT + shared-secret
    # behavior unchanged. AUTH_MODE=oauth routes user login and A2A/MCP
    # auth through the self-hosted auth-server (agents/python/auth_server/),
    # which issues RS256 tokens validated via its JWKS. No external IdP.
    AUTH_MODE: str = "local"  # local | oauth

    AUTH_SERVER_ISSUER: str = "http://localhost:8090"
    AUTH_SERVER_JWKS_URL: str = "http://localhost:8090/.well-known/jwks.json"
    AUTH_SERVER_TOKEN_URL: str = "http://localhost:8090/oauth/token"
    AUTH_ACCESS_TOKEN_TTL: int = 3600  # seconds
    AUTH_REFRESH_TOKEN_TTL: int = 604800  # 7 days
    # AUTH_MODE=local only: /api/auth/refresh issues a new refresh token on
    # every use and revokes the whole token family if an already-used one is
    # presented again (refresh_tokens table, migration 0003). false restores
    # the old behaviour of returning only a new access token. Has no effect
    # in AUTH_MODE=oauth — rotation there is the auth-server's job and is not
    # implemented (docs/parity-matrix.md).
    AUTH_REFRESH_ROTATION: bool = True
    # Absolute lifetime of a refresh-token family, counted from the login that started it
    # (migration 0005). Rotation hands every successor a fresh 7-day token, so without a cap a
    # session that keeps refreshing never ends; once this has passed, refresh is a 401 and the
    # user logs in again.
    AUTH_REFRESH_FAMILY_MAX_DAYS: int = 30
    AUTH_JWKS_CACHE_TTL: int = 900  # resource-server JWKS cache
    AUTH_RSA_KEY_SIZE: int = 2048  # auth-server only
    AUTH_SIGNING_KEY_ENCRYPTION_KEY: str = ""  # optional KEK for the private key at rest
    # Signing-key rotation (auth-server only, AUTH_MODE=oauth): how long a rotated-out key stays published in the
    # JWKS. 0 (default) derives it as AUTH_ACCESS_TOKEN_TTL + AUTH_KEY_CLOCK_SKEW_SECONDS, i.e. until the last
    # token it signed has expired and any verifier's clock skew has passed. Set it explicitly only to lengthen it.
    AUTH_KEY_RETIRE_AFTER_SECONDS: int = 0
    AUTH_KEY_CLOCK_SKEW_SECONDS: int = 300

    # Per-service OAuth client identity (client-credentials grant).
    OAUTH_CLIENT_ID: str = ""  # defaults to the service name when empty
    OAUTH_CLIENT_SECRET: str = ""  # prod override; dev derives from OAUTH_SEED_KEY
    OAUTH_SEED_KEY: str = "dev-oauth-seed-change-me"  # dev shared knob; unsafe in prod

    AUTH_ORCH_AUDIENCE: str = "ecommerce-orchestrator"
    AUTH_AGENT_AUDIENCE: str = "ecommerce-agents"

    # The AS's own resource identifier — used only by the (optional, gated)
    # dynamic client registration endpoint below, where the AS itself is the
    # protected resource being called (aud on a `client:register`-scoped
    # token), distinct from the orchestrator/agent/MCP audiences above.
    AUTH_SERVER_AUDIENCE: str = "ecommerce-auth-server"

    # RFC 7591 dynamic client registration (`POST /oauth/register`), off by
    # default — this app's client registry is otherwise fixed/seeded
    # (scripts/seed.py). When enabled, registration itself still requires a
    # bearer token scoped `client:register` (see auth_server/register.py);
    # this flag only gates whether that route exists at all.
    AUTH_ALLOW_DYNAMIC_REGISTRATION: bool = False

    # ── MCP resource-server auth (independent of MCP_ENABLED) ────────
    MCP_AUTH_ENABLED: bool = False
    MCP_PRODUCT_AUDIENCE: str = "mcp-product"
    MCP_INVENTORY_AUDIENCE: str = "mcp-inventory"
    MCP_PRODUCT_REQUIRED_SCOPE: str = "mcp:product"
    MCP_INVENTORY_REQUIRED_SCOPE: str = "mcp:inventory"
    MCP_PRODUCT_RESOURCE_URL: str = "http://localhost:9000/mcp"
    MCP_INVENTORY_RESOURCE_URL: str = "http://localhost:9001/mcp"

    # ── Agent Registry (A2A endpoint map) ───────────────────────────
    AGENT_REGISTRY: str = "{}"
    # Which wire the *calling* side (orchestrator, handoff mesh) speaks to a specialist: the original
    # `/message:send` + `/message:stream` pair (`legacy`, the default) or A2A 1.0 JSON-RPC at `/a2a/v1`
    # (`a2a`, see shared/a2a_server.py). Every Python specialist serves both regardless; .NET
    # specialists serve only `legacy` until B-13. Anything else is rejected at startup.
    A2A_TRANSPORT: Literal["legacy", "a2a"] = "legacy"
    # The origin a specialist advertises in its agent card. Empty derives it from the request
    # (scheme + Host), which is right on a compose network and for a local port; set it behind a
    # proxy that terminates TLS or rewrites the host.
    A2A_PUBLIC_BASE_URL: str = ""
    # Concurrently *running* A2A tasks a specialist process accepts, in total and per forwarded user. Over
    # either, a new message is refused with a JSON-RPC error instead of queueing: a task outlives the
    # connection that started it, so this is the bound on work a slow or dropped caller can leave behind.
    A2A_MAX_ACTIVE_TASKS: int = Field(default=32, ge=1)
    A2A_MAX_ACTIVE_TASKS_PER_CALLER: int = Field(default=8, ge=1)
    # How a specialist learns which user a call is for (B-14, shared/identity_token.py). `headers`: the
    # forwarded X-User-* headers, trusted on the strength of the inter-agent credential (the original way).
    # `token`: only a signed, short-lived, audience-bound X-User-Context token; a call without a valid one is
    # refused and the headers are ignored. `both` (default, for one release): the orchestrator sends both,
    # a specialist verifies a token when one is present (a header that contradicts it is refused) and falls
    # back to the headers when there is none, so a mixed or half-upgraded deployment keeps working.
    A2A_IDENTITY: Literal["headers", "token", "both"] = "both"
    # The key the token is signed with, HKDF-derived. Empty uses AGENT_SHARED_SECRET (nothing new to distribute).
    # Set it to a value different from the shared secret so that holding X-Agent-Secret alone no longer lets a
    # caller choose its user. Must be identical on the orchestrator and every specialist, on both stacks.
    A2A_IDENTITY_KEY: str = ""
    # Token lifetime. A verifier also refuses any token living longer than 15 minutes.
    A2A_IDENTITY_TTL_SECONDS: int = Field(default=300, ge=30, le=900)

    # ── Telemetry ───────────────────────────────────────────────────
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:18889"
    OTEL_SERVICE_NAME: str = "ecommerce"
    GENAI_CAPTURE_CONTENT: bool = False

    # ── Langfuse (optional parallel OTel sink) ───────────────────────
    # When enabled, traces are sent to Langfuse alongside the Aspire sink.
    # Requires a Langfuse account; free tier is sufficient for a demo.
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # ── HITL (Human-in-the-Loop) approval queue ─────────────────────
    # When enabled, high-stakes tools (cancel_order, process_refund,
    # initiate_return, modify_order, place_backorder) require admin approval
    # before executing. Requests queue in the hitl_requests DB table and are
    # reviewed at /admin/approvals.
    HITL_ENABLED: bool = True
    # Before a gated call is parked for staff approval the customer is asked to confirm the exact action in plain
    # words, and only an explicit yes on a later turn lets it park (shared/customer_confirmation.py, #302). Separate
    # from HITL_ENABLED: staff approval still applies after the customer confirms. Off = park straight away.
    CUSTOMER_CONFIRMATION_ENABLED: bool = True
    # How long an unanswered confirmation stays answerable. A yes after this starts the question over.
    CUSTOMER_CONFIRMATION_TTL_SECONDS: int = 600
    # How long a resume claim ('processing' on hitl_requests) may be held before
    # another approver can take it over. A crash between claim and release used to
    # strand the row forever; claimed_at (migration 0002) makes that detectable.
    # Must comfortably exceed the longest legitimate resume.
    HITL_CLAIM_TTL_SECONDS: int = 600
    # Hard timeout on one resume execution. Must be shorter than the claim TTL, or a
    # stalled resume could still be running when another approver takes its claim.
    HITL_RESUME_TIMEOUT_SECONDS: int = 300

    # ── Outbox worker (python -m worker.main) ───────────────────────
    # Drains the outbox table: approval reminders, SLA escalation and timeouts. One Python worker
    # serves both stacks (they share the database). Disabled, the process idles and drains nothing.
    OUTBOX_WORKER_ENABLED: bool = True
    OUTBOX_POLL_INTERVAL_SECONDS: float = 2.0
    OUTBOX_BATCH_SIZE: int = 10
    # A row that fails this many times is parked (outbox.failed_at) instead of retried forever.
    OUTBOX_MAX_ATTEMPTS: int = 8
    # How long a claimed row stays owned; a crashed worker's rows are picked up again after it.
    OUTBOX_LEASE_SECONDS: int = 120
    # Retry delay = base * 2**(attempts - 1), capped.
    OUTBOX_BACKOFF_BASE_SECONDS: float = 5.0
    OUTBOX_BACKOFF_MAX_SECONDS: float = 600.0
    # Touched every loop; the container healthcheck reads its age.
    OUTBOX_WORKER_HEARTBEAT_FILE: str = "/tmp/outbox-worker.heartbeat"
    # The reminder fires this fraction of an approval's sla_hours after it enters review.
    APPROVAL_REMINDER_FRACTION: float = 0.5

    # ── Merchant proposals (#93) ─────────────────────────────────────
    # A self-rescheduling daily run through the outbox worker that generates proposals for every seller. Off by
    # default. The generation itself is deterministic and spends no model money today; the flag stays off so the
    # worker never writes seller-facing data nobody asked for, and so the keyed merchant-ops agent (M-2) can be
    # put behind the same switch later without changing what "off" means.
    MERCHANT_OPS_SCHEDULE_ENABLED: bool = False
    # UTC hour (0-23) of the daily run.
    MERCHANT_OPS_RUN_HOUR_UTC: int = 6
    # The model-driven merchant-ops specialist (#298, port 8086) is reachable by a direct call to its own service
    # whenever it runs. This flag decides whether the ORCHESTRATOR may route to it: off (the default), the agent is
    # dropped from the registry the orchestrator reads and neither its prompt nor the routing tool's description
    # mention it, so every recorded orchestrator request is byte-identical to what it was before the agent existed.
    MERCHANT_OPS_AGENT_ENABLED: bool = False

    # ── Event autonomy (#94) ─────────────────────────────────────────
    # Events (a shipment running late) handled by the outbox worker: inside an autonomy budget the system acts
    # alone (a goodwill store credit), outside it opens an approval. OFF by default; with it off the worker
    # consumes an event row and does nothing. No model is called either way. docs/event-autonomy.md.
    AUTONOMY_ENABLED: bool = False
    # The admin-only event simulator (POST /api/admin/events/simulate). Needs AUTONOMY_ENABLED too; there is no
    # production event producer, so with the simulator off no event reaches the engine at all.
    AUTONOMY_SIMULATOR_ENABLED: bool = False
    # Use this autonomy policy file instead of config/policies/autonomy.yaml (tests, demos with small caps).
    AUTONOMY_POLICY_PATH: str = ""
    # The ops-responder (#299): after the engine decides, a model drafts the customer-facing message (a short apology or
    # explanation) from the decision's facts. It only words the message: the amount and the tier are the engine's, the
    # draft is checked against the decision's facts (amounts, ids, day counts) and a failed check or a model error falls
    # back to the template. OFF by default; with it off no model is called anywhere in event autonomy. Needs
    # AUTONOMY_ENABLED and a configured LLM_PROVIDER.
    AUTONOMY_RESPONDER_ENABLED: bool = False

    # ── History compaction (B-15) ────────────────────────────────────
    # Keep only the last N message groups (a user turn with its assistant/tool messages) of the rehydrated
    # conversation history, in the specialist host and in the orchestrator's RunContext.history. 0 = off, and
    # with it off the history (and so the model request and its replay key) is exactly what it was.
    HISTORY_COMPACTION_KEEP_LAST_GROUPS: int = 0
    # ── end History compaction ───────────────────────────────────────

    # ── MCP integration (optional, flag-gated) ──────────────────────
    # When enabled, specialist agents connect to MCP servers for their data
    # layer instead of calling asyncpg directly. MCP servers must be running
    # at the configured URLs. The tools overlap but are not identical (field sets, one ranking): see
    # docs/mcp-integration.md, "Native versus MCP, measured".
    MCP_ENABLED: bool = False
    MCP_PRODUCT_SERVER_URL: str = "http://localhost:9000/mcp"
    MCP_INVENTORY_SERVER_URL: str = "http://localhost:9001/mcp"

    # ── General ─────────────────────────────────────────────────────
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── MAF v1 feature flags (all optional, safe defaults) ──────────
    # These switches back the Phase 7 refactor. Every flag defaults to the
    # behavior the app had before the refactor, so turning them on is opt-in.

    # Conversation-state backend:
    #   postgres — use the existing Postgres schema
    #   file     — write to MAF_SESSION_DIR
    #   memory   — ephemeral (tests)
    MAF_SESSION_BACKEND: str = "postgres"
    MAF_SESSION_DIR: str = "./.sessions"

    # Checkpoint-state backend for durable workflows.
    MAF_CHECKPOINT_BACKEND: str = "postgres"  # postgres | file | memory
    MAF_CHECKPOINT_DIR: str = "./.checkpoints"

    # Threshold (USD) above which the return/replace workflow pauses for a
    # human approval via HITL.
    RETURN_HITL_THRESHOLD: float = 500.0
    # The return lifecycle after approval (#95): label, then wait for the carrier's first scan, then an inspection.
    # Off, a return finishes at `finalize` exactly as before (graph, fixtures and .NET parity unchanged). The graph
    # is part of a checkpoint, so flip this only with no return paused.
    RETURN_LIFECYCLE_ENABLED: bool = False
    # How long a return may wait for the carrier's scan, and for support to inspect it, before it expires.
    RETURN_SCAN_TIMEOUT_DAYS: int = 14
    RETURN_INSPECTION_TIMEOUT_DAYS: int = 7

    # When true, handoff between specialists happens without intermediate
    # user events (mirrors today's behavior). false → emit a handoff event
    # for UI breadcrumbs / observability.
    HANDOFF_AUTONOMOUS_MODE: bool = True

    # Autonomous mode's contract is "if the agent does not hand off, feed it a
    # continuation prompt and run it again". MAF's default ceiling is 50 turns,
    # which is a very expensive way to discover that an agent cannot hand off:
    # this mode produced a 23,637-character monologue over 100-200 s before the
    # triage agent was made tool-free. Three turns covers hand-off, hand-back
    # and wrap-up; anything beyond that is the loop, not the conversation.
    HANDOFF_MAX_TURNS: int = 3

    # Default orchestration mode when a request doesn't specify one — see
    # orchestrator/modes/. "tool" (LLM calls call_specialist_agent) is the
    # only mode with no other prerequisites; "handoff" requires AGENT_REGISTRY
    # to be populated. Renamed from MAF_HANDOFF_MODE now that the mode
    # registry supports more than a tool/handoff binary choice; the old name
    # is accepted as an alias so existing .env files and docker-compose.yml
    # don't need to change immediately.
    ORCHESTRATION_MODE: str = Field(
        default="tool",
        validation_alias=AliasChoices("ORCHESTRATION_MODE", "MAF_HANDOFF_MODE"),
    )

    # Hard ceiling on a single SSE stream's wall-clock duration. The
    # orchestrator's chat-stream endpoint aborts the underlying generator
    # after this many seconds even if the LLM is still producing tokens.
    # Prevents runaway streams from holding a Starlette worker forever.
    MAF_STREAM_TIMEOUT_SECONDS: float = 120.0

    # Hard ceiling on the bytes we'll buffer per stream for the
    # persisted-message accumulator. A pathological model output that
    # exceeds this is truncated and an `[truncated]` marker is yielded
    # so the user sees the cutoff instead of a hung stream.
    MAF_STREAM_MAX_BYTES: int = 10_000_000

    # When true, CI regenerates docs/workflows/*.mmd and fails on drift.
    WORKFLOW_VISUALIZATION_ON_BUILD: bool = False

    # ── Guardrails (Track A — security) ─────────────────────────────
    # Master switch for the code-layer guardrails (output sanitization,
    # injection detection, role enforcement). Safe default: on.
    GUARDRAILS_ENABLED: bool = True

    # Neutralize stored / indirect injection in tool outputs before they
    # re-enter the model.
    GUARDRAILS_OUTPUT_SANITIZATION: bool = True

    # Observe-only by default: guardrail failures log and continue, and the
    # inbound injection detector counts without blocking. Flip to False (with
    # GUARDRAILS_BLOCK_ON_INJECTION) once the false-positive rate is measured.
    GUARDRAILS_FAIL_OPEN: bool = True

    # When True, the inbound injection detector refuses the run instead of
    # only logging. Independent of FAIL_OPEN so you can block injection while
    # keeping sanitization non-raising.
    GUARDRAILS_BLOCK_ON_INJECTION: bool = False

    # When True, validate forwarded x-user-email / x-user-role on the
    # inter-agent path and reject on anomaly (not just log).
    GUARDRAILS_STRICT_IDENTITY: bool = False

    # Inbound injection-detection provider: "regex" (default, zero-dependency)
    # or "azure_content_safety" (Azure AI Content Safety — Prompt Shields).
    GUARDRAILS_INJECTION_PROVIDER: str = "regex"

    # ── Grounding (Track A — server-side fact verification) ─────────
    # off      — GroundingVerificationMiddleware is skipped entirely.
    # observe  — verify and log counts; nothing surfaced to the caller.
    # annotate — verify and attach a report to response.additional_properties["grounding"].
    # enforce  — annotate, plus strip not-found cards and correct price/total
    #            mismatches in the finalized response text. See
    #            shared/grounding/middleware.py for the streaming caveat: this
    #            corrects the persisted/final response, not chunks already
    #            streamed to the browser.
    GROUNDING_MODE: str = "annotate"

    # ── Cost budget (per-run ceiling) ────────────────────────────────
    # ``shared/cost.py::estimate_cost`` previously had exactly one caller
    # (``evals/evaluator.py``, for after-the-fact eval reporting) — nothing
    # read it at runtime, so an agentic loop that keeps calling tools /
    # re-prompting had no ceiling on what a single run could spend. These two
    # flags back ``shared.guardrails.cost_budget_middleware.CostBudgetMiddleware``,
    # the runtime consumer that closes that gap.
    #
    # off      — CostBudgetMiddleware is skipped entirely (not attached).
    # observe  — accumulate and log the running per-run cost; never blocks,
    #            even past COST_BUDGET_USD_PER_RUN.
    # enforce  — same accumulation, plus refuses to start another LLM turn
    #            once the running total exceeds COST_BUDGET_USD_PER_RUN.
    COST_BUDGET_MODE: str = "observe"

    # USD ceiling for a single run's cumulative estimated cost. None (default)
    # means no ceiling is enforced even in "enforce" mode — additive, opt-in,
    # matching this repo's other guardrail flags' default-off posture.
    COST_BUDGET_USD_PER_RUN: float | None = None

    # ── Model pricing (config/pricing/models.yaml) ───────────────────
    # No price lives in code: shared/cost.py reads the pricing file. Empty means the shipped
    # file. PRICING_OVERRIDES_FILE is merged over it entry by entry (a local model at 0, a
    # negotiated rate). An unlisted model's cost is unknown (null), never a guess.
    PRICING_FILE: str = ""
    PRICING_OVERRIDES_FILE: str = ""

    # ── Run profile (config/profiles/<name>.env) ─────────────────────
    # Set by `dev.sh --profile` / `-Profile` / the eval CLI so a service can say which profile it runs under in its
    # startup line and the doctor. Informational: the profile's values themselves arrive as ordinary variables.
    RUN_PROFILE: str = ""

    # ── Per-run token budget (EV-12) ─────────────────────────────────
    # A ceiling on input + output tokens for one run, beside the dollar budget and under the
    # same COST_BUDGET_MODE: not attached when "off", counted and logged in "observe", and in
    # "enforce" the next LLM turn is refused once the run has reached the ceiling (a turn in
    # flight is never aborted, so a run can overshoot by one turn). Tokens are counted even for
    # an unpriced model, so this is the ceiling that still holds where cost is unknown. In
    # enforce a turn that reports no usage also stops the run (fail closed). 0 or unset = off.
    COST_BUDGET_TOKENS_PER_RUN: int | None = None

    # ── Output moderation (Phase 6.4) ────────────────────────────────
    # shared/guardrails/output_middleware.py's OutputSanitizationMiddleware
    # defangs adversarial *instructions* hiding in untrusted tool output —
    # a different problem from the model's own generated text containing
    # content-policy violations (self-harm, violence, hate/harassment,
    # sexual content), which nothing checked for until this.
    #
    # off      — OutputModerationMiddleware is skipped entirely.
    # observe  — classify the final response and log any flagged
    #            categories; never blocks. Default, because the classifier
    #            (shared/guardrails/moderation.py) is a small set of
    #            high-precision local patterns, not a trained model — a
    #            false positive blocking a legitimate response is a worse
    #            failure mode than one going unflagged in observe mode.
    # enforce  — same classification, plus replaces the response with a
    #            refusal when the non-streaming path flags a category.
    #            Streamed responses can only be flagged, not blocked —
    #            same streaming caveat as GROUNDING_MODE=enforce: chunks
    #            already sent to the browser can't be un-sent.
    OUTPUT_MODERATION_MODE: str = "observe"

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def cors_origins(self) -> list[str]:
        """``CORS_ALLOWED_ORIGINS`` split on commas, blanks dropped, trailing slashes trimmed."""
        return [o.strip().rstrip("/") for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

    @field_validator("LLM_TEMPERATURE", mode="before")
    @classmethod
    def _blank_temperature_is_none(cls, value: object) -> object:
        """``LLM_TEMPERATURE=`` (set, empty) omits the option; compose passes it through as ''."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("COST_BUDGET_TOKENS_PER_RUN", mode="before")
    @classmethod
    def _blank_token_budget_is_off(cls, value: object) -> object:
        """``COST_BUDGET_TOKENS_PER_RUN=`` (set, empty; compose passes it through as '') means no ceiling."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("DB_POOL_MIN_SIZE", "DB_POOL_MAX_SIZE", mode="before")
    @classmethod
    def _blank_pool_size_is_default(cls, value: object, info: ValidationInfo) -> object:
        """A set-but-empty pool size means 'use the default'.

        The .NET compose file passes ``DB_POOL_MIN_SIZE: ${DB_POOL_MIN_SIZE:-}`` (empty means Npgsql's own
        default there), and in the mixed ``net-py`` stack that empty string reaches the Python specialists.
        """
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[str(info.field_name)].default
        return value

    @model_validator(mode="after")
    def _validate_pool_bounds(self) -> "Settings":
        if self.DB_POOL_MIN_SIZE > self.DB_POOL_MAX_SIZE:
            raise ValueError("DB_POOL_MIN_SIZE must not exceed DB_POOL_MAX_SIZE")
        return self

    @field_validator("AZURE_OPENAI_AUTH", mode="before")
    @classmethod
    def _normalise_azure_auth(cls, value: object) -> object:
        """Trimmed and lower-cased; blank is the default, ``key``."""
        if isinstance(value, str):
            return value.strip().lower() or "key"
        return value

    @field_validator("LLM_PROVIDER")
    @classmethod
    def _normalise_llm_provider(cls, value: str) -> str:
        """Trimmed and lower-cased, as .NET reads it (loader, ``ChatClientFactory``): ``" Replay "`` is ``replay``."""
        return value.strip().lower()

    @model_validator(mode="after")
    def _validate_secrets(self) -> "Settings":
        """Fail fast on weak / default secrets in production; warn in dev.

        HS256 demands at least a 256-bit key; shorter secrets are stretched
        via SHA-256 on the .NET side but nothing stops PyJWT from signing
        with a 20-byte placeholder. Matching both sides means rejecting the
        placeholders we ship in ``.env.example`` whenever we're not in
        development — and logging a loud warning even then.
        """
        is_prod = self.ENVIRONMENT.lower() not in {"development", "dev", "test"}

        def _check(name: str, value: str) -> None:
            stripped = value.strip()
            too_short = len(stripped.encode("utf-8")) < _MIN_SECRET_BYTES
            is_default = stripped in _UNSAFE_SECRET_DEFAULTS
            if too_short or is_default:
                msg = (
                    f"{name} is unsafe ("
                    + ("placeholder default" if is_default else f"{len(stripped)} chars < {_MIN_SECRET_BYTES}")
                    + "). Generate a fresh random 256-bit value."
                )
                if is_prod:
                    raise ValueError(msg)
                # Log a fixed label, not ``msg``: ``msg`` carries the secret's length, and
                # nothing derived from a secret's value belongs in a log line.
                reason = "placeholder default" if is_default else "too short"
                logger.warning("settings.secret_unsafe var=%s reason=%s", name, reason)

        _check("JWT_SECRET", self.JWT_SECRET)
        _check("AGENT_SHARED_SECRET", self.AGENT_SHARED_SECRET)
        if self.A2A_IDENTITY_KEY.strip():
            _check("A2A_IDENTITY_KEY", self.A2A_IDENTITY_KEY)

        # `token` mode only protects against a caller holding just X-Agent-Secret when the signing key is a different
        # secret; with no key (it falls back to the shared secret) or the same value, holding the secret is holding the
        # key. Name the variables, never a value.
        identity_key = self.A2A_IDENTITY_KEY.strip()
        shared_secret = self.AGENT_SHARED_SECRET.strip()
        if self.A2A_IDENTITY == "token" and (not identity_key or identity_key == shared_secret):
            logger.warning(
                "settings.a2a_identity_key_not_distinct mode=token reason=%s: a caller holding only the shared secret "
                "can still mint user-context tokens; set A2A_IDENTITY_KEY to a different secret",
                "key_empty" if not identity_key else "key_equals_shared_secret",
            )

        if self.AUTH_MODE == "oauth":
            _check("OAUTH_SEED_KEY", self.OAUTH_SEED_KEY)
            if is_prod and not self.AUTH_SIGNING_KEY_ENCRYPTION_KEY.strip():
                msg = (
                    "AUTH_SIGNING_KEY_ENCRYPTION_KEY is required when AUTH_MODE=oauth "
                    "outside development — without it the auth-server stores its RSA "
                    "private key unencrypted."
                )
                raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _validate_hitl_timeouts(self) -> "Settings":
        if not 0 < self.HITL_RESUME_TIMEOUT_SECONDS < self.HITL_CLAIM_TTL_SECONDS:
            raise ValueError(
                "HITL_RESUME_TIMEOUT_SECONDS must be positive and shorter than HITL_CLAIM_TTL_SECONDS "
                f"(got {self.HITL_RESUME_TIMEOUT_SECONDS} and {self.HITL_CLAIM_TTL_SECONDS})."
            )
        return self

    @model_validator(mode="after")
    def _validate_injection_provider(self) -> "Settings":
        """Reject injection-detection providers that aren't implemented.

        ``GUARDRAILS_INJECTION_PROVIDER`` historically accepted
        "azure_content_safety" as a value with no code behind it — selecting
        it silently ran the default regex provider instead. Fail fast instead
        so misconfiguration is caught at startup, not discovered in
        production traffic.
        """
        provider = self.GUARDRAILS_INJECTION_PROVIDER
        if provider in _NOT_YET_IMPLEMENTED_INJECTION_PROVIDERS:
            raise ValueError(
                f"GUARDRAILS_INJECTION_PROVIDER={provider!r} is not implemented "
                f"(shared/guardrails/azure_shield.py does not exist yet). "
                f"Supported values: {sorted(_SUPPORTED_INJECTION_PROVIDERS)!r}."
            )
        if provider not in _SUPPORTED_INJECTION_PROVIDERS:
            raise ValueError(
                f"GUARDRAILS_INJECTION_PROVIDER={provider!r} is not a recognized value. "
                f"Supported values: {sorted(_SUPPORTED_INJECTION_PROVIDERS)!r}."
            )
        return self

    @model_validator(mode="after")
    def _validate_grounding_mode(self) -> "Settings":
        if self.GROUNDING_MODE not in _SUPPORTED_GROUNDING_MODES:
            raise ValueError(
                f"GROUNDING_MODE={self.GROUNDING_MODE!r} is not a recognized value. "
                f"Supported values: {sorted(_SUPPORTED_GROUNDING_MODES)!r}."
            )
        return self

    @model_validator(mode="after")
    def _validate_cost_budget_mode(self) -> "Settings":
        if self.COST_BUDGET_MODE not in _SUPPORTED_COST_BUDGET_MODES:
            raise ValueError(
                f"COST_BUDGET_MODE={self.COST_BUDGET_MODE!r} is not a recognized value. "
                f"Supported values: {sorted(_SUPPORTED_COST_BUDGET_MODES)!r}."
            )
        return self

    @model_validator(mode="after")
    def _validate_output_moderation_mode(self) -> "Settings":
        if self.OUTPUT_MODERATION_MODE not in _SUPPORTED_OUTPUT_MODERATION_MODES:
            raise ValueError(
                f"OUTPUT_MODERATION_MODE={self.OUTPUT_MODERATION_MODE!r} is not a recognized value. "
                f"Supported values: {sorted(_SUPPORTED_OUTPUT_MODERATION_MODES)!r}."
            )
        return self


settings = Settings()
