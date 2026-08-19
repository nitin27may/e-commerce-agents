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

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Secrets below this length are considered unsafe for HS256 and are also
# the shape of the shipped `.env.example` placeholders. 32 bytes (256 bits)
# matches both HS256's minimum key size and MAF's .NET validator.
_MIN_SECRET_BYTES = 32
_UNSAFE_SECRET_DEFAULTS = {
    "change-me-in-production",
    "change-me-generate-a-random-256-bit-key",
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

# Resolve .env once, relative to the repo root, so the eval/seed scripts pick
# it up regardless of the cwd they're launched from. Inside the Docker image
# there is no .env at the repo root — containers get their config from the
# compose `environment:` block — so a missing file is fine.
# config.py lives at <repo>/agents/python/shared/, so the repo root is 3 levels
# up — parents[2] stops at <repo>/agents and silently resolved to a path with no
# .env, which meant pydantic's env_file loading never fired and Settings fell back
# to defaults even when a real .env was present.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    # ── Database ────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents"

    # ── Redis ───────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"

    # ── LLM ─────────────────────────────────────────────────────────
    LLM_PROVIDER: str = "openai"  # openai | azure | replay
    LLM_MODEL: str = "gpt-4.1"
    # Optional base_url override for the OpenAI-compatible `openai` provider —
    # points OpenAIChatClient at any OpenAI-compatible endpoint (GitHub Models,
    # OpenRouter, vLLM, LM Studio, Azure AI Foundry's OpenAI-compatible route)
    # instead of api.openai.com. Unset by default; only takes effect when
    # LLM_PROVIDER=openai. See tutorials/00-setup/README.md for the GitHub
    # Models path, which is free with a GitHub PAT.
    LLM_BASE_URL: str = ""
    # replay-provider settings — see shared/replay_client.py. RECORD=true makes
    # a missing fixture fall through to a real call (via REPLAY_RECORD_PROVIDER)
    # and persist the result instead of raising.
    REPLAY_RECORD_PROVIDER: str = "openai"  # openai | azure — used only when RECORD=true
    REPLAY_FIXTURES_DIR: str = "tests/fixtures/replay"
    RECORD: bool = False
    # Sampling temperature for every agent run. Defaults LOW for consistent,
    # reproducible answers — at the provider default (~1.0) the same query can
    # yield categorically different results (e.g. finding vs. not finding a
    # borderline-priced product). Raise toward 1.0 for more varied phrasing.
    LLM_TEMPERATURE: float = 0.2
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_API_KEY: str = ""

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
    AUTH_JWKS_CACHE_TTL: int = 900  # resource-server JWKS cache
    AUTH_RSA_KEY_SIZE: int = 2048  # auth-server only
    AUTH_SIGNING_KEY_ENCRYPTION_KEY: str = ""  # optional KEK for the private key at rest

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

    # ── MCP integration (optional, flag-gated) ──────────────────────
    # When enabled, specialist agents connect to MCP servers for their data
    # layer instead of calling asyncpg directly. MCP servers must be running
    # at the configured URLs. Both modes are semantically equivalent.
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

    # When true, handoff between specialists happens without intermediate
    # user events (mirrors today's behavior). false → emit a handoff event
    # for UI breadcrumbs / observability.
    HANDOFF_AUTONOMOUS_MODE: bool = True

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

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

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
                logger.warning("settings.secret_unsafe var=%s reason=%s", name, msg)

        _check("JWT_SECRET", self.JWT_SECRET)
        _check("AGENT_SHARED_SECRET", self.AGENT_SHARED_SECRET)

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


settings = Settings()
