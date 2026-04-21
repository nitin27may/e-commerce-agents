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

        var databaseUrl =
            config.GetConnectionString("Postgres")
            ?? Get("DATABASE_URL", "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents");

        return new AgentSettings
        {
            DatabaseUrl = databaseUrl,
            RedisUrl = Get("REDIS_URL", "redis://localhost:6379"),

            LlmProvider = Get("LLM_PROVIDER", "openai").ToLowerInvariant(),
            LlmModel = Get("LLM_MODEL", "gpt-4.1"),
            EmbeddingModel = Get("EMBEDDING_MODEL", "text-embedding-3-small"),
            OpenAiApiKey = Get("OPENAI_API_KEY"),

            AzureOpenAiEndpoint = Get("AZURE_OPENAI_ENDPOINT"),
            AzureOpenAiKey = GetWithAlias("AZURE_OPENAI_KEY", "AZURE_OPENAI_API_KEY"),
            AzureOpenAiDeployment = GetWithAlias("AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME"),
            AzureOpenAiApiVersion = Get("AZURE_OPENAI_API_VERSION", "2025-03-01-preview"),
            AzureEmbeddingDeployment = Get("AZURE_EMBEDDING_DEPLOYMENT"),

            JwtSecret = Get("JWT_SECRET", "change-me-in-production"),
            AgentSharedSecret = Get("AGENT_SHARED_SECRET", "agent-internal-secret"),

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
