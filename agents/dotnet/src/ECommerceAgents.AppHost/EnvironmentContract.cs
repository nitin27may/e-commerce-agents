namespace ECommerceAgents.AppHost;

/// <summary>One pass-through variable: read from the AppHost's own environment, else the compose default.</summary>
public sealed record EnvVar(string Name, string Default, bool Secret = false);

/// <summary>
/// The variable names and defaults from the <c>x-agent-env</c> anchor in <c>docker-compose.yml</c> and
/// <c>docker-compose.dotnet.yml</c>, copied rather than invented. When a compose default changes, change it
/// here: <c>AppHostModelTests</c> pins the names, not the values.
/// </summary>
public static class EnvironmentContract
{
    public const string AuthServerPort = "8090";

    /// <summary>The URL every host-side process uses for the auth-server (compose says <c>http://auth-server:8090</c>).</summary>
    public static readonly string AuthServerUrl = $"http://localhost:{AuthServerPort}";

    /// <summary>Read by both stacks.</summary>
    public static readonly IReadOnlyList<EnvVar> Common =
    [
        new("LLM_PROVIDER", "openai"),
        new("OPENAI_API_KEY", "", Secret: true),
        new("LLM_BASE_URL", ""),
        new("LLM_MODEL", "gpt-4.1"),
        new("ANTHROPIC_API_KEY", "", Secret: true),
        new("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        new("COST_BUDGET_MODE", "observe"),
        new("COST_BUDGET_USD_PER_RUN", "0.50"),
        new("EMBEDDING_MODEL", "text-embedding-3-small"),
        new("EMBEDDING_PROVIDER", ""),
        new("EMBEDDING_BASE_URL", ""),
        new("EMBEDDING_API_KEY", "", Secret: true),
        new("EMBEDDING_DIM", "1536"),
        new("AZURE_OPENAI_ENDPOINT", ""),
        new("AZURE_OPENAI_KEY", "", Secret: true),
        new("AZURE_OPENAI_DEPLOYMENT", ""),
        new("AZURE_OPENAI_API_VERSION", "2025-03-01-preview"),
        new("AZURE_EMBEDDING_DEPLOYMENT", ""),
        new("JWT_SECRET", "change-me-in-production-use-random-256-bit-key", Secret: true),
        new("AGENT_SHARED_SECRET", "agent-internal-shared-secret", Secret: true),
        new("AUTH_MODE", "local"),
        new("AUTH_JWKS_CACHE_TTL", "900"),
        new("AUTH_ORCH_AUDIENCE", "ecommerce-orchestrator"),
        new("AUTH_AGENT_AUDIENCE", "ecommerce-agents"),
        new("OAUTH_CLIENT_SECRET", "", Secret: true),
        new("OAUTH_SEED_KEY", "dev-oauth-seed-change-me", Secret: true),
        new("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false"),
        new("CORS_ALLOWED_ORIGINS", "http://localhost:3000"),
        new("RATE_LIMIT_AUTH_MAX_REQUESTS", "10"),
        new("RATE_LIMIT_AUTH_IP_MAX_REQUESTS", "300"),
        new("RATE_LIMIT_TRUSTED_PROXY_HOPS", "1"),
        new("MAF_SESSION_BACKEND", "postgres"),
        new("HISTORY_COMPACTION_KEEP_LAST_GROUPS", "0"),
        new("MAF_CHECKPOINT_BACKEND", "postgres"),
        new("RETURN_HITL_THRESHOLD", "500"),
        new("HANDOFF_AUTONOMOUS_MODE", "true"),
        new("A2A_TRANSPORT", "legacy"),
        new("A2A_IDENTITY", "both"),
        new("A2A_IDENTITY_KEY", "", Secret: true),
        new("A2A_IDENTITY_TTL_SECONDS", "300"),
    ];

    /// <summary>Only the Python compose file passes these.</summary>
    public static readonly IReadOnlyList<EnvVar> PythonOnly =
    [
        new("LLM_API", "responses"),
        new("ORCHESTRATION_MODE", "tool"),
        new("AUTONOMY_ENABLED", "false"),
        new("AUTONOMY_SIMULATOR_ENABLED", "false"),
    ];

    /// <summary>The outbox worker's own set (<c>outbox-worker</c> in both compose files).</summary>
    public static readonly IReadOnlyList<EnvVar> OutboxWorker =
    [
        new("ENVIRONMENT", "development"),
        new("OUTBOX_WORKER_ENABLED", "true"),
        new("OUTBOX_POLL_INTERVAL_SECONDS", "2"),
        new("OUTBOX_BATCH_SIZE", "10"),
        new("OUTBOX_MAX_ATTEMPTS", "8"),
        new("OUTBOX_LEASE_SECONDS", "120"),
    ];

    /// <summary>The auth-server's own set (<c>auth-server</c> in the Python compose file).</summary>
    public static readonly IReadOnlyList<EnvVar> AuthServer =
    [
        new("AUTH_RSA_KEY_SIZE", "2048"),
        new("AUTH_SIGNING_KEY_ENCRYPTION_KEY", "", Secret: true),
        new("AUTH_ACCESS_TOKEN_TTL", "3600"),
        new("AUTH_KEY_RETIRE_AFTER_SECONDS", "0"),
        new("AUTH_REFRESH_TOKEN_TTL", "604800"),
        new("OAUTH_SEED_KEY", "dev-oauth-seed-change-me", Secret: true),
        new("AUTH_SERVER_AUDIENCE", "ecommerce-auth-server"),
        new("AUTH_ALLOW_DYNAMIC_REGISTRATION", "false"),
        new("ENVIRONMENT", "development"),
    ];

    /// <summary>What the seeder container reads.</summary>
    public static readonly IReadOnlyList<EnvVar> Seeder =
    [
        new("OAUTH_SEED_KEY", "dev-oauth-seed-change-me", Secret: true),
        new("LLM_PROVIDER", "openai"),
        new("EMBEDDING_MODEL", "text-embedding-3-small"),
        new("EMBEDDING_PROVIDER", ""),
        new("EMBEDDING_BASE_URL", ""),
        new("EMBEDDING_API_KEY", "", Secret: true),
        new("EMBEDDING_DIM", "1536"),
    ];

    /// <summary>The MCP servers' own set.</summary>
    public static readonly IReadOnlyList<EnvVar> Mcp =
    [
        new("MCP_AUTH_ENABLED", "false"),
        new("MCP_PRODUCT_AUDIENCE", "mcp-product"),
        new("MCP_PRODUCT_REQUIRED_SCOPE", "mcp:product"),
        new("MCP_INVENTORY_AUDIENCE", "mcp-inventory"),
        new("MCP_INVENTORY_REQUIRED_SCOPE", "mcp:inventory"),
    ];
}
