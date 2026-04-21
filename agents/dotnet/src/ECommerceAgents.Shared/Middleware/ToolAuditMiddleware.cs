using Microsoft.Extensions.Logging;
using System.Collections.Concurrent;
using System.Diagnostics;

namespace ECommerceAgents.Shared.Middleware;

/// <summary>
/// Audit every tool invocation: name, caller, latency, success flag.
/// Mirrors Python's <c>ToolAuditMiddleware</c>. Does NOT enforce
/// approval — that lives on MAF's native approval flow. This just
/// records what happened so we can dashboard it.
/// </summary>
/// <remarks>
/// Delegate-invoked: agents that want audit trails wrap every tool
/// call in
/// <code>
/// await audit.RecordAsync(toolName, async () => { …tool body… });
/// </code>
/// Keeps the middleware thread-safe (ConcurrentQueue) so multiple
/// concurrent tool calls from the same agent share one instance
/// without racing.
/// </remarks>
public sealed class ToolAuditMiddleware
{
    public sealed record ToolAuditRecord(
        string Tool,
        double ElapsedMs,
        string? Error,
        DateTimeOffset AtUtc
    );

    private readonly ILogger<ToolAuditMiddleware> _logger;
    private readonly ConcurrentQueue<ToolAuditRecord> _audited = new();

    public ToolAuditMiddleware(ILogger<ToolAuditMiddleware> logger) => _logger = logger;

    public IReadOnlyCollection<ToolAuditRecord> Audited => _audited;

    public async Task<T> RecordAsync<T>(string toolName, Func<Task<T>> body)
    {
        var sw = Stopwatch.StartNew();
        string? error = null;
        try
        {
            return await body();
        }
        catch (Exception ex)
        {
            error = $"{ex.GetType().Name}: {ex.Message}";
            throw;
        }
        finally
        {
            sw.Stop();
            var record = new ToolAuditRecord(
                Tool: toolName,
                ElapsedMs: Math.Round(sw.Elapsed.TotalMilliseconds, 2),
                Error: error,
                AtUtc: DateTimeOffset.UtcNow
            );
            _audited.Enqueue(record);
            _logger.LogInformation(
                "tool.invoked name={Tool} elapsed_ms={Elapsed:F1} error={Error}",
                toolName,
                sw.Elapsed.TotalMilliseconds,
                error ?? "-"
            );
        }
    }

    public async Task RecordAsync(string toolName, Func<Task> body)
    {
        await RecordAsync(toolName, async () =>
        {
            await body();
            return 0;
        });
    }
}
