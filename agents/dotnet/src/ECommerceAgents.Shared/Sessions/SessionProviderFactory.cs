using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;

namespace ECommerceAgents.Shared.Sessions;

/// <summary>
/// Keyed off <see cref="AgentSettings.MafSessionBackend"/> (values
/// <c>postgres</c> | <c>file</c> | <c>memory</c>). Matches the Python
/// <c>get_history_provider()</c> factory shape exactly.
/// </summary>
public static class SessionProviderFactory
{
    public static ISessionHistoryProvider Build(AgentSettings settings, DatabasePool? pool = null)
    {
        var backend = string.IsNullOrEmpty(settings.MafSessionBackend)
            ? "postgres"
            : settings.MafSessionBackend.ToLowerInvariant();

        return backend switch
        {
            "memory" => new InMemorySessionHistoryProvider(),
            "file" => new FileSessionHistoryProvider(settings.MafSessionDir),
            "postgres" => pool is null
                ? throw new InvalidOperationException(
                    "PostgresSessionHistoryProvider requires a DatabasePool — pass one to Build or set MAF_SESSION_BACKEND=file|memory for dev."
                )
                : new PostgresSessionHistoryProvider(pool),
            _ => throw new InvalidOperationException($"Unknown MAF_SESSION_BACKEND: {backend}"),
        };
    }
}
