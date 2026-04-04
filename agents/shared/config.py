"""Pydantic Settings for AgentBazaar agents."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql://agentbazaar:agentbazaar@localhost:5432/agentbazaar"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # LLM
    LLM_PROVIDER: str = "openai"                  # openai | azure
    LLM_MODEL: str = "gpt-4.1"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_API_KEY: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_KEY: str = ""
    AZURE_OPENAI_DEPLOYMENT: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-12-01-preview"
    AZURE_EMBEDDING_DEPLOYMENT: str = ""

    # Auth
    JWT_SECRET: str = "change-me-in-production"
    AGENT_SHARED_SECRET: str = "agent-internal-secret"

    # Agent Registry
    AGENT_REGISTRY: str = "{}"

    # Telemetry
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:18889"
    OTEL_SERVICE_NAME: str = "agentbazaar"

    # General
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
