using FluentAssertions;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Agents.AI.Workflows.Checkpointing;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Does MAF .NET round-trip a <see cref="RequestPort"/> pause through a checkpoint?
/// </summary>
/// <remarks>
/// This is a spike, deliberately kept as a test so the answer stays true rather than
/// living in a commit message. It gates issue #33's workflow-resume work: .NET has no
/// <c>POST /api/orchestration/{run_id}/resume</c>, and the plan is to rebuild the paused
/// workflow from a checkpoint the way Python does
/// (<c>orchestrator/modes/workflow_mode.py:299</c>) rather than caching a live
/// <c>StreamingRun</c> in process memory — which is what
/// <c>ReturnAndReplaceWorkflow._pausedRuns</c> does today, and why the pause cannot
/// survive the request that created it.
///
/// The doubt worth resolving before building on it: MAF tracks outstanding external
/// requests in <c>WorkflowSession._pendingRequests</c>, and
/// <c>SendMessagesWithResponseConversionAsync</c> only converts a response into an
/// <c>ExternalResponse</c> "when there's a matching pending request". Nothing in the
/// package documents whether that map is part of a checkpoint. If it is not, a restored
/// run cannot recognise the response and the whole approach collapses.
///
/// So this asserts the strongest form of the claim: run until the port pauses, serialize
/// every checkpoint to JSON text, throw away the store, the manager and the workflow
/// object, rebuild all three from that text alone, and only then deliver the response.
/// Nothing but the serialized bytes crosses the boundary — which is exactly what a
/// Postgres-backed store gives you across a process restart.
///
/// <b>Result: it works.</b> The restored run re-raises <c>RequestInfoEvent</c> carrying the
/// same <c>RequestId</c>, and the graph continues once the response is delivered. So .NET
/// can resume from durable storage exactly as Python does, and the paused run does not
/// have to be held in process memory. Two details this cost time to find, both worth
/// keeping: an executor must declare every message type it sends with
/// <c>[SendsMessage]</c> or the run fails at dispatch with "cannot send messages of type",
/// and the checkpoint to resume from is the one on the <c>SuperStepCompletedEvent</c> whose
/// <c>CompletionInfo.HasPendingRequests</c> is true — which arrives *after* the
/// <c>RequestInfoEvent</c>, not with it.
/// </remarks>
public sealed class MafCheckpointResumeSpikeTests
{
    [Fact]
    public async Task APausedRequestPort_SurvivesACheckpointRoundTrip_AndResumes()
    {
        var store = new JsonTextCheckpointStore();
        var manager = CheckpointManager.CreateJson(store, new JsonSerializerOptions());
        const string sessionId = "spike-session";

        // ── first process: run until the port pauses ──
        CheckpointInfo? pausedAt = null;
        string? requestId = null;

        var run = await InProcessExecution.RunStreamingAsync(
            BuildWorkflow(), "start", manager, sessionId);

        await foreach (var evt in run.WatchStreamAsync())
        {
            if (evt is RequestInfoEvent request)
            {
                requestId = request.Request.RequestId;
            }
            if (evt is SuperStepCompletedEvent step && step.CompletionInfo?.HasPendingRequests == true)
            {
                pausedAt = step.CompletionInfo.Checkpoint;
                break;
            }
        }

        requestId.Should().NotBeNull("the port must have raised a request before it paused");
        pausedAt.Should().NotBeNull(
            "a checkpoint taken while a request is outstanding is the only thing a later process can resume from");

        // Simulate the process ending mid-approval: the live run is gone.
        await run.DisposeAsync();

        // ── second process: nothing survives but the serialized bytes ──
        var wireFormat = store.Serialize();
        var revivedStore = JsonTextCheckpointStore.Deserialize(wireFormat);
        var revivedManager = CheckpointManager.CreateJson(revivedStore, new JsonSerializerOptions());

        var resumed = await InProcessExecution.ResumeStreamingAsync(
            BuildWorkflow(), pausedAt!, revivedManager);

        // The response cannot be hand-built: ExternalResponse is created *from* the
        // ExternalRequest. So the restored run has to hand the pending request back —
        // which is the whole question. If it does, the pending-request map is genuinely
        // part of the checkpoint and resume-from-storage works.
        ExternalRequest? rehydrated = null;
        await foreach (var evt in resumed.WatchStreamAsync())
        {
            if (evt is RequestInfoEvent request)
            {
                rehydrated = request.Request;
                break;
            }
        }

        rehydrated.Should().NotBeNull(
            "a run restored from a checkpoint must re-surface the request it was waiting on");
        rehydrated!.RequestId.Should().Be(requestId,
            "the same request must come back, or a response could not be correlated to it");

        await resumed.SendResponseAsync(rehydrated.CreateResponse(true));

        var outputs = new List<string>();
        await foreach (var evt in resumed.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent { Data: string text })
            {
                outputs.Add(text);
                if (text.StartsWith("done:", StringComparison.Ordinal))
                {
                    break;
                }
            }
        }

        outputs.Should().Contain(o => o.StartsWith("done:", StringComparison.Ordinal),
            "the graph must continue past the port once the response is delivered in the new process");
        await resumed.DisposeAsync();
    }

    /// <summary>
    /// Minimal gate → port → continue graph. Deliberately not the real
    /// ReturnAndReplaceWorkflow: this isolates the MAF question from that workflow's own
    /// state handling, so a failure here is unambiguously the framework's behaviour.
    /// </summary>
    private static Workflow BuildWorkflow()
    {
        var port = RequestPort.Create<string, bool>("Approval");
        var start = new AskExecutor(port.Id);
        var finish = new FinishExecutor();

        return new WorkflowBuilder(start)
            .AddEdge(start, port)
            .AddEdge(port, finish)
            .WithOutputFrom(new ExecutorBinding[] { finish })
            .Build();
    }

    [SendsMessage(typeof(string))]
    private sealed class AskExecutor(string portId) : Executor<string>("ask")
    {
        public override async ValueTask HandleAsync(string message, IWorkflowContext context, CancellationToken ct = default)
            => await context.SendMessageAsync("approve?", portId, ct);
    }

    [YieldsOutput(typeof(string))]
    private sealed class FinishExecutor() : Executor<bool>("finish")
    {
        public override async ValueTask HandleAsync(bool approved, IWorkflowContext context, CancellationToken ct = default)
            => await context.YieldOutputAsync($"done:{approved}", ct);
    }

    /// <summary>
    /// An <see cref="ICheckpointStore{T}"/> whose entire contents can be reduced to a
    /// string and rebuilt from it — standing in for the Postgres-backed store, and the
    /// reason this spike proves something a purely in-memory manager could not.
    /// </summary>
    private sealed class JsonTextCheckpointStore : ICheckpointStore<JsonElement>
    {
        private readonly Dictionary<string, Dictionary<string, string>> _bySession = [];
        private readonly Dictionary<string, List<string>> _index = [];

        public string Serialize() => JsonSerializer.Serialize(new Wire(_bySession, _index));

        public static JsonTextCheckpointStore Deserialize(string json)
        {
            var wire = JsonSerializer.Deserialize<Wire>(json)!;
            var store = new JsonTextCheckpointStore();
            foreach (var (session, checkpoints) in wire.Checkpoints)
            {
                store._bySession[session] = new Dictionary<string, string>(checkpoints);
            }
            foreach (var (session, ids) in wire.Index)
            {
                store._index[session] = [.. ids];
            }
            return store;
        }

        public ValueTask<CheckpointInfo> CreateCheckpointAsync(string sessionId, JsonElement value, CheckpointInfo? parent = null)
        {
            var id = Guid.NewGuid().ToString("N");
            if (!_bySession.TryGetValue(sessionId, out var checkpoints))
            {
                _bySession[sessionId] = checkpoints = [];
                _index[sessionId] = [];
            }
            checkpoints[id] = value.GetRawText();
            _index[sessionId].Add(id);
            return new ValueTask<CheckpointInfo>(new CheckpointInfo(sessionId, id));
        }

        public ValueTask<JsonElement> RetrieveCheckpointAsync(string sessionId, CheckpointInfo key)
        {
            var raw = _bySession[sessionId][key.CheckpointId];
            return new ValueTask<JsonElement>(JsonDocument.Parse(raw).RootElement.Clone());
        }

        public ValueTask<IEnumerable<CheckpointInfo>> RetrieveIndexAsync(string sessionId, CheckpointInfo? withParent = null)
        {
            var ids = _index.TryGetValue(sessionId, out var known) ? known : [];
            return new ValueTask<IEnumerable<CheckpointInfo>>(
                ids.Select(id => new CheckpointInfo(sessionId, id)).ToList());
        }

        private sealed record Wire(
            Dictionary<string, Dictionary<string, string>> Checkpoints,
            Dictionary<string, List<string>> Index
        );
    }
}
