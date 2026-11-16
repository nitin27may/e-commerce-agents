using System.Text.Json;
using A2A;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using AmbientContext = ECommerceAgents.Shared.Context.RequestContext;
using ECommerceAgents.Shared.Logging;
using ECommerceAgents.Shared.Telemetry;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.A2A.Protocol;

/// <summary>
/// Runs one specialist turn for an A2A task. Twin of Python's <c>SpecialistExecutor</c>, and custom for the
/// same reason: it feeds the message text and the rehydrated session history through the very
/// <see cref="AgentHost.RunAgentWithHistoryStreamingAsync"/> the legacy <c>/message:stream</c> route uses, so
/// what the specialist's model sees is identical on both wires (replay fixtures are keyed on it).
/// </summary>
/// <remarks>
/// Mapping from the legacy contract: <c>X-Session-Id</c> becomes the message's <c>contextId</c> (the header
/// is still read as a fallback); the answer is one artifact whose first chunk is <c>append=false</c>, later
/// ones <c>append=true</c> and the last <c>last_chunk=true</c>; steps, usage and a replay miss ride in status
/// metadata under the three extension URIs, sent only to a caller that listed them in
/// <c>A2A-Extensions</c>. Lifecycle: submitted, working, completed or failed. <c>input-required</c> is
/// reserved for the cross-agent approval work and never produced.
/// </remarks>
public sealed class SpecialistA2AExecutor(
    IServiceProvider services,
    string agentName,
    ActiveTaskLimiter limiter,
    OwnedBoundedTaskStore store,
    ILogger<SpecialistA2AExecutor> logger) : IAgentHandler
{
    public async Task ExecuteAsync(RequestContext context, AgentEventQueue eventQueue, CancellationToken cancellationToken)
    {
        var caller = AmbientContext.CurrentUserEmail;
        var taskId = context.TaskId;
        var contextId = context.ContextId;
        var updater = new TaskUpdater(eventQueue, taskId, contextId);

        if (!limiter.TryAcquire(caller))
        {
            // Past the server's pre-check (a race with another task starting): refuse, never queue.
            logger.LogWarning("a2a.task_refused agent={Agent}", LogSafe.Clean(agentName));
            await EnsureTaskAsync(context, eventQueue, cancellationToken);
            await updater.RejectAsync(AgentMessage(context, A2AWire.TooManyTasksText), null, cancellationToken);
            return;
        }

        try
        {
            await RunAsync(context, eventQueue, updater, cancellationToken);
        }
        finally
        {
            limiter.Release(caller);
        }
    }

    public async Task CancelAsync(RequestContext context, AgentEventQueue eventQueue, CancellationToken cancellationToken)
    {
        await new TaskUpdater(eventQueue, context.TaskId, context.ContextId).CancelAsync(null, cancellationToken);
    }

    private async Task RunAsync(RequestContext context, AgentEventQueue eventQueue, TaskUpdater updater, CancellationToken ct)
    {
        var wants = context.ExtensionActivation?.ActivatedExtensions ?? new HashSet<string>();
        await EnsureTaskAsync(context, eventQueue, ct);

        var text = context.UserText;
        if (string.IsNullOrWhiteSpace(text))
        {
            await updater.RejectAsync(AgentMessage(context, A2AWire.NoMessageText), null, ct);
            return;
        }

        var sessionId = A2AWire.SessionIdFromContext(
            context.ContextId, context.ClientProvidedContextId, AmbientContext.CurrentSessionId);
        var model = services.GetService<AgentSettings>()?.LlmModel ?? string.Empty;

        // A fresh step list, usage tally and spend tally for this turn, under the identity the auth
        // middleware stamped. The session id is the message's contextId, not the header's.
        using var scope = AmbientContext.Scope(
            AmbientContext.CurrentUserEmail, AmbientContext.CurrentUserRole, sessionId);
        using var span = TelemetrySetup.AgentRunSpan(agentName, model);

        var sentSteps = 0;

        Dictionary<string, JsonElement>? StepUpdate(bool final)
        {
            if (!wants.Contains(A2AWire.StepsExtensionUri))
            {
                return null;
            }

            var steps = AmbientContext.CurrentSteps;
            var start = final ? 0 : sentSteps;
            var batch = steps.Skip(start).Select(s => A2AWire.WireStep.From(s, agentName)).ToList();
            sentSteps = steps.Count;
            return batch.Count == 0
                ? null
                : A2AWire.Metadata((A2AWire.StepsExtensionUri, A2AWire.StepsPayload(batch, start)));
        }

        Dictionary<string, JsonElement>? TerminalMetadata(Dictionary<string, JsonElement>? extra = null)
        {
            var metadata = StepUpdate(final: true) ?? new Dictionary<string, JsonElement>(StringComparer.Ordinal);
            if (wants.Contains(A2AWire.UsageExtensionUri) && AmbientContext.CurrentRunUsage.Total() is { } usage)
            {
                metadata[A2AWire.UsageExtensionUri] = A2AWire.ToElement(usage);
            }

            if (extra is not null)
            {
                foreach (var (key, value) in extra)
                {
                    metadata[key] = value;
                }
            }

            return metadata.Count == 0 ? null : metadata;
        }

        // Published to the store before the event that carries it is enqueued, so the task the server
        // saves from that event (and a blocking SendMessage returns) already holds it.
        Dictionary<string, JsonElement>? Publish(Dictionary<string, JsonElement>? metadata)
        {
            store.MergeMetadata(context.TaskId, metadata);
            return metadata;
        }

        await updater.StartWorkAsync(null, null, ct);

        var artifactId = Guid.NewGuid().ToString("N");
        var wroteAny = false;
        string? pending = null;

        async Task FlushAsync(bool last)
        {
            if (pending is null)
            {
                return;
            }

            await updater.AddArtifactAsync(
                [Part.FromText(pending)], artifactId, "answer", null, lastChunk: last, append: wroteAny, null, ct);
            wroteAny = true;
            pending = null;
        }

        try
        {
            await foreach (var chunk in AgentHost.RunAgentWithHistoryStreamingAsync(services, text, ct))
            {
                await FlushAsync(last: false);

                // Steps completed since the previous chunk go out after the text before them and ahead
                // of the text that follows: the order the legacy stream reports them in.
                if (Publish(StepUpdate(final: false)) is { } live)
                {
                    await updater.StartWorkAsync(null, live, ct);
                }

                pending = chunk;
            }

            await FlushAsync(last: true);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            // CancelTask (or a dropped blocking caller): the cancel handler records the Canceled state.
            return;
        }
        catch (Exception ex) when (FindReplayMiss(ex))
        {
            // Replay mode only: a missing recording, not a failure of the agent.
            logger.LogWarning("a2a.replay_miss agent={Agent}", LogSafe.Clean(agentName));
            await FlushAsync(last: true);
            var miss = wants.Contains(A2AWire.ReplayMissExtensionUri)
                ? A2AWire.Metadata((A2AWire.ReplayMissExtensionUri, A2AWire.ToElement(new { error = A2AWire.ReplayMissError })))
                : null;
            await updater.FailAsync(AgentMessage(context, A2AWire.ReplayMissError), Publish(TerminalMetadata(miss)), ct);
            return;
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "a2a.execute_error agent={Agent}", LogSafe.Clean(agentName));
            await FlushAsync(last: true);
            await updater.FailAsync(AgentMessage(context, A2AWire.FailureText), Publish(TerminalMetadata()), ct);
            return;
        }

        await updater.CompleteAsync(null, Publish(TerminalMetadata()), ct);
    }

    /// <summary>The submitted task a status update needs to exist first (the SDK drops updates for an unknown task).</summary>
    private static async Task EnsureTaskAsync(RequestContext context, AgentEventQueue eventQueue, CancellationToken ct)
    {
        if (context.Task is not null)
        {
            return;
        }

        await eventQueue.EnqueueTaskAsync(
            new AgentTask
            {
                Id = context.TaskId,
                ContextId = context.ContextId,
                Status = new global::A2A.TaskStatus { State = TaskState.Submitted, Timestamp = DateTimeOffset.UtcNow },
                History = context.Message is null ? null : [context.Message],
            },
            ct);
    }

    private static Message AgentMessage(RequestContext context, string text) => new()
    {
        Role = Role.Agent,
        MessageId = Guid.NewGuid().ToString("N"),
        TaskId = context.TaskId,
        ContextId = context.ContextId,
        Parts = [Part.FromText(text)],
    };

    private static bool FindReplayMiss(Exception ex)
    {
        for (var current = ex; current is not null; current = current.InnerException)
        {
            if (current is ReplayChatClient.FixtureMissingException)
            {
                return true;
            }
        }

        return false;
    }
}
