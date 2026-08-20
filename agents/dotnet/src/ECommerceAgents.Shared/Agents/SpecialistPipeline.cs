using System.Diagnostics;
using System.Runtime.CompilerServices;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Guardrails;
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
/// Issue #15 adds the three guardrail layers Python has that .NET didn't:
/// inbound prompt-injection detection (with an optional hard-block escalation),
/// stored-injection output sanitization on allowlisted tool results, and
/// outbound content moderation of the model's own generated text. Python's
/// grounding and cost-budget layers still have no .NET port — out of scope
/// for #15, tracked separately.
///
/// Issue #16 adds agentic-timeline capture: <see cref="RecordSteps"/>
/// appends one <see cref="ExecutionStep"/> per tool call to
/// <see cref="RequestContext.CurrentSteps"/>, unconditionally (matching
/// Python's <c>StepRecorderMiddleware</c>, which isn't gated by any
/// setting). It's the last function-invocation stage so it records what
/// actually happened to a tool call after sanitization/audit — same
/// ordering Python uses (<c>STEP_MIDDLEWARE</c> appended last in
/// <c>build_specialist_middleware</c>).
/// </remarks>
public static class SpecialistPipeline
{
    /// <summary>
    /// Wraps <paramref name="inner"/> in, outermost to innermost: agent-run
    /// logging, inbound injection detection + outbound content moderation,
    /// PII redaction of inbound messages, per-tool-call audit logging, and
    /// stored-injection sanitization of allowlisted tool results.
    /// </summary>
    public static AIAgent Apply(AIAgent inner, AgentSettings settings, IServiceProvider services)
    {
        var runLogger = services.GetRequiredService<AgentRunLogger>();
        var redactor = services.GetRequiredService<PiiRedactor>();
        var toolAudit = services.GetRequiredService<ToolAuditMiddleware>();
        var logger = services.GetRequiredService<ILogger<AgentRunLoggerAdapter>>();
        var guardrailLogger = services.GetRequiredService<ILogger<GuardrailGateAdapter>>();

        var builder = inner
            .AsBuilder()
            .Use(WrapAgentRun(logger));

        if (settings.GuardrailsEnabled)
        {
            builder = builder.Use(GuardrailGateRun(settings, guardrailLogger), GuardrailGateStreaming(settings, guardrailLogger));
        }

        builder = builder.Use(RedactInboundMessages(redactor));

        if (settings.GuardrailsEnabled && settings.GuardrailsOutputSanitization)
        {
            builder = builder.Use(SanitizeToolOutput(guardrailLogger));
        }

        return builder
            .Use(AuditToolCalls(toolAudit))
            .Use(RecordSteps())
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

    /// <summary>
    /// Defangs stored-injection markers in allowlisted tool results (issue
    /// #15) — the .NET twin of Python's <c>OutputSanitizationMiddleware</c>.
    /// Runs the tool via <paramref name="next"/> first, then rewrites the
    /// returned object in place via <see cref="OutputSanitizer"/> if its
    /// name is in <see cref="SanitizeToolsConfig.SanitizeTools"/> — unlisted
    /// tools (structured/numeric results) pass through untouched.
    /// </summary>
    private static Func<
        AIAgent,
        FunctionInvocationContext,
        Func<FunctionInvocationContext, CancellationToken, ValueTask<object?>>,
        CancellationToken,
        ValueTask<object?>
    > SanitizeToolOutput(ILogger logger) =>
        async (agent, context, next, ct) =>
        {
            var result = await next(context, ct);
            if (!SanitizeToolsConfig.SanitizeTools.TryGetValue(context.Function.Name, out var fields))
            {
                return result;
            }

            var sanitized = OutputSanitizer.Sanitize(result, fields);
            if (!Equals(sanitized, result))
            {
                logger.LogInformation("guardrails.output_sanitized tool={Tool}", context.Function.Name);
            }
            return sanitized;
        };

    /// <summary>
    /// Appends one <see cref="ExecutionStep"/> per tool call to
    /// <see cref="RequestContext.CurrentSteps"/> — the .NET twin of Python's
    /// <c>StepRecorderMiddleware</c> (issue #16). A no-op when
    /// <see cref="RequestContext.CurrentSteps"/> has no active scope (the
    /// same "safe outside a request" property every other
    /// <c>RequestContext</c> writer already has).
    /// </summary>
    private static Func<
        AIAgent,
        FunctionInvocationContext,
        Func<FunctionInvocationContext, CancellationToken, ValueTask<object?>>,
        CancellationToken,
        ValueTask<object?>
    > RecordSteps() =>
        async (agent, context, next, ct) =>
        {
            var toolName = context.Function.Name;
            var toolInput = context.Arguments is null
                ? null
                : new Dictionary<string, object?>(context.Arguments);
            var sw = Stopwatch.StartNew();
            var status = "success";
            object? result = null;
            try
            {
                result = await next(context, ct);
                return result;
            }
            catch
            {
                status = "error";
                throw;
            }
            finally
            {
                RequestContext.RecordStep(new ExecutionStep(toolName, toolInput, result, status, (int)sw.ElapsedMilliseconds));
            }
        };

    private const string InjectionRefusalMessage =
        "I can't process that request — it looks like it contains an attempt to override " +
        "my instructions. If you have a genuine question, please rephrase it without the " +
        "embedded commands.";

    private const string ModerationRefusalMessage =
        "I'm not able to share that response — it was flagged by content moderation. " +
        "If this seems like a mistake, please rephrase your question.";

    /// <summary>
    /// Non-streaming half of the combined injection-detection +
    /// output-moderation gate (issue #15) — the .NET twin of Python's
    /// <c>InjectionDetectionChatMiddleware</c> and
    /// <c>OutputModerationMiddleware</c>, combined into one stage because
    /// both need full control over whether/what gets returned (block before
    /// calling the inner agent; replace after), which is exactly what the
    /// <c>Use(runFunc, streamingFunc)</c> overload — as opposed to the
    /// call-and-continue shared-func overload the other stages use — is for.
    /// </summary>
    private static Func<
        IEnumerable<ChatMessage>,
        AgentSession?,
        AgentRunOptions?,
        AIAgent,
        CancellationToken,
        Task<AgentResponse>
    > GuardrailGateRun(AgentSettings settings, ILogger logger) =>
        async (messages, session, options, innerAgent, ct) =>
        {
            if (InboundInjectionDetected(messages, settings, logger, out var refusal) && refusal is not null)
            {
                return new AgentResponse(new ChatMessage(ChatRole.Assistant, refusal));
            }

            var response = await innerAgent.RunAsync(messages, session, options, cancellationToken: ct);
            return CheckOutputModeration(response, settings, logger);
        };

    /// <summary>
    /// Streaming half of the same gate. Injection blocking works identically
    /// (a single refusal update instead of draining the inner stream at
    /// all). Output moderation on a stream can only ever log+flag, never
    /// un-send chunks already forwarded to the caller — same documented
    /// trade-off as Python's <c>OUTPUT_MODERATION_MODE=enforce</c> on a
    /// streamed response.
    /// </summary>
    private static Func<
        IEnumerable<ChatMessage>,
        AgentSession?,
        AgentRunOptions?,
        AIAgent,
        CancellationToken,
        IAsyncEnumerable<AgentResponseUpdate>
    > GuardrailGateStreaming(AgentSettings settings, ILogger logger) =>
        (messages, session, options, innerAgent, ct) => RunGuardedStream(messages, session, options, innerAgent, settings, logger, ct);

    private static async IAsyncEnumerable<AgentResponseUpdate> RunGuardedStream(
        IEnumerable<ChatMessage> messages,
        AgentSession? session,
        AgentRunOptions? options,
        AIAgent innerAgent,
        AgentSettings settings,
        ILogger logger,
        [EnumeratorCancellation] CancellationToken ct
    )
    {
        if (InboundInjectionDetected(messages, settings, logger, out var refusal) && refusal is not null)
        {
            yield return new AgentResponseUpdate(ChatRole.Assistant, refusal);
            yield break;
        }

        var accumulated = new System.Text.StringBuilder();
        await foreach (var update in innerAgent.RunStreamingAsync(messages, session, options, cancellationToken: ct))
        {
            accumulated.Append(update.Text);
            yield return update;
        }

        CheckTextModeration(accumulated.ToString(), settings, logger, streaming: true);
    }

    private static bool InboundInjectionDetected(
        IEnumerable<ChatMessage> messages,
        AgentSettings settings,
        ILogger logger,
        out string? refusal
    )
    {
        refusal = null;
        var flagged = messages.Any(m => Sanitize.ContainsInjectionMarkers(m.Text));
        if (!flagged)
        {
            return false;
        }

        RequestContext.SetGuardrailFlag("injection_detected", true);
        if (settings.GuardrailsBlockOnInjection)
        {
            logger.LogWarning("guardrails.injection_blocked blocking=True");
            RequestContext.SetGuardrailFlag("injection_blocked", true);
            refusal = InjectionRefusalMessage;
            return true;
        }

        logger.LogInformation("guardrails.injection_detected blocking=False");
        return false;
    }

    private static AgentResponse CheckOutputModeration(AgentResponse response, AgentSettings settings, ILogger logger)
    {
        if (settings.OutputModerationMode == "off")
        {
            return response;
        }

        var flagged = CheckTextModeration(response.Text, settings, logger, streaming: false);
        if (!flagged || settings.OutputModerationMode != "enforce")
        {
            return response;
        }

        return new AgentResponse(new ChatMessage(ChatRole.Assistant, ModerationRefusalMessage));
    }

    private static bool CheckTextModeration(string? text, AgentSettings settings, ILogger logger, bool streaming)
    {
        if (settings.OutputModerationMode == "off" || string.IsNullOrEmpty(text))
        {
            return false;
        }

        var categories = Moderation.Classify(text);
        if (categories.Count == 0)
        {
            return false;
        }

        RequestContext.SetGuardrailFlag("output_moderation_flagged", true);
        logger.LogWarning(
            "guardrails.output_moderation_flagged categories={Categories} mode={Mode} streaming={Streaming}",
            string.Join(",", categories.Select(c => c.ToString())),
            settings.OutputModerationMode,
            streaming
        );
        return true;
    }

    /// <summary>Marker type purely so <see cref="WrapAgentRun"/> can resolve a category-scoped logger.</summary>
    private sealed class AgentRunLoggerAdapter { }

    /// <summary>Marker type purely so the guardrail gate can resolve its own category-scoped logger.</summary>
    private sealed class GuardrailGateAdapter { }
}
