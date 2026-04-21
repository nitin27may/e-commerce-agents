namespace ECommerceAgents.Shared.Checkpoints;

/// <summary>
/// A frozen snapshot of a running workflow. Mirrors the fields MAF's
/// Python <c>WorkflowCheckpoint</c> round-trips through JSONB: an id,
/// a workflow name, a timestamp, and an opaque payload that the
/// runtime re-hydrates into executor state.
/// </summary>
public sealed record WorkflowCheckpoint(
    string CheckpointId,
    string WorkflowName,
    DateTimeOffset Timestamp,
    string PayloadJson
);

/// <summary>
/// Durable checkpoint store. Three implementations live alongside:
/// <see cref="InMemoryCheckpointStorage"/>,
/// <see cref="FileCheckpointStorage"/>,
/// <see cref="PostgresCheckpointStorage"/> — matching Python's
/// <c>MAF_CHECKPOINT_BACKEND</c> values.
/// </summary>
public interface ICheckpointStorage
{
    Task<string> SaveAsync(WorkflowCheckpoint checkpoint, CancellationToken ct = default);
    Task<WorkflowCheckpoint?> LoadAsync(string checkpointId, CancellationToken ct = default);
    Task<IReadOnlyList<string>> ListCheckpointIdsAsync(string workflowName, CancellationToken ct = default);
    Task<WorkflowCheckpoint?> GetLatestAsync(string workflowName, CancellationToken ct = default);
    Task<bool> DeleteAsync(string checkpointId, CancellationToken ct = default);
}
