using Dapper;
using ECommerceAgents.Shared.Data;

namespace ECommerceAgents.Shared.Checkpoints;

/// <summary>
/// .NET twin of Python's <c>PostgresCheckpointStorage</c>. Reads +
/// writes the <c>workflow_checkpoints</c> table defined in
/// <c>docker/postgres/init.sql</c>. Upserts on conflict so re-saving
/// the same checkpoint id updates the row in place.
/// </summary>
public sealed class PostgresCheckpointStorage(DatabasePool pool) : ICheckpointStorage
{
    private readonly DatabasePool _pool = pool;

    public async Task<string> SaveAsync(WorkflowCheckpoint checkpoint, CancellationToken ct = default)
    {
        if (!Guid.TryParse(checkpoint.CheckpointId, out var id))
        {
            throw new ArgumentException("CheckpointId must be a UUID", nameof(checkpoint));
        }

        await using var conn = await _pool.OpenAsync(ct);
        await conn.ExecuteAsync(
            @"INSERT INTO workflow_checkpoints (checkpoint_id, workflow_name, payload, created_at)
              VALUES (@id, @name, @payload::jsonb, @ts)
              ON CONFLICT (checkpoint_id)
              DO UPDATE SET payload = EXCLUDED.payload,
                            created_at = EXCLUDED.created_at",
            new
            {
                id,
                name = checkpoint.WorkflowName,
                payload = checkpoint.PayloadJson,
                ts = checkpoint.Timestamp.UtcDateTime,
            }
        );
        return checkpoint.CheckpointId;
    }

    public async Task<WorkflowCheckpoint?> LoadAsync(string checkpointId, CancellationToken ct = default)
    {
        if (!Guid.TryParse(checkpointId, out var id)) return null;
        await using var conn = await _pool.OpenAsync(ct);
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT checkpoint_id, workflow_name, payload::text AS payload_text, created_at
              FROM workflow_checkpoints
              WHERE checkpoint_id = @id",
            new { id }
        );
        return row is null ? null : ToCheckpoint(row);
    }

    public async Task<IReadOnlyList<string>> ListCheckpointIdsAsync(string workflowName, CancellationToken ct = default)
    {
        await using var conn = await _pool.OpenAsync(ct);
        var rows = await conn.QueryAsync<Guid>(
            @"SELECT checkpoint_id
              FROM workflow_checkpoints
              WHERE workflow_name = @name
              ORDER BY created_at DESC",
            new { name = workflowName }
        );
        return rows.Select(id => id.ToString()).ToList();
    }

    public async Task<WorkflowCheckpoint?> GetLatestAsync(string workflowName, CancellationToken ct = default)
    {
        await using var conn = await _pool.OpenAsync(ct);
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT checkpoint_id, workflow_name, payload::text AS payload_text, created_at
              FROM workflow_checkpoints
              WHERE workflow_name = @name
              ORDER BY created_at DESC
              LIMIT 1",
            new { name = workflowName }
        );
        return row is null ? null : ToCheckpoint(row);
    }

    public async Task<bool> DeleteAsync(string checkpointId, CancellationToken ct = default)
    {
        if (!Guid.TryParse(checkpointId, out var id)) return false;
        await using var conn = await _pool.OpenAsync(ct);
        var affected = await conn.ExecuteAsync(
            "DELETE FROM workflow_checkpoints WHERE checkpoint_id = @id",
            new { id }
        );
        return affected > 0;
    }

    private static WorkflowCheckpoint ToCheckpoint(dynamic row) => new(
        CheckpointId: ((Guid)row.checkpoint_id).ToString(),
        WorkflowName: (string)row.workflow_name,
        Timestamp: new DateTimeOffset((DateTime)row.created_at, TimeSpan.Zero),
        PayloadJson: (string)row.payload_text
    );
}
