namespace ECommerceAgents.Shared.Configuration;

/// <summary>
/// Strongly-typed settings mirroring the Python <c>shared/config.py</c>.
/// Every environment variable consumed by the .NET backend is declared here
/// so callers can inject <see cref="AgentSettings"/> instead of reading
/// <c>Environment.GetEnvironmentVariable</c> directly.
/// </summary>
/// <remarks>
/// Parity with Python is the whole point of this type. When you add a new
/// MAF feature flag to Python's Settings, add its twin here and extend
/// <see cref="AgentSettingsLoader.Load"/>.
/// </remarks>
public sealed record AgentSettings
{
    // ── Database ────────────────────────────────────────────────
    public string DatabaseUrl { get; init; } =
        "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents";

    // ── Redis ───────────────────────────────────────────────────
    public string RedisUrl { get; init; } = "redis://localhost:6379";

    // ── LLM ─────────────────────────────────────────────────────
    public string LlmProvider { get; init; } = "openai"; // "openai" | "azure"
    public string LlmModel { get; init; } = "gpt-4.1";
    public string EmbeddingModel { get; init; } = "text-embedding-3-small";
    public string OpenAiApiKey { get; init; } = "";

    public string AzureOpenAiEndpoint { get; init; } = "";

    /// <summary>Accepts either <c>AZURE_OPENAI_KEY</c> or the MAF-doc alias <c>AZURE_OPENAI_API_KEY</c>.</summary>
    public string AzureOpenAiKey { get; init; } = "";

    /// <summary>Accepts either <c>AZURE_OPENAI_DEPLOYMENT</c> or the alias <c>AZURE_OPENAI_DEPLOYMENT_NAME</c>.</summary>
    public string AzureOpenAiDeployment { get; init; } = "";

    public string AzureOpenAiApiVersion { get; init; } = "2025-03-01-preview";
    public string AzureEmbeddingDeployment { get; init; } = "";

    // ── Auth ────────────────────────────────────────────────────
    public string JwtSecret { get; init; } = "change-me-in-production";
    public string AgentSharedSecret { get; init; } = "agent-internal-secret";

    // ── Agent Registry (A2A endpoint map) ───────────────────────
    public string AgentRegistry { get; init; } = "{}";

    // ── Telemetry ───────────────────────────────────────────────
    public bool OtelEnabled { get; init; }
    public string OtelExporterOtlpEndpoint { get; init; } = "http://localhost:18889";
    public string OtelServiceName { get; init; } = "ecommerce";
    public bool GenAiCaptureContent { get; init; }

    // ── General ─────────────────────────────────────────────────
    public string Environment { get; init; } = "development";
    public string LogLevel { get; init; } = "INFO";

    // ── MAF v1 feature flags (all optional, safe defaults) ──────
    public string MafSessionBackend { get; init; } = "postgres";
    public string MafSessionDir { get; init; } = "./.sessions";
    public string MafCheckpointBackend { get; init; } = "postgres";
    public string MafCheckpointDir { get; init; } = "./.checkpoints";
    public double ReturnHitlThreshold { get; init; } = 500.0;
    public bool HandoffAutonomousMode { get; init; } = true;
    public bool WorkflowVisualizationOnBuild { get; init; }
    public string MafHandoffMode { get; init; } = "tool"; // "tool" | "handoff"
}
