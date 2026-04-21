using Microsoft.Extensions.Logging;
using System.Diagnostics;

namespace ECommerceAgents.Shared.Middleware;

/// <summary>
/// Emits start / finish log lines per agent invocation with a short
/// correlation id the caller can thread into its own logs. Mirrors
/// Python's <c>AgentRunLogger</c>.
/// </summary>
public sealed class AgentRunLogger
{
    private readonly ILogger<AgentRunLogger> _logger;

    public AgentRunLogger(ILogger<AgentRunLogger> logger) => _logger = logger;

    public async Task<T> RunAsync<T>(string agentName, Func<string, Task<T>> body)
    {
        var runId = Guid.NewGuid().ToString("N")[..8];
        var sw = Stopwatch.StartNew();
        _logger.LogInformation("agent.start agent={Agent} run_id={RunId}", agentName, runId);
        try
        {
            var result = await body(runId);
            _logger.LogInformation(
                "agent.finish agent={Agent} run_id={RunId} elapsed_ms={Elapsed:F1}",
                agentName,
                runId,
                sw.Elapsed.TotalMilliseconds
            );
            return result;
        }
        catch (Exception ex)
        {
            _logger.LogError(
                ex,
                "agent.fail agent={Agent} run_id={RunId} elapsed_ms={Elapsed:F1}",
                agentName,
                runId,
                sw.Elapsed.TotalMilliseconds
            );
            throw;
        }
    }
}
