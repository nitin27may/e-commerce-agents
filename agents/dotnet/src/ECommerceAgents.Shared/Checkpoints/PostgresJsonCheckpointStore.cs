using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Agents.AI.Workflows.Checkpointing;
using System.Text.Json;

namespace ECommerceAgents.Shared.Checkpoints;

/// <summary>
/// Durable checkpoint store for MAF workflows, backed by <c>workflow_checkpoints</c>.
/// </summary>
/// <remarks>
/// This implements MAF's own <see cref="ICheckpointStore{T}"/> rather than the bespoke
/// interface that used to live here. That interface keyed on an id and a workflow name;
/// MAF keys on <c>(sessionId, CheckpointInfo)</c> with parent filtering, so adapting one
/// to the other meant inventing a session id and a parent link — which is how you get a
/// resume that quietly loads the wrong superstep.
///
/// It replaces four files that were registered in <c>Program.cs</c> and called by
/// nothing: <c>workflow_checkpoints</c> was never written on .NET, so
/// <c>GET /api/runs/{id}/checkpoints</c> always answered with an empty list while
/// <c>MAF_CHECKPOINT_BACKEND</c> looked like it was doing something. Their tests passed
/// throughout, against code no request could reach.
///
/// <b>Ordering is part of the contract.</b> MAF resumes the checkpoint its index returns
/// first, so <see cref="RetrieveIndexAsync"/> orders by <c>seq</c> — a BIGSERIAL, because
/// <c>created_at</c> can tie at this resolution and a tie here means resuming the wrong
/// step.
///
/// <b>This store owns checkpoint ids.</b> MAF's are opaque strings; the column is a UUID
/// primary key that <c>hitl_requests.checkpoint_id</c> has a foreign key onto. So a Guid
/// is minted here and returned in the <see cref="CheckpointInfo"/>, which MAF then uses
/// as the key — verified by <c>MafCheckpointResumeSpikeTests</c>. Widening the column
/// would have broken that FK for both stacks.
/// </remarks>
public sealed class PostgresJsonCheckpointStore(DatabasePool pool, string workflowName)
    : ICheckpointStore<JsonElement>
{
    private readonly DatabasePool _pool = pool;
    private readonly string _workflowName = workflowName;

    public async ValueTask<CheckpointInfo> CreateCheckpointAsync(
        string sessionId,
        JsonElement value,
        CheckpointInfo? parent = null
    )
    {
        var checkpointId = Guid.NewGuid();
        Guid? parentId = parent is not null && Guid.TryParse(parent.CheckpointId, out var p) ? p : null;

        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"INSERT INTO workflow_checkpoints
                  (checkpoint_id, workflow_name, payload, session_id, parent_checkpoint_id)
              VALUES (@checkpointId, @workflowName, @payload::jsonb, @sessionId, @parentId)",
            new
            {
                checkpointId,
                workflowName = _workflowName,
                payload = value.GetRawText(),
                sessionId,
                parentId,
            }
        );

        return new CheckpointInfo(sessionId, checkpointId.ToString());
    }

    public async ValueTask<JsonElement> RetrieveCheckpointAsync(string sessionId, CheckpointInfo key)
    {
        ArgumentNullException.ThrowIfNull(key);

        if (!Guid.TryParse(key.CheckpointId, out var checkpointId))
        {
            throw new KeyNotFoundException($"Checkpoint id is not a GUID: {key.CheckpointId}");
        }

        await using var conn = await _pool.OpenAsync();
        var payload = await conn.ExecuteScalarAsync<string?>(
            "SELECT payload FROM workflow_checkpoints WHERE checkpoint_id = @checkpointId AND session_id = @sessionId",
            new { checkpointId, sessionId }
        );

        if (payload is null)
        {
            // Scoped by session on purpose: a checkpoint id from another session is a
            // correlation bug, and resuming it would run someone else's workflow.
            throw new KeyNotFoundException(
                $"No checkpoint {key.CheckpointId} for session {sessionId}."
            );
        }

        return JsonDocument.Parse(payload).RootElement.Clone();
    }

    public async ValueTask<IEnumerable<CheckpointInfo>> RetrieveIndexAsync(
        string sessionId,
        CheckpointInfo? withParent = null
    )
    {
        Guid? parentId = withParent is not null && Guid.TryParse(withParent.CheckpointId, out var p)
            ? p
            : null;

        await using var conn = await _pool.OpenAsync();
        var ids = await conn.QueryAsync<Guid>(
            @"SELECT checkpoint_id FROM workflow_checkpoints
              WHERE session_id = @sessionId
                AND (@parentId::uuid IS NULL OR parent_checkpoint_id = @parentId)
              ORDER BY seq",
            new { sessionId, parentId }
        );

        return ids.Select(id => new CheckpointInfo(sessionId, id.ToString())).ToList();
    }
}
