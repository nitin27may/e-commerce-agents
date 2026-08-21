using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Agents.AI.Workflows.Checkpointing;
using System.Text.Json;

namespace ECommerceAgents.Shared.Checkpoints;

/// <summary>
/// Builds the <see cref="CheckpointManager"/> workflows check point through, keyed off
/// <see cref="AgentSettings.MafCheckpointBackend"/> — the same
/// <c>memory | file | postgres</c> values Python's <c>MAF_CHECKPOINT_BACKEND</c> takes.
/// </summary>
/// <remarks>
/// Returns MAF's own <see cref="CheckpointManager"/> rather than a bespoke interface.
/// Two of the three backends are now SDK-provided, and the third is
/// <see cref="PostgresJsonCheckpointStore"/>. Until this change the setting was read,
/// validated, registered in DI — and consumed by nothing.
/// </remarks>
public static class CheckpointStorageFactory
{
    /// <summary>
    /// Types that travel inside a checkpoint. Registering them matters: a paused run's
    /// outstanding <c>ExternalRequest</c> carries its payload as a <c>PortableValue</c>,
    /// so without these the pause itself will not serialize even though the rest of the
    /// state does — and the failure surfaces later, at resume, as a request that cannot
    /// be rehydrated.
    /// </summary>
    public static JsonSerializerOptions SerializerOptions { get; } = new()
    {
        PropertyNamingPolicy = null,
    };

    public static CheckpointManager Build(AgentSettings settings, DatabasePool? pool = null, string workflowName = "workflow")
    {
        ArgumentNullException.ThrowIfNull(settings);

        var backend = string.IsNullOrEmpty(settings.MafCheckpointBackend)
            ? "postgres"
            : settings.MafCheckpointBackend.ToLowerInvariant();

        return backend switch
        {
            "memory" => CheckpointManager.CreateInMemory(),
            "file" => CheckpointManager.CreateJson(
                new FileSystemJsonCheckpointStore(Directory.CreateDirectory(settings.MafCheckpointDir)),
                SerializerOptions
            ),
            "postgres" => pool is null
                ? throw new InvalidOperationException(
                    "The postgres checkpoint backend requires a DatabasePool — pass one to Build, or set MAF_CHECKPOINT_BACKEND=file|memory for dev."
                )
                : CheckpointManager.CreateJson(new PostgresJsonCheckpointStore(pool, workflowName), SerializerOptions),
            _ => throw new InvalidOperationException($"Unknown MAF_CHECKPOINT_BACKEND: {backend}"),
        };
    }
}
