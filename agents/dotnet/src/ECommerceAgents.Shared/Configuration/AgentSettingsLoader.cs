using System.Globalization;
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
    /// Accepts the four grounding modes Python has: <c>off</c>, <c>observe</c>, <c>annotate</c> and
    /// <c>enforce</c>. Anything else fails at startup, so a typo cannot silently fall back to a weaker mode.
    /// </summary>
    private static string ValidatedGroundingMode(string raw)
    {
        var mode = raw.Trim().ToLowerInvariant();
        if (mode is "off" or "observe" or "annotate" or "enforce")
        {
            return mode;
        }

        throw new InvalidOperationException(
            $"GROUNDING_MODE='{raw}' is not supported. Use off, observe, annotate or enforce.");
    }

    /// <summary>
    /// <c>A2A_TRANSPORT</c> is <c>legacy</c> or <c>a2a</c>; anything else fails at startup (Python's field is a
    /// <c>Literal</c>), so a typo cannot silently fall back to the other wire.
    /// </summary>
    private static string ValidatedA2aTransport(string raw)
    {
        var value = raw.Trim().ToLowerInvariant();
        if (value is "legacy" or "a2a")
        {
            return value;
        }

        throw new InvalidOperationException($"A2A_TRANSPORT='{raw}' is not supported. Use legacy or a2a.");
    }

    /// <summary><c>AZURE_OPENAI_AUTH</c> is <c>key</c> (blank too) or <c>identity</c>; anything else fails at startup.</summary>
    private static string ValidatedAzureAuth(string raw)
    {
        var value = raw.Trim().ToLowerInvariant();
        if (value.Length == 0)
        {
            return "key";
        }

        return value is "key" or "identity"
            ? value
            : throw new InvalidOperationException($"AZURE_OPENAI_AUTH='{raw}' is not supported. Use key or identity.");
    }

    /// <summary>A bounded integer setting; out of range fails at startup (Python's <c>Field(ge=, le=)</c> does too).</summary>
    private static int InRange(string name, int value, int min, int max) =>
        value >= min && value <= max
            ? value
            : throw new InvalidOperationException($"{name}={value} is out of range ({min} to {max}).");

    /// <summary>
    /// <c>A2A_IDENTITY</c> is <c>headers</c>, <c>token</c> or <c>both</c>; anything else fails at startup (Python's field
    /// is a <c>Literal</c>), so a typo cannot silently fall back to a different trust model.
    /// </summary>
    private static string ValidatedA2aIdentity(string raw)
    {
        var value = raw.Trim().ToLowerInvariant();
        if (value is "headers" or "token" or "both")
        {
            return value;
        }

        throw new InvalidOperationException($"A2A_IDENTITY='{raw}' is not supported. Use headers, token or both.");
    }

    /// <summary>The resume timeout must be positive and shorter than the claim TTL (mirrors Python's validator).</summary>
    private static int ValidatedResumeTimeout(int timeoutSeconds, int claimTtlSeconds)
    {
        if (timeoutSeconds <= 0 || timeoutSeconds >= claimTtlSeconds)
        {
            throw new InvalidOperationException(
                "HITL_RESUME_TIMEOUT_SECONDS must be positive and shorter than HITL_CLAIM_TTL_SECONDS "
                    + $"(got {timeoutSeconds} and {claimTtlSeconds}).");
        }

        return timeoutSeconds;
    }

    public static AgentSettings Load(IConfiguration config)
    {
        string Get(string key, string fallback = "") =>
            config[key] ?? Environment.GetEnvironmentVariable(key) ?? fallback;

        int EmbeddingDim()
        {
            var raw = Get("EMBEDDING_DIM");
            if (string.IsNullOrWhiteSpace(raw))
            {
                return AgentSettings.DefaultEmbeddingDim;
            }

            // Fail at startup rather than coerce: a silently-wrong width corrupts every stored vector.
            return int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var dim) && dim > 0
                ? dim
                : throw new InvalidOperationException("EMBEDDING_DIM must be a positive integer");
        }

        string AnthropicModel() =>
            string.IsNullOrWhiteSpace(Get("ANTHROPIC_MODEL")) ? AgentSettings.DefaultAnthropicModel : Get("ANTHROPIC_MODEL").Trim();

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

        // Unset keeps the fallback; set-but-empty means "none" (null); anything unparsable keeps the fallback.
        double? GetOptionalDouble(string key, double fallback)
        {
            var raw = config[key] ?? Environment.GetEnvironmentVariable(key);
            return raw is null ? fallback
                : string.IsNullOrWhiteSpace(raw) ? null
                : double.TryParse(raw, out var parsed) ? parsed
                : fallback;
        }

        int? GetNullableInt(string key)
        {
            var raw = Get(key);
            return string.IsNullOrWhiteSpace(raw) ? null
                : int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed) ? parsed
                : throw new InvalidOperationException($"{key} must be an integer");
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

            LlmProvider = Get("LLM_PROVIDER", "openai").Trim().ToLowerInvariant(),
            ReplayFixturesDir = Get("REPLAY_FIXTURES_DIR", "evals/fixtures/replay"),
            Record = Get("RECORD", "false").Equals("true", StringComparison.OrdinalIgnoreCase),
            ReplayRecordProvider = Get("REPLAY_RECORD_PROVIDER", "azure").ToLowerInvariant(),
            // With LLM_PROVIDER=anthropic the model is ANTHROPIC_MODEL (default claude-sonnet-5-5), not
            // LLM_MODEL: the compose files forward LLM_MODEL with a gpt-4.1 default that Anthropic would reject.
            LlmModel = Get("LLM_PROVIDER", "openai").Equals("anthropic", StringComparison.OrdinalIgnoreCase)
                ? AnthropicModel()
                : Get("LLM_MODEL", "gpt-4.1"),
            AnthropicModel = AnthropicModel(),
            AnthropicApiKey = Get("ANTHROPIC_API_KEY"),
            EmbeddingModel = Get("EMBEDDING_MODEL", "text-embedding-3-small"),
            EmbeddingProvider = Get("EMBEDDING_PROVIDER").Trim().ToLowerInvariant(),
            EmbeddingBaseUrl = Get("EMBEDDING_BASE_URL"),
            EmbeddingApiKey = Get("EMBEDDING_API_KEY"),
            EmbeddingDim = EmbeddingDim(),
            OpenAiApiKey = Get("OPENAI_API_KEY"),
            LlmBaseUrl = Get("LLM_BASE_URL"),
            GatewayHeadersEnabled = GetBool("GATEWAY_HEADERS_ENABLED", true),
            EvalRunId = Get("EVAL_RUN_ID"),
            RateLimitEnabled = GetBool("RATE_LIMIT_ENABLED", true),
            RateLimitMaxRequests = GetInt("RATE_LIMIT_MAX_REQUESTS", 30),
            RateLimitWindowSeconds = GetDouble("RATE_LIMIT_WINDOW_SECONDS", 60.0),
            RateLimitAuthMaxRequests = GetInt("RATE_LIMIT_AUTH_MAX_REQUESTS", 10),
            RateLimitAuthIpMaxRequests = GetInt("RATE_LIMIT_AUTH_IP_MAX_REQUESTS", 300),
            RateLimitAuthWindowSeconds = GetDouble("RATE_LIMIT_AUTH_WINDOW_SECONDS", 60.0),
            RateLimitTrustedProxyHops = GetInt("RATE_LIMIT_TRUSTED_PROXY_HOPS", 1),
            CorsAllowedOrigins = Get("CORS_ALLOWED_ORIGINS", "http://localhost:3000"),
            HandoffMaxTurns = GetInt("HANDOFF_MAX_TURNS", 3),
            GroundingMode = ValidatedGroundingMode(Get("GROUNDING_MODE", "annotate")),
            CostBudgetMode = Get("COST_BUDGET_MODE", "observe"),
            CostBudgetUsdPerRun = GetNullableDouble("COST_BUDGET_USD_PER_RUN"),
            CostBudgetTokensPerRun = GetNullableInt("COST_BUDGET_TOKENS_PER_RUN"),
            Temperature = GetOptionalDouble("LLM_TEMPERATURE", 0.2),
            DbPoolMinSize = GetNullableInt("DB_POOL_MIN_SIZE"),
            DbPoolMaxSize = GetNullableInt("DB_POOL_MAX_SIZE"),

            AzureOpenAiEndpoint = Get("AZURE_OPENAI_ENDPOINT"),
            AzureOpenAiAuth = ValidatedAzureAuth(Get("AZURE_OPENAI_AUTH")),
            AzureClientId = Get("AZURE_CLIENT_ID").Trim(),
            AzureOpenAiKey = GetWithAlias("AZURE_OPENAI_KEY", "AZURE_OPENAI_API_KEY"),
            AzureOpenAiDeployment = GetWithAlias("AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME"),
            AzureOpenAiApiVersion = Get("AZURE_OPENAI_API_VERSION", "2025-03-01-preview"),
            AzureEmbeddingDeployment = Get("AZURE_EMBEDDING_DEPLOYMENT"),

            JwtSecret = Get("JWT_SECRET", "change-me-in-production"),
            AgentSharedSecret = Get("AGENT_SHARED_SECRET", "agent-internal-secret"),

            AuthMode = Get("AUTH_MODE", "local").ToLowerInvariant(),
            AuthRefreshRotation = GetBool("AUTH_REFRESH_ROTATION", true),
            AuthRefreshFamilyMaxDays = GetInt("AUTH_REFRESH_FAMILY_MAX_DAYS", 30),
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
            A2aTransport = ValidatedA2aTransport(Get("A2A_TRANSPORT", "legacy")),
            A2aPublicBaseUrl = Get("A2A_PUBLIC_BASE_URL").Trim(),
            A2aMaxActiveTasks = InRange("A2A_MAX_ACTIVE_TASKS", GetInt("A2A_MAX_ACTIVE_TASKS", 32), 1, int.MaxValue),
            A2aMaxActiveTasksPerCaller = InRange("A2A_MAX_ACTIVE_TASKS_PER_CALLER", GetInt("A2A_MAX_ACTIVE_TASKS_PER_CALLER", 8), 1, int.MaxValue),
            A2aIdentity = ValidatedA2aIdentity(Get("A2A_IDENTITY", "both")),
            A2aIdentityKey = Get("A2A_IDENTITY_KEY").Trim(),
            A2aIdentityTtlSeconds = InRange("A2A_IDENTITY_TTL_SECONDS", GetInt("A2A_IDENTITY_TTL_SECONDS", 300), 30, 900),

            OtelEnabled = GetBool("OTEL_ENABLED", false),
            OtelExporterOtlpEndpoint = Get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:18889"),
            OtelServiceName = Get("OTEL_SERVICE_NAME", "ecommerce"),
            GenAiCaptureContent = GetBool("GENAI_CAPTURE_CONTENT", false),

            Environment = Get("ENVIRONMENT", "development"),
            LogLevel = Get("LOG_LEVEL", "INFO"),

            MafSessionBackend = Get("MAF_SESSION_BACKEND", "postgres").ToLowerInvariant(),
            MafSessionDir = Get("MAF_SESSION_DIR", "./.sessions"),
            HistoryCompactionKeepLastGroups = Math.Max(0, GetInt("HISTORY_COMPACTION_KEEP_LAST_GROUPS", 0)),
            MafCheckpointBackend = Get("MAF_CHECKPOINT_BACKEND", "postgres").ToLowerInvariant(),
            MafCheckpointDir = Get("MAF_CHECKPOINT_DIR", "./.checkpoints"),
            ReturnHitlThreshold = GetDouble("RETURN_HITL_THRESHOLD", 500.0),
            HandoffAutonomousMode = GetBool("HANDOFF_AUTONOMOUS_MODE", true),
            WorkflowVisualizationOnBuild = GetBool("WORKFLOW_VISUALIZATION_ON_BUILD", false),
            MafHandoffMode = Get("MAF_HANDOFF_MODE", "tool").ToLowerInvariant(),
            HitlEnabled = GetBool("HITL_ENABLED", true),
            HitlClaimTtlSeconds = GetInt("HITL_CLAIM_TTL_SECONDS", 600),
            HitlResumeTimeoutSeconds = ValidatedResumeTimeout(
                GetInt("HITL_RESUME_TIMEOUT_SECONDS", 300), GetInt("HITL_CLAIM_TTL_SECONDS", 600)),
            ApprovalReminderFraction = GetDouble("APPROVAL_REMINDER_FRACTION", 0.5),
            DotnetTimeoutWorkerEnabled = GetBool("DOTNET_TIMEOUT_WORKER_ENABLED", true),
            OutboxPollIntervalSeconds = GetDouble("OUTBOX_POLL_INTERVAL_SECONDS", 2.0),
            OutboxBatchSize = GetInt("OUTBOX_BATCH_SIZE", 10),
            OutboxMaxAttempts = GetInt("OUTBOX_MAX_ATTEMPTS", 8),
            OutboxLeaseSeconds = GetInt("OUTBOX_LEASE_SECONDS", 120),
            OutboxBackoffBaseSeconds = GetDouble("OUTBOX_BACKOFF_BASE_SECONDS", 5.0),
            OutboxBackoffMaxSeconds = GetDouble("OUTBOX_BACKOFF_MAX_SECONDS", 600.0),
        };
    }

    /// <summary>
    /// Parses <see cref="AgentSettings.AgentRegistry"/> into a
    /// <c>name → A2A base URL</c> map.
    /// </summary>
    /// <remarks>
    /// This used to swallow malformed JSON and return an empty dictionary "so
    /// callers don't have to try/catch themselves". The cost of that
    /// convenience is that a config typo produces an orchestrator that starts
    /// cleanly, passes its health check, and cannot route — which presents as
    /// the model refusing to use its specialists. Every failure now throws.
    ///
    /// That matters more once the value is assembled from infrastructure
    /// outputs rather than hand-written in a compose file: an unresolved
    /// template output arrives as the empty string, and a bare hostname
    /// arrives with no scheme. Scheme and host are checked; the port is not,
    /// because a managed endpoint does not have one and requiring it would
    /// reject exactly the deployment this validates for.
    ///
    /// Mirrors <c>shared/factory.py::parse_agent_registry</c> on the Python
    /// stack, including which inputs are rejected.
    /// </remarks>
    /// <exception cref="InvalidOperationException">
    /// The value is not a JSON object of <c>name → absolute http(s) URL</c>.
    /// </exception>
    public static IReadOnlyDictionary<string, string> ParseAgentRegistry(AgentSettings settings)
    {
        if (string.IsNullOrWhiteSpace(settings.AgentRegistry))
        {
            return new Dictionary<string, string>();
        }

        Dictionary<string, string>? map;
        try
        {
            map = System.Text.Json.JsonSerializer.Deserialize<Dictionary<string, string>>(
                settings.AgentRegistry
            );
        }
        catch (System.Text.Json.JsonException ex)
        {
            throw new InvalidOperationException(
                $"AGENT_REGISTRY is not valid JSON ({ex.Message}). Got: {settings.AgentRegistry}",
                ex
            );
        }

        if (map is null)
        {
            return new Dictionary<string, string>();
        }

        var parsed = new Dictionary<string, string>(map.Count);
        foreach (var (name, rawUrl) in map)
        {
            var url = rawUrl?.Trim() ?? string.Empty;
            if (url.Length == 0)
            {
                throw new InvalidOperationException(
                    $"AGENT_REGISTRY entry '{name}' has an empty URL. An unresolved template "
                        + "output or an unset variable usually looks like this."
                );
            }

            if (
                !Uri.TryCreate(url, UriKind.Absolute, out var uri)
                || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps)
            )
            {
                throw new InvalidOperationException(
                    $"AGENT_REGISTRY entry '{name}' must be an absolute http(s) URL, got '{url}'."
                );
            }

            parsed[name] = url;
        }

        return parsed;
    }
}
