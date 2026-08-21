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
    /// <summary>"openai" | "azure" | "replay".</summary>
    /// <remarks>
    /// <c>replay</c> serves recorded fixtures instead of calling a model, so an eval run
    /// is deterministic and costs nothing. Unknown values are rejected by
    /// <c>ChatClientFactory</c> rather than falling through to OpenAI, which used to turn
    /// a typo into a confusing "OPENAI_API_KEY is required".
    /// </remarks>
    public string LlmProvider { get; init; } = "openai";

    /// <summary>Where <c>LLM_PROVIDER=replay</c> reads and writes fixtures.</summary>
    public string ReplayFixturesDir { get; init; } = "evals/fixtures/replay";

    /// <summary>
    /// When true, a replay fixture miss calls a real provider and records the
    /// answer instead of failing. Mirrors Python's <c>RECORD=true</c>.
    /// </summary>
    /// <remarks>
    /// Deliberately opt-in and separate from <see cref="LlmProvider"/>: the
    /// whole value of replay mode is that a normal run cannot reach the
    /// network, so recording has to be something you ask for explicitly rather
    /// than a fallback that quietly starts spending money when a fixture is
    /// missing.
    /// </remarks>
    public bool Record { get; init; }

    /// <summary>Provider used for recording — <c>azure</c> or <c>openai</c>.</summary>
    public string ReplayRecordProvider { get; init; } = "azure";
    public string LlmModel { get; init; } = "gpt-4.1";

    /// <summary>
    /// Optional base-URL override for the OpenAI-compatible <c>openai</c>
    /// provider — the .NET twin of Python's <c>LLM_BASE_URL</c>
    /// (<c>shared/config.py</c>). Points the client at any OpenAI-compatible
    /// endpoint instead of api.openai.com: GitHub Models, OpenRouter, Ollama,
    /// LM Studio, llama.cpp's server, vLLM, Azure AI Foundry's OpenAI-compatible
    /// route. Unset by default; only takes effect when <c>LLM_PROVIDER=openai</c>
    /// (<c>azure</c> keeps its own endpoint setting).
    /// </summary>
    public string LlmBaseUrl { get; init; } = "";

    // ── Rate limiting (issue #30) — .NET twin of Python's RATE_LIMIT_* ──
    public bool RateLimitEnabled { get; init; } = true;
    public int RateLimitMaxRequests { get; init; } = 30;
    public double RateLimitWindowSeconds { get; init; } = 60.0;

    // ── Grounding (issue #33 PR 7) — .NET twin of Python's GROUNDING_MODE ──
    /// <summary>
    /// "off" | "observe" | "annotate". Defaults to annotate, matching Python.
    /// </summary>
    /// <remarks>
    /// Python also has "enforce", which strips unverified card blocks and
    /// corrects prices from the database row before the response leaves. Not
    /// implemented here yet, and <see cref="AgentSettingsLoader"/> rejects it
    /// at startup rather than accepting it: a mode that is advertised but
    /// silently behaves like "annotate" is the exact failure this repo already
    /// fixed once, when GUARDRAILS_BLOCK_ON_INJECTION did not block.
    /// </remarks>
    public string GroundingMode { get; init; } = "annotate";

    // ── Cost budget (issue #30) — .NET twin of Python's COST_BUDGET_* ──
    /// <summary>"off" | "observe" | "enforce". Defaults to observe, matching Python.</summary>
    public string CostBudgetMode { get; init; } = "observe";

    /// <summary>USD ceiling for a single agent run; null disables the ceiling.</summary>
    public double? CostBudgetUsdPerRun { get; init; }
    public string EmbeddingModel { get; init; } = "text-embedding-3-small";
    public string OpenAiApiKey { get; init; } = "";

    /// <summary>
    /// Sampling temperature pinned on every agent run so identical prompts
    /// yield consistent answers (the provider default of ~1.0 makes them
    /// diverge). Mirrors Python's <c>LLM_TEMPERATURE</c>
    /// (<c>shared/config.py</c>), same env var name and default.
    /// </summary>
    public double Temperature { get; init; } = 0.2;

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

    // ── OAuth2 (self-hosted Authorization Server, optional) ──────
    // AuthMode "local" (default) keeps the HS256 JWT + shared-secret
    // behavior above unchanged. "oauth" routes user login and A2A/MCP auth
    // through the self-hosted auth-server (agents/python/auth_server/,
    // shared by both stacks), which issues RS256 tokens validated via its
    // JWKS. No external IdP.
    public string AuthMode { get; init; } = "local"; // "local" | "oauth"

    public string AuthServerIssuer { get; init; } = "http://localhost:8090";
    public string AuthServerJwksUrl { get; init; } = "http://localhost:8090/.well-known/jwks.json";
    public string AuthServerTokenUrl { get; init; } = "http://localhost:8090/oauth/token";
    public int AuthJwksCacheTtl { get; init; } = 900; // seconds

    /// <summary>Defaults to the service name (see AgentAuthMiddleware) when empty.</summary>
    public string OAuthClientId { get; init; } = "";

    /// <summary>Prod override; dev derives from <see cref="OAuthSeedKey"/>.</summary>
    public string OAuthClientSecret { get; init; } = "";

    public string OAuthSeedKey { get; init; } = "dev-oauth-seed-change-me";

    public string AuthOrchAudience { get; init; } = "ecommerce-orchestrator";
    public string AuthAgentAudience { get; init; } = "ecommerce-agents";

    // ── MCP resource-server auth (independent of MCP_ENABLED; consumed by
    // ECommerceAgents.Mcp only — its actual tool surface is inventory data,
    // matching Python's mcp-inventory server) ────────────────────────────
    public bool McpAuthEnabled { get; init; }
    public string McpAudience { get; init; } = "mcp-inventory";
    public string McpRequiredScope { get; init; } = "mcp:inventory";
    public string McpResourceUrl { get; init; } = "http://localhost:9001/mcp";

    // ── Guardrails ────────────────────────────────────────────────
    /// <summary>
    /// Master switch for tool-level role enforcement (see
    /// <c>ECommerceAgents.Shared.Guardrails.RoleGuard</c>). Mirrors Python's
    /// <c>GUARDRAILS_ENABLED</c> — on by default.
    /// </summary>
    public bool GuardrailsEnabled { get; init; } = true;

    /// <summary>
    /// When true, reject (not just log) an inter-agent request whose
    /// forwarded X-User-Email/X-User-Role headers look spoofed (unknown
    /// role, malformed email). Mirrors Python's
    /// <c>GUARDRAILS_STRICT_IDENTITY</c> — observe-only by default.
    /// </summary>
    public bool GuardrailsStrictIdentity { get; init; }

    /// <summary>
    /// When false (default), an inbound message carrying a prompt-injection
    /// signal is flagged (logged + recorded on
    /// <see cref="Context.RequestContext"/>'s guardrail flags) but still
    /// reaches the LLM — the active defenses are the prompt-layer refusal
    /// rules and tool-output sanitization. When true, the flagged message is
    /// refused before it ever reaches the chat client. Mirrors Python's
    /// <c>GUARDRAILS_BLOCK_ON_INJECTION</c>.
    /// </summary>
    public bool GuardrailsBlockOnInjection { get; init; }

    /// <summary>
    /// Gates <c>OutputSanitizationMiddleware</c>-equivalent stored-injection
    /// defense (defanging adversarial instructions hiding inside untrusted
    /// tool output — reviews, product descriptions, order notes) — distinct
    /// from <see cref="GuardrailsBlockOnInjection"/>, which is about
    /// user-typed input, not tool results. Mirrors Python's
    /// <c>GUARDRAILS_OUTPUT_SANITIZATION</c> — on by default.
    /// </summary>
    public bool GuardrailsOutputSanitization { get; init; } = true;

    /// <summary>
    /// <c>off</c> | <c>observe</c> | <c>enforce</c> — classifies the model's
    /// own generated text against a coarse local content-policy classifier
    /// (self-harm, violence, hate/harassment, sexual content), a different
    /// concern from stored-injection sanitization above (that's about
    /// untrusted input; this is about the model's own output). Mirrors
    /// Python's <c>OUTPUT_MODERATION_MODE</c> — <c>observe</c> by default.
    /// <c>enforce</c> can only replace a non-streaming response; a streamed
    /// response's chunks are already on the wire by the time the full text
    /// is known, so <c>enforce</c> mode only flags it there (same documented
    /// trade-off as the Python side).
    /// </summary>
    public string OutputModerationMode { get; init; } = "observe";

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

    // ── Tool-level HITL approval queue ──────────────────────────
    // Mirrors Python's settings.HITL_ENABLED (shared/config.py). Distinct
    // from ReturnHitlThreshold above, which gates the WorkflowBuilder-style
    // return/replace workflow specifically — this flag gates the generic
    // cross-agent tool-interception queue (HitlApprovalMiddleware).
    public bool HitlEnabled { get; init; } = true;
}
