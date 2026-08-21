using Microsoft.Extensions.Configuration;

namespace ECommerceAgents.Shared.Configuration;

/// <summary>
/// Binds environment variables + <c>ConnectionStrings</c> into an
/// <see cref="AgentSettings"/>. Handles the same alias rules as the Python
/// Pydantic settings — <c>AZURE_OPENAI_KEY</c> wins over
/// <c>AZURE_OPENAI_API_KEY</c> when both are set.
/// </summary>
public static class AgentSettingsLoader
{
    /// <summary>
    /// Accepts only the grounding modes .NET actually implements.
    /// </summary>
    /// <remarks>
    /// Python's "enforce" is deliberately refused rather than quietly treated
    /// as "annotate". Operators set that value expecting unverified cards to
    /// be stripped; accepting it would mean the strongest setting is the one
    /// that lies. Same posture as the config rejection for the unimplemented
    /// azure_content_safety guardrail provider on the Python side.
    /// </remarks>
    private static string ValidatedGroundingMode(string raw)
    {
        var mode = raw.Trim().ToLowerInvariant();
        if (mode is "off" or "observe" or "annotate")
        {
            return mode;
        }

        throw new InvalidOperationException(
            $"GROUNDING_MODE='{raw}' is not supported. Use off, observe or annotate. "
                + (mode == "enforce"
                    ? "enforce (strip unverified cards, correct prices) exists in the Python "
                        + "stack only — see docs/parity-matrix.md."
                    : "")
        );
    }

    public static AgentSettings Load(IConfiguration config)
    {
        string Get(string key, string fallback = "") =>
            config[key] ?? Environment.GetEnvironmentVariable(key) ?? fallback;

        string GetWithAlias(string primary, string alias, string fallback = "")
        {
            var p = Get(primary);
            if (!string.IsNullOrEmpty(p))
            {
                return p;
            }

            var a = Get(alias);
            return !string.IsNullOrEmpty(a) ? a : fallback;
        }

        bool GetBool(string key, bool fallback)
        {
            var raw = Get(key);
            return bool.TryParse(raw, out var parsed) ? parsed : fallback;
        }

        double GetDouble(string key, double fallback)
        {
            var raw = Get(key);
            return double.TryParse(raw, out var parsed) ? parsed : fallback;
        }

        double? GetNullableDouble(string key)
        {
            var raw = Get(key);
            return string.IsNullOrWhiteSpace(raw) ? null
                : double.TryParse(raw, out var parsed) ? parsed
                : null;
        }

        int GetInt(string key, int fallback)
        {
            var raw = Get(key);
            return int.TryParse(raw, out var parsed) ? parsed : fallback;
        }

        var databaseUrl =
            config.GetConnectionString("Postgres")
            ?? Get("DATABASE_URL", "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents");
        var redisUrl =
            config.GetConnectionString("Redis")
            ?? Get("REDIS_URL", "redis://localhost:6379");

        return new AgentSettings
        {
            DatabaseUrl = databaseUrl,
            RedisUrl = redisUrl,

            LlmProvider = Get("LLM_PROVIDER", "openai").ToLowerInvariant(),
            ReplayFixturesDir = Get("REPLAY_FIXTURES_DIR", "evals/fixtures/replay"),
            Record = Get("RECORD", "false").Equals("true", StringComparison.OrdinalIgnoreCase),
            ReplayRecordProvider = Get("REPLAY_RECORD_PROVIDER", "azure").ToLowerInvariant(),
            LlmModel = Get("LLM_MODEL", "gpt-4.1"),
            EmbeddingModel = Get("EMBEDDING_MODEL", "text-embedding-3-small"),
            OpenAiApiKey = Get("OPENAI_API_KEY"),
            LlmBaseUrl = Get("LLM_BASE_URL"),
            RateLimitEnabled = GetBool("RATE_LIMIT_ENABLED", true),
            RateLimitMaxRequests = GetInt("RATE_LIMIT_MAX_REQUESTS", 30),
            RateLimitWindowSeconds = GetDouble("RATE_LIMIT_WINDOW_SECONDS", 60.0),
            GroundingMode = ValidatedGroundingMode(Get("GROUNDING_MODE", "annotate")),
            CostBudgetMode = Get("COST_BUDGET_MODE", "observe"),
            CostBudgetUsdPerRun = GetNullableDouble("COST_BUDGET_USD_PER_RUN"),
            Temperature = GetDouble("LLM_TEMPERATURE", 0.2),

            AzureOpenAiEndpoint = Get("AZURE_OPENAI_ENDPOINT"),
            AzureOpenAiKey = GetWithAlias("AZURE_OPENAI_KEY", "AZURE_OPENAI_API_KEY"),
            AzureOpenAiDeployment = GetWithAlias("AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME"),
            AzureOpenAiApiVersion = Get("AZURE_OPENAI_API_VERSION", "2025-03-01-preview"),
            AzureEmbeddingDeployment = Get("AZURE_EMBEDDING_DEPLOYMENT"),

            JwtSecret = Get("JWT_SECRET", "change-me-in-production"),
            AgentSharedSecret = Get("AGENT_SHARED_SECRET", "agent-internal-secret"),

            AuthMode = Get("AUTH_MODE", "local").ToLowerInvariant(),
            AuthServerIssuer = Get("AUTH_SERVER_ISSUER", "http://localhost:8090"),
            AuthServerJwksUrl = Get("AUTH_SERVER_JWKS_URL", "http://localhost:8090/.well-known/jwks.json"),
            AuthServerTokenUrl = Get("AUTH_SERVER_TOKEN_URL", "http://localhost:8090/oauth/token"),
            AuthJwksCacheTtl = GetInt("AUTH_JWKS_CACHE_TTL", 900),
            OAuthClientId = Get("OAUTH_CLIENT_ID"),
            OAuthClientSecret = Get("OAUTH_CLIENT_SECRET"),
            OAuthSeedKey = Get("OAUTH_SEED_KEY", "dev-oauth-seed-change-me"),
            AuthOrchAudience = Get("AUTH_ORCH_AUDIENCE", "ecommerce-orchestrator"),
            AuthAgentAudience = Get("AUTH_AGENT_AUDIENCE", "ecommerce-agents"),
            GuardrailsEnabled = GetBool("GUARDRAILS_ENABLED", true),
            GuardrailsStrictIdentity = GetBool("GUARDRAILS_STRICT_IDENTITY", false),
            GuardrailsBlockOnInjection = GetBool("GUARDRAILS_BLOCK_ON_INJECTION", false),
            GuardrailsOutputSanitization = GetBool("GUARDRAILS_OUTPUT_SANITIZATION", true),
            OutputModerationMode = Get("OUTPUT_MODERATION_MODE", "observe").ToLowerInvariant(),

            McpAuthEnabled = GetBool("MCP_AUTH_ENABLED", false),
            McpAudience = Get("MCP_INVENTORY_AUDIENCE", "mcp-inventory"),
            McpRequiredScope = Get("MCP_INVENTORY_REQUIRED_SCOPE", "mcp:inventory"),
            McpResourceUrl = Get("MCP_INVENTORY_RESOURCE_URL", "http://localhost:9001/mcp"),

            AgentRegistry = Get("AGENT_REGISTRY", "{}"),

            OtelEnabled = GetBool("OTEL_ENABLED", false),
            OtelExporterOtlpEndpoint = Get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:18889"),
            OtelServiceName = Get("OTEL_SERVICE_NAME", "ecommerce"),
            GenAiCaptureContent = GetBool("GENAI_CAPTURE_CONTENT", false),

            Environment = Get("ENVIRONMENT", "development"),
            LogLevel = Get("LOG_LEVEL", "INFO"),

            MafSessionBackend = Get("MAF_SESSION_BACKEND", "postgres").ToLowerInvariant(),
            MafSessionDir = Get("MAF_SESSION_DIR", "./.sessions"),
            MafCheckpointBackend = Get("MAF_CHECKPOINT_BACKEND", "postgres").ToLowerInvariant(),
            MafCheckpointDir = Get("MAF_CHECKPOINT_DIR", "./.checkpoints"),
            ReturnHitlThreshold = GetDouble("RETURN_HITL_THRESHOLD", 500.0),
            HandoffAutonomousMode = GetBool("HANDOFF_AUTONOMOUS_MODE", true),
            WorkflowVisualizationOnBuild = GetBool("WORKFLOW_VISUALIZATION_ON_BUILD", false),
            MafHandoffMode = Get("MAF_HANDOFF_MODE", "tool").ToLowerInvariant(),
            HitlEnabled = GetBool("HITL_ENABLED", true),
        };
    }

    /// <summary>
    /// Parses <see cref="AgentSettings.AgentRegistry"/> into a
    /// <c>name → A2A base URL</c> map. Returns an empty dictionary on
    /// malformed JSON so callers don't have to try/catch themselves.
    /// </summary>
    public static IReadOnlyDictionary<string, string> ParseAgentRegistry(AgentSettings settings)
    {
        if (string.IsNullOrWhiteSpace(settings.AgentRegistry))
        {
            return new Dictionary<string, string>();
        }

        try
        {
            var map = System.Text.Json.JsonSerializer.Deserialize<Dictionary<string, string>>(
                settings.AgentRegistry
            );
            return map ?? new Dictionary<string, string>();
        }
        catch (System.Text.Json.JsonException)
        {
            return new Dictionary<string, string>();
        }
    }
}
