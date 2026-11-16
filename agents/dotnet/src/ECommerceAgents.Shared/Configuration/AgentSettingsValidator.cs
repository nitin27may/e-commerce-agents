using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.Configuration;

/// <summary>
/// Mirrors Python's <c>Settings._validate_secrets</c>. Rejects placeholder
/// or undersized <c>JWT_SECRET</c> / <c>AGENT_SHARED_SECRET</c> values in
/// production; logs a loud warning in development so the default
/// <c>.env.example</c> experience still works on a laptop.
/// </summary>
public static class AgentSettingsValidator
{
    /// <summary>HS256's minimum key size (bits) + .NET's own validator.</summary>
    public const int MinSecretBytes = 32;

    private static readonly HashSet<string> UnsafeDefaults = new(StringComparer.Ordinal)
    {
        "change-me-in-production",
        "change-me-generate-a-random-256-bit-key",
        "change-me-in-production-use-random-256-bit-key",
        "agent-internal-secret",
        "agent-internal-shared-secret",
        "dev-oauth-seed-change-me",
    };

    private static readonly HashSet<string> SupportedOutputModerationModes = new(StringComparer.Ordinal)
    {
        "off", "observe", "enforce",
    };

    public static void Validate(AgentSettings settings, ILogger logger)
    {
        // Names the variable, never its value.
        if (string.Equals(settings.LlmProvider, "anthropic", StringComparison.OrdinalIgnoreCase)
            && string.IsNullOrWhiteSpace(settings.AnthropicApiKey))
        {
            throw new InvalidOperationException("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic");
        }

        var isProd = !IsDevelopmentEnv(settings.Environment);
        Check("JWT_SECRET", settings.JwtSecret, isProd, logger);
        Check("AGENT_SHARED_SECRET", settings.AgentSharedSecret, isProd, logger);
        if (!string.IsNullOrWhiteSpace(settings.A2aIdentityKey))
        {
            Check("A2A_IDENTITY_KEY", settings.A2aIdentityKey, isProd, logger);
        }

        // `token` mode only protects against a caller holding just X-Agent-Secret when the signing key is a different
        // secret; with no key (it falls back to the shared secret) or the same value, holding the secret is holding the
        // key. Names the variables, never a value.
        var identityKey = (settings.A2aIdentityKey ?? string.Empty).Trim();
        if (string.Equals(settings.A2aIdentity, "token", StringComparison.Ordinal)
            && (identityKey.Length == 0 || string.Equals(identityKey, (settings.AgentSharedSecret ?? string.Empty).Trim(), StringComparison.Ordinal)))
        {
            logger.LogWarning(
                "settings.a2a_identity_key_not_distinct mode=token reason={Reason}: a caller holding only AGENT_SHARED_SECRET "
                + "can still mint user-context tokens; set A2A_IDENTITY_KEY to a different secret",
                identityKey.Length == 0 ? "key_empty" : "key_equals_shared_secret");
        }

        if (settings.AuthMode == "oauth")
        {
            Check("OAUTH_SEED_KEY", settings.OAuthSeedKey, isProd, logger);
        }

        if (!SupportedOutputModerationModes.Contains(settings.OutputModerationMode))
        {
            throw new InvalidOperationException(
                $"OUTPUT_MODERATION_MODE='{settings.OutputModerationMode}' is not a recognized value. " +
                "Supported values: off, observe, enforce."
            );
        }
    }

    private static void Check(string name, string value, bool failFast, ILogger logger)
    {
        var stripped = (value ?? string.Empty).Trim();
        var bytes = System.Text.Encoding.UTF8.GetByteCount(stripped);
        var tooShort = bytes < MinSecretBytes;
        var isDefault = UnsafeDefaults.Contains(stripped);

        if (!tooShort && !isDefault)
        {
            return;
        }

        var reason = isDefault
            ? "placeholder default"
            : $"{bytes} bytes < {MinSecretBytes}";
        var message =
            $"{name} is unsafe ({reason}). Generate a fresh random 256-bit value.";

        if (failFast)
        {
            throw new InvalidOperationException(message);
        }

        logger.LogWarning("settings.secret_unsafe var={Var} reason={Reason}", name, message);
    }

    private static bool IsDevelopmentEnv(string? env)
    {
        var value = (env ?? string.Empty).ToLowerInvariant();
        return value is "development" or "dev" or "test";
    }
}
