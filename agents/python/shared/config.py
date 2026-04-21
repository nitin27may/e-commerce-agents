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

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env once, relative to the repo root, so the eval/seed scripts pick
# it up regardless of the cwd they're launched from. Inside the Docker image
# there is no .env at the repo root — containers get their config from the
# compose `environment:` block — so a missing file is fine.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    # ── Database ────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents"

    # ── Redis ───────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"

    # ── LLM ─────────────────────────────────────────────────────────
    LLM_PROVIDER: str = "openai"  # openai | azure
    LLM_MODEL: str = "gpt-4.1"
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

    # ── Agent Registry (A2A endpoint map) ───────────────────────────
    AGENT_REGISTRY: str = "{}"

    # ── Telemetry ───────────────────────────────────────────────────
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:18889"
    OTEL_SERVICE_NAME: str = "ecommerce"
    GENAI_CAPTURE_CONTENT: bool = False

    # ── General ─────────────────────────────────────────────────────
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── MAF v1 feature flags (all optional, safe defaults) ──────────
    # These switches back the Phase 7 refactor. Every flag defaults to the
    # behavior the app had before the refactor, so turning them on is opt-in.

    # When true, specialist agents use MAF's native execution path
    # (agent.run) instead of the custom OpenAI tool loop in shared/agent_host.
    MAF_NATIVE_EXECUTION: bool = True

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

    # Orchestrator routing mode:
    #   tool    — LLM calls call_specialist_agent tool (today's behavior)
    #   handoff — MAF HandoffBuilder workflow with remote A2A proxies
    # Default stays "tool" so rollouts are opt-in.
    MAF_HANDOFF_MODE: str = "tool"

    # When true, CI regenerates docs/workflows/*.mmd and fails on drift.
    WORKFLOW_VISUALIZATION_ON_BUILD: bool = False

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )


settings = Settings()
