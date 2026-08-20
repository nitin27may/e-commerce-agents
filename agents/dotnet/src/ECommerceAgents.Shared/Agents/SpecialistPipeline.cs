using System.Diagnostics;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Middleware;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// Composes the cross-cutting middleware stack every specialist / orchestrator
/// agent picks up, mirroring Python's
/// <c>shared/middleware.py::build_specialist_middleware()</c>. This is the
/// wiring point that finally activates <see cref="AgentRunLogger"/>,
/// <see cref="ToolAuditMiddleware"/> and <see cref="PiiRedactor"/> — until
/// this existed, those classes were exercised only by
/// <c>ECommerceAgents.Shared.Tests.MiddlewareTests</c>, never attached to a
/// running <see cref="AIAgent"/> (see issue #12).
/// </summary>
/// <remarks>
/// Python's stack has several additional gated layers (injection detection,
/// output sanitization, grounding, cost budget, output moderation) that have
/// no .NET port yet — this composes only the four building blocks that exist
/// in this codebase today. None of the four are behind a feature flag in
/// Python either (only HITL and the not-yet-ported guardrail layers are), so
/// none are gated here.
/// </remarks>
public static class SpecialistPipeline
{
    /// <summary>
    /// Wraps <paramref name="inner"/> in, in order: agent-run logging (outermost,
    /// so it times the whole run including every layer below it), PII
    /// redaction of inbound messages, and per-tool-call audit logging.
    /// </summary>
    public static AIAgent Apply(AIAgent inner, AgentSettings settings, IServiceProvider services)
    {
        var runLogger = services.GetRequiredService<AgentRunLogger>();
        var redactor = services.GetRequiredService<PiiRedactor>();
        var toolAudit = services.GetRequiredService<ToolAuditMiddleware>();
        var logger = services.GetRequiredService<ILogger<AgentRunLoggerAdapter>>();

        return inner
            .AsBuilder()
            .Use(WrapAgentRun(logger))
            .Use(RedactInboundMessages(redactor))
            .Use(AuditToolCalls(toolAudit))
            .Build(services);
    }

    /// <summary>
    /// Agent-run timing/correlation-id logging around the whole call (both
    /// <c>RunAsync</c> and <c>RunStreamingAsync</c> share this one delegate —
    /// the shared-func <c>Use</c> overload dispatches to whichever the caller
    /// invoked). Direct <see cref="ILogger"/> logging rather than routing
    /// through <see cref="AgentRunLogger"/>'s own generic wrapper, since that
    /// wrapper's <c>Func&lt;string, Task&lt;T&gt;&gt;</c> shape predates this
    /// pipeline and is kept for its existing unit tests
    /// (<c>MiddlewareTests.AgentRunLogger_*</c>) rather than reshaped to fit —
    /// the log line format matches it exactly.
    /// </summary>
    private static Func<
        IEnumerable<ChatMessage>,
        AgentSession?,
        AgentRunOptions?,
        Func<IEnumerable<ChatMessage>, AgentSession?, AgentRunOptions?, CancellationToken, Task>,
        CancellationToken,
        Task
    > WrapAgentRun(ILogger logger) =>
        async (messages, session, options, next, ct) =>
        {
            var runId = Guid.NewGuid().ToString("N")[..8];
            var sw = Stopwatch.StartNew();
            logger.LogInformation("agent.start run_id={RunId}", runId);
            try
            {
                await next(messages, session, options, ct);
                logger.LogInformation(
                    "agent.finish run_id={RunId} elapsed_ms={Elapsed:F1}",
                    runId,
                    sw.Elapsed.TotalMilliseconds
                );
            }
            catch (Exception ex)
            {
                logger.LogError(
                    ex,
                    "agent.fail run_id={RunId} elapsed_ms={Elapsed:F1}",
                    runId,
                    sw.Elapsed.TotalMilliseconds
                );
                throw;
            }
        };

    /// <summary>
    /// Masks credit-card/SSN-shaped substrings in inbound message text before
    /// they reach the chat client — the .NET twin of Python's
    /// <c>PiiRedactionMiddleware</c> (a <c>ChatMiddleware</c> there; here it's
    /// the agent-pipeline seam, since .NET's <see cref="AIAgentBuilder"/> has
    /// no separate chat-client-level hook for this SDK version).
    /// </summary>
    private static Func<
        IEnumerable<ChatMessage>,
        AgentSession?,
        AgentRunOptions?,
        Func<IEnumerable<ChatMessage>, AgentSession?, AgentRunOptions?, CancellationToken, Task>,
        CancellationToken,
        Task
    > RedactInboundMessages(PiiRedactor redactor) =>
        (messages, session, options, next, ct) =>
        {
            var redacted = messages.Select(m =>
            {
                var text = m.Text;
                var cleaned = redactor.Redact(text);
                if (cleaned == text)
                {
                    return m;
                }

                return new ChatMessage(m.Role, cleaned)
                {
                    AuthorName = m.AuthorName,
                    MessageId = m.MessageId,
                    CreatedAt = m.CreatedAt,
                };
            });

            return next(redacted, session, options, ct);
        };

    /// <summary>
    /// Per-tool-call audit logging via <see cref="ToolAuditMiddleware"/> — the
    /// .NET twin of Python's <c>ToolAuditMiddleware</c> (there, a
    /// <c>FunctionMiddleware</c>; here, the <c>AIAgentBuilder</c>
    /// function-invocation seam added in MAF .NET 1.18).
    /// </summary>
    private static Func<
        AIAgent,
        FunctionInvocationContext,
        Func<FunctionInvocationContext, CancellationToken, ValueTask<object?>>,
        CancellationToken,
        ValueTask<object?>
    > AuditToolCalls(ToolAuditMiddleware audit) =>
        (agent, context, next, ct) =>
            new ValueTask<object?>(audit.RecordAsync(context.Function.Name, () => next(context, ct).AsTask()));

    /// <summary>Marker type purely so <see cref="WrapAgentRun"/> can resolve a category-scoped logger.</summary>
    private sealed class AgentRunLoggerAdapter { }
}
