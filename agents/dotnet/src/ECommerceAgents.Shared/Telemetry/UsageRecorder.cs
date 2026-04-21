using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.Logging;
using System.Diagnostics;
using System.Text.Json;

namespace ECommerceAgents.Shared.Telemetry;

/// <summary>
/// Writes agent invocations to <c>usage_logs</c> and per-tool steps to
/// <c>agent_execution_steps</c>. .NET parity port of
/// <c>agents/python/shared/usage_db.py</c>.
/// <para>
/// The <c>trace_id</c> column is populated from the active
/// <see cref="Activity.Current"/> so Aspire Dashboard can correlate the
/// usage row with the telemetry trace. Failures are swallowed and
/// logged — usage tracking must never break the caller.
/// </para>
/// </summary>
public sealed class UsageRecorder
{
    private readonly DatabasePool _pool;
    private readonly ILogger<UsageRecorder>? _logger;

    public UsageRecorder(DatabasePool pool, ILogger<UsageRecorder>? logger = null)
    {
        _pool = pool ?? throw new ArgumentNullException(nameof(pool));
        _logger = logger;
    }

    public async Task<Guid?> LogAgentUsageAsync(
        Guid? userId,
        string agentName,
        Guid? sessionId = null,
        string inputSummary = "",
        int tokensIn = 0,
        int tokensOut = 0,
        int toolCallsCount = 0,
        int durationMs = 0,
        string status = "success",
        string? errorMessage = null,
        CancellationToken ct = default
    )
    {
        try
        {
            await using var conn = await _pool.OpenAsync(ct);
            var id = await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO usage_logs
                  (user_id, agent_name, session_id, trace_id, input_summary,
                   tokens_in, tokens_out, tool_calls_count, duration_ms, status, error_message)
                  VALUES (@u, @n, @s, @t, @i, @ti, @to, @tc, @d, @st, @e)
                  RETURNING id",
                new
                {
                    u = userId,
                    n = agentName,
                    s = sessionId,
                    t = Activity.Current?.TraceId.ToString(),
                    i = Truncate(inputSummary, 500),
                    ti = tokensIn,
                    to = tokensOut,
                    tc = toolCallsCount,
                    d = durationMs,
                    st = status,
                    e = errorMessage,
                }
            );
            return id;
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to log agent usage for {Agent}", agentName);
            return null;
        }
    }

    public async Task LogExecutionStepAsync(
        Guid usageLogId,
        int stepIndex,
        string toolName,
        object? toolInput = null,
        object? toolOutput = null,
        string status = "success",
        int durationMs = 0,
        CancellationToken ct = default
    )
    {
        try
        {
            await using var conn = await _pool.OpenAsync(ct);
            await conn.ExecuteAsync(
                @"INSERT INTO agent_execution_steps
                  (usage_log_id, step_index, tool_name, tool_input, tool_output, status, duration_ms)
                  VALUES (@u, @i, @n, @in::jsonb, @out::jsonb, @st, @d)",
                new
                {
                    u = usageLogId,
                    i = stepIndex,
                    n = toolName,
                    @in = SafeJson(toolInput),
                    @out = SafeJson(toolOutput),
                    st = status,
                    d = durationMs,
                }
            );
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "Failed to log execution step {Index} for {Tool}", stepIndex, toolName);
        }
    }

    private static string? Truncate(string? s, int max)
    {
        if (string.IsNullOrEmpty(s)) return null;
        return s.Length <= max ? s : s[..max];
    }

    private static string? SafeJson(object? data)
    {
        if (data is null) return null;
        try
        {
            return JsonSerializer.Serialize(data);
        }
        catch
        {
            return "{\"error\":\"unserializable\"}";
        }
    }
}

/// <summary>
/// Stopwatch helper that matches the Python <c>UsageTimer</c> context
/// manager — use with <c>using var timer = new UsageTimer();</c> and
/// read <see cref="DurationMs"/> after the block.
/// </summary>
public sealed class UsageTimer : IDisposable
{
    private readonly long _start = Stopwatch.GetTimestamp();
    private long _elapsed;

    public int DurationMs => (int)(_elapsed * 1000 / Stopwatch.Frequency);

    public void Dispose()
    {
        _elapsed = Stopwatch.GetTimestamp() - _start;
    }
}
