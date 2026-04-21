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
        "agent-internal-secret",
        "agent-internal-shared-secret",
    };

    public static void Validate(AgentSettings settings, ILogger logger)
    {
        var isProd = !IsDevelopmentEnv(settings.Environment);
        Check("JWT_SECRET", settings.JwtSecret, isProd, logger);
        Check("AGENT_SHARED_SECRET", settings.AgentSharedSecret, isProd, logger);
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
