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

    /// <summary>
    /// <c>ANTHROPIC_API_KEY</c>, required when <c>LLM_PROVIDER=anthropic</c> (.NET only — the Python
    /// connector needs MAF core 1.24+; see docs/parity-matrix.md). Never logged.
    /// </summary>
    public string AnthropicApiKey { get; init; } = "";

    /// <summary>
    /// <c>ANTHROPIC_MODEL</c>. With <c>LLM_PROVIDER=anthropic</c> the loader copies it into
    /// <see cref="LlmModel"/> (which cost and telemetry read), instead of <c>LLM_MODEL</c> — the compose files
    /// forward <c>LLM_MODEL</c> with a gpt-4.1 default that Anthropic would reject.
    /// </summary>
    public string AnthropicModel { get; init; } = DefaultAnthropicModel;

    /// <summary>Other current ids, for the docs: <c>claude-opus-5-5</c>, <c>claude-haiku-5-5</c>.</summary>
    public const string DefaultAnthropicModel = "claude-sonnet-5-5";

    // ── Rate limiting (issue #30) — .NET twin of Python's RATE_LIMIT_* ──
    public bool RateLimitEnabled { get; init; } = true;
    public int RateLimitMaxRequests { get; init; } = 30;

    /// <summary>
    /// Autonomous-mode turn ceiling for the handoff mesh's triage agent.
    /// </summary>
    /// <remarks>
    /// Mirrors Python's <c>HANDOFF_MAX_TURNS</c>. Autonomous mode's contract is "if the
    /// agent does not hand off, feed it a continuation prompt and run it again", so this
    /// is the only bound on an agent that cannot hand off. MAF defaults to 50, which is
    /// how Python's handoff mode produced a 23,637-character monologue over 100-200
    /// seconds before its triage agent was made tool-free. Three covers hand-off,
    /// hand-back and wrap-up.
    /// </remarks>
    public int HandoffMaxTurns { get; init; } = 3;
    public double RateLimitWindowSeconds { get; init; } = 60.0;

    /// <summary>
    /// Signup / login / refresh buckets - Python's <c>RATE_LIMIT_AUTH_*</c>. The tight budget is per
    /// (client address, target email) for login/signup and per user for refresh, so one caller cannot
    /// starve the others; the address-only ceiling is far looser (behind the default web proxy every
    /// browser user shares one address).
    /// </summary>
    public int RateLimitAuthMaxRequests { get; init; } = 10;
    public int RateLimitAuthIpMaxRequests { get; init; } = 300;
    public double RateLimitAuthWindowSeconds { get; init; } = 60.0;

    /// <summary>
    /// Trusted reverse proxies that append to <c>X-Forwarded-For</c>; the client address is the
    /// entry this many places from the right. 0 ignores the header. Python's
    /// <c>RATE_LIMIT_TRUSTED_PROXY_HOPS</c>.
    /// </summary>
    public int RateLimitTrustedProxyHops { get; init; } = 1;

    /// <summary>Comma-separated browser origins allowed by CORS - Python's <c>CORS_ALLOWED_ORIGINS</c>.</summary>
    public string CorsAllowedOrigins { get; init; } = "http://localhost:3000";

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

    /// <summary>
    /// <c>EMBEDDING_PROVIDER</c>: "openai" | "azure" | "openai_compatible" | "replay"; empty follows
    /// <see cref="LlmProvider"/>, exactly as before. Python twin: <c>shared/embedding_config.py</c>.
    /// </summary>
    public string EmbeddingProvider { get; init; } = "";

    /// <summary><c>EMBEDDING_BASE_URL</c>: replaces <c>LLM_BASE_URL</c> (or the Azure endpoint) for embeddings only.</summary>
    public string EmbeddingBaseUrl { get; init; } = "";

    /// <summary><c>EMBEDDING_API_KEY</c>: replaces the chat key for embeddings only. Never logged.</summary>
    public string EmbeddingApiKey { get; init; } = "";

    /// <summary>
    /// <c>EMBEDDING_DIM</c>: the width of every vector; must equal the <c>vector(N)</c> columns
    /// (<c>scripts/reset_embeddings.py</c> changes them; <see cref="Agents.EmbeddingDimensionCheck"/> verifies).
    /// </summary>
    public int EmbeddingDim { get; init; } = DefaultEmbeddingDim;

    /// <summary>The baseline's column width (text-embedding-3-small).</summary>
    public const int DefaultEmbeddingDim = 1536;

    public string OpenAiApiKey { get; init; } = "";

    /// <summary>
    /// Sampling temperature pinned on every agent run so identical prompts
    /// yield consistent answers (the provider default of ~1.0 makes them
    /// diverge). Mirrors Python's <c>LLM_TEMPERATURE</c>
    /// (<c>shared/config.py</c>), same env var name and default. <c>null</c>
    /// (<c>LLM_TEMPERATURE</c> set but empty) sends no temperature at all: gpt-5.x
    /// models reject the parameter.
    /// </summary>
    public double? Temperature { get; init; } = 0.2;

    /// <summary>
    /// <c>DB_POOL_MIN_SIZE</c> / <c>DB_POOL_MAX_SIZE</c>: Npgsql pool bounds per process. <c>null</c>
    /// leaves Npgsql's own defaults (0 and 100), which is what every install had before the knobs.
    /// </summary>
    public int? DbPoolMinSize { get; init; }

    public int? DbPoolMaxSize { get; init; }

    public string AzureOpenAiEndpoint { get; init; } = "";

    /// <summary>Accepts either <c>AZURE_OPENAI_KEY</c> or the MAF-doc alias <c>AZURE_OPENAI_API_KEY</c>.</summary>
    public string AzureOpenAiKey { get; init; } = "";

    /// <summary>Accepts either <c>AZURE_OPENAI_DEPLOYMENT</c> or the alias <c>AZURE_OPENAI_DEPLOYMENT_NAME</c>.</summary>
    public string AzureOpenAiDeployment { get; init; } = "";

    public string AzureOpenAiApiVersion { get; init; } = "2025-03-01-preview";

    /// <summary>
    /// <c>AZURE_OPENAI_AUTH</c>: <c>key</c> (default) or <c>identity</c> (a Microsoft Entra token, no key).
    /// Python twin: <c>AZURE_OPENAI_AUTH</c> in <c>shared/config.py</c>.
    /// </summary>
    public string AzureOpenAiAuth { get; init; } = "key";

    /// <summary><c>AZURE_CLIENT_ID</c>: a user-assigned managed identity; empty uses <c>DefaultAzureCredential</c>.</summary>
    public string AzureClientId { get; init; } = "";
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

    /// <summary>
    /// AuthMode=local only: rotate refresh tokens with reuse detection (Python's
    /// <c>AUTH_REFRESH_ROTATION</c>). False restores "refresh returns only a new access token".
    /// </summary>
    public bool AuthRefreshRotation { get; init; } = true;

    /// <summary>
    /// Absolute lifetime of a refresh-token family from the login that started it (migration 0005).
    /// Rotation mints a fresh 7-day token each time, so without a cap a session that keeps
    /// refreshing never ends. Mirrors Python's <c>AUTH_REFRESH_FAMILY_MAX_DAYS</c>.
    /// </summary>
    public int AuthRefreshFamilyMaxDays { get; init; } = 30;

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

    /// <summary>
    /// <c>A2A_TRANSPORT</c>: which wire the <em>calling</em> side (orchestrator, handoff mesh) speaks to a
    /// specialist — <c>legacy</c> (the original <c>/message:send</c> + <c>/message:stream</c> pair, the
    /// default) or <c>a2a</c> (A2A 1.0 JSON-RPC at <c>/a2a/v1</c>). Every specialist serves both. Twin of
    /// Python's <c>A2A_TRANSPORT</c>.
    /// </summary>
    public string A2aTransport { get; init; } = "legacy";

    /// <summary>
    /// <c>A2A_PUBLIC_BASE_URL</c>: the origin a specialist advertises in its agent card. Empty derives it
    /// from the request (scheme + Host). Must name the same origin the registry uses to reach the
    /// specialist, or a calling client refuses the card.
    /// </summary>
    public string A2aPublicBaseUrl { get; init; } = "";

    /// <summary><c>A2A_MAX_ACTIVE_TASKS</c>: A2A tasks a specialist runs at once, in total (over it: refused, never queued).</summary>
    public int A2aMaxActiveTasks { get; init; } = 32;

    /// <summary><c>A2A_MAX_ACTIVE_TASKS_PER_CALLER</c>: the same bound per forwarded user.</summary>
    public int A2aMaxActiveTasksPerCaller { get; init; } = 8;

    /// <summary>
    /// <c>A2A_IDENTITY</c> (B-14): how a specialist learns which user a call is for. <c>headers</c>: the forwarded
    /// <c>X-User-*</c> headers, trusted on the strength of the inter-agent credential (the original way). <c>token</c>:
    /// only a signed, short-lived, audience-bound <c>X-User-Context</c> token; a call without a valid one is refused and
    /// the headers are not read. <c>both</c> (default, for one release): the orchestrator sends both, a specialist
    /// verifies a token when one is present (a header that contradicts it is refused) and falls back to the headers when
    /// there is none, so a mixed or half-upgraded deployment keeps working. See <see cref="Auth.UserContextToken"/>.
    /// </summary>
    public string A2aIdentity { get; init; } = "both";

    /// <summary>
    /// <c>A2A_IDENTITY_KEY</c>: the key the token is signed with (HKDF-derived). Empty uses
    /// <see cref="AgentSharedSecret"/> (nothing new to distribute). Set it to a value different from the shared secret
    /// so that holding <c>X-Agent-Secret</c> alone no longer lets a caller choose its user. Identical on every process,
    /// on both stacks.
    /// </summary>
    public string A2aIdentityKey { get; init; } = "";

    /// <summary><c>A2A_IDENTITY_TTL_SECONDS</c>: token lifetime (30 to 900; a verifier refuses any token living longer than 15 minutes).</summary>
    public int A2aIdentityTtlSeconds { get; init; } = 300;

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

    /// <summary>B-15: keep only the last N message groups of rehydrated history; 0 (default) is off.</summary>
    public int HistoryCompactionKeepLastGroups { get; init; }
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

    // How long a resume claim ('processing' on hitl_requests) may be held before
    // another approver can take it over. Mirrors Python's HITL_CLAIM_TTL_SECONDS.
    public int HitlClaimTtlSeconds { get; init; } = 600;

    // Hard timeout on one resume execution. Must be shorter than the claim TTL, or a stalled
    // resume could still be running when another approver takes its claim. Mirrors Python's
    // HITL_RESUME_TIMEOUT_SECONDS.
    public int HitlResumeTimeoutSeconds { get; init; } = 300;

    // The reminder timer fires this fraction of an approval's sla_hours after it enters review.
    // Mirrors Python's APPROVAL_REMINDER_FRACTION (shared/config.py).
    public double ApprovalReminderFraction { get; init; } = 0.5;
}
