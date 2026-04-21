using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;

namespace ECommerceAgents.Shared.Checkpoints;

/// <summary>Keyed off <see cref="AgentSettings.MafCheckpointBackend"/>.</summary>
public static class CheckpointStorageFactory
{
    public static ICheckpointStorage Build(AgentSettings settings, DatabasePool? pool = null)
    {
        var backend = string.IsNullOrEmpty(settings.MafCheckpointBackend)
            ? "postgres"
            : settings.MafCheckpointBackend.ToLowerInvariant();

        return backend switch
        {
            "memory" => new InMemoryCheckpointStorage(),
            "file" => new FileCheckpointStorage(settings.MafCheckpointDir),
            "postgres" => pool is null
                ? throw new InvalidOperationException(
                    "PostgresCheckpointStorage requires a DatabasePool — pass one to Build or set MAF_CHECKPOINT_BACKEND=file|memory for dev."
                )
                : new PostgresCheckpointStorage(pool),
            _ => throw new InvalidOperationException($"Unknown MAF_CHECKPOINT_BACKEND: {backend}"),
        };
    }
}
