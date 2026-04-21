using System.Collections.Concurrent;

namespace ECommerceAgents.Shared.Checkpoints;

public sealed class InMemoryCheckpointStorage : ICheckpointStorage
{
    private readonly ConcurrentDictionary<string, WorkflowCheckpoint> _store = new();

    public Task<string> SaveAsync(WorkflowCheckpoint checkpoint, CancellationToken ct = default)
    {
        _store[checkpoint.CheckpointId] = checkpoint;
        return Task.FromResult(checkpoint.CheckpointId);
    }

    public Task<WorkflowCheckpoint?> LoadAsync(string checkpointId, CancellationToken ct = default) =>
        Task.FromResult(_store.TryGetValue(checkpointId, out var cp) ? cp : null);

    public Task<IReadOnlyList<string>> ListCheckpointIdsAsync(string workflowName, CancellationToken ct = default)
    {
        var ids = _store.Values
            .Where(c => c.WorkflowName == workflowName)
            .OrderByDescending(c => c.Timestamp)
            .Select(c => c.CheckpointId)
            .ToList();
        return Task.FromResult<IReadOnlyList<string>>(ids);
    }

    public Task<WorkflowCheckpoint?> GetLatestAsync(string workflowName, CancellationToken ct = default)
    {
        var latest = _store.Values
            .Where(c => c.WorkflowName == workflowName)
            .OrderByDescending(c => c.Timestamp)
            .FirstOrDefault();
        return Task.FromResult(latest);
    }

    public Task<bool> DeleteAsync(string checkpointId, CancellationToken ct = default) =>
        Task.FromResult(_store.TryRemove(checkpointId, out _));
}
