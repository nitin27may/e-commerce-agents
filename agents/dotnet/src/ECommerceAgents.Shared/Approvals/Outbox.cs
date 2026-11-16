using Dapper;
using System.Text.Json;

namespace ECommerceAgents.Shared.Approvals;

/// <summary>
/// Transactional outbox: events and timers as rows in Postgres — the .NET twin of Python's <c>shared/outbox.py</c>.
/// <see cref="EnqueueAsync"/> runs on the CALLER's connection and transaction, so the row commits or rolls back with the
/// business write that caused it. A timer is a row whose <c>available_at</c> is in the future; <c>dedupe_key</c> makes an
/// enqueue idempotent. The worker that drains the table is Python's (<c>agents/python/worker/</c>) and serves both stacks.
/// </summary>
public static class Outbox
{
    /// <summary>Inserts one outbox row on <paramref name="tx"/>; returns its id, or <c>null</c> if <paramref name="dedupeKey"/> already exists.</summary>
    public static async Task<long?> EnqueueAsync(
        Tx tx,
        string topic,
        IReadOnlyDictionary<string, object?>? payload,
        DateTime availableAt,
        string? dedupeKey = null,
        Guid? aggregateId = null)
    {
        if (string.IsNullOrEmpty(topic)) throw new ArgumentException("an outbox row needs a topic", nameof(topic));
        return await tx.Conn.ExecuteScalarAsync<long?>(
            @"INSERT INTO outbox (topic, aggregate_id, payload, dedupe_key, available_at)
              VALUES (@topic, @aggregateId, @payload::jsonb, @dedupeKey, @availableAt)
              ON CONFLICT (dedupe_key) DO NOTHING
              RETURNING id",
            new
            {
                topic,
                aggregateId,
                payload = JsonSerializer.Serialize(payload ?? new Dictionary<string, object?>()),
                dedupeKey,
                availableAt = Approval.AsUtc(availableAt),
            },
            tx.Trans);
    }
}
