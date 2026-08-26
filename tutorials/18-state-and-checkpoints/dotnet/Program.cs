// MAF v1 — Chapter 18: State and Checkpoints (.NET)
//
// Two-executor workflow: ReturnRequestExecutor accumulates a refund amount
// as return line items get processed and forwards it to
// FinalizeReturnExecutor, which yields the refund total as workflow
// output. The framework checkpoints at the end of each superstep; we
// persist every snapshot to disk via FileSystemJsonCheckpointStore.
//
// After the first end-to-end run, we throw away the run object, build a
// fresh workflow instance, and resume from the second-to-last checkpoint
// — proving that executor state (ReturnRequestExecutor's _refundAmount)
// round-trips through the JSON on disk.
//
// This is a small approximation of the production
// `workflow:return-replace` chain (agents/python/workflows/return_replace.py)
// — that workflow carries a much larger WorkflowState through six
// HITL-gated steps. This chapter only teaches the checkpoint
// save/restore mechanic itself, at toy scale.
//
// Run:
//   cd tutorials/18-state-and-checkpoints/dotnet
//   dotnet run                     # default: initial 10, item 5 -> refund 15, resume from checkpoint
//   dotnet run -- 10 5              # explicit initial refund + item refund

using Microsoft.Agents.AI.Workflows;
using Microsoft.Agents.AI.Workflows.Checkpointing;

namespace MafV1.Ch18.Checkpoints;

/// <summary>The result of running once and then resuming from a checkpoint.</summary>
/// <param name="FirstRun">Output of the original run.</param>
/// <param name="Resumed">Output of the run rebuilt from disk. Equal, if state round-tripped.</param>
/// <param name="CheckpointIds">Checkpoint ids written for the session, in order.</param>
internal sealed record CheckpointRoundTrip(
    double? FirstRun,
    double? Resumed,
    IReadOnlyList<string> CheckpointIds);

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        double initialRefund = args.Length > 0 && double.TryParse(args[0], out double ir) ? ir : 10.0;
        double itemRefund = args.Length > 1 && double.TryParse(args[1], out double itr) ? itr : 5.0;

        var checkpointDir = new DirectoryInfo(
            Path.Combine(Directory.GetCurrentDirectory(), ".checkpoints"));
        if (checkpointDir.Exists) checkpointDir.Delete(recursive: true);
        checkpointDir.Create();

        Console.WriteLine($"Phase 1: initialRefund={initialRefund}, itemRefund={itemRefund}");

        CheckpointRoundTrip result = await RunAsync(checkpointDir, initialRefund, itemRefund);

        foreach (string id in result.CheckpointIds)
        {
            Console.WriteLine($"  superstep complete — checkpoint {id[..8]}");
        }

        Console.WriteLine($"Phase 1 result: refund_amount = {result.FirstRun}");
        Console.WriteLine();
        Console.WriteLine($"{result.CheckpointIds.Count} checkpoint(s) on disk.");

        if (result.CheckpointIds.Count == 0)
        {
            Console.Error.WriteLine("No checkpoints produced — nothing to resume.");
            return 1;
        }

        Console.WriteLine($"Resuming from {result.CheckpointIds[0][..8]} into a fresh Workflow...");
        Console.WriteLine($"Phase 2 result: refund_amount = {result.Resumed} (expected {result.FirstRun})");

        return result.Resumed == result.FirstRun ? 0 : 2;
    }

    /// <summary>
    /// Runs the workflow end to end, then rebuilds it from scratch and resumes
    /// it from the first checkpoint on disk.
    /// </summary>
    /// <remarks>
    /// The moment that matters is phase 2: a brand-new Workflow object and a
    /// brand-new ReturnRequestExecutor whose _refundAmount is back to
    /// <paramref name="initialRefund"/>. If OnCheckpointRestoredAsync does its
    /// job, the resumed run still produces initialRefund + itemRefund — and if
    /// it silently does nothing, the resumed run produces initialRefund alone.
    /// Both are plausible-looking numbers, which is why this needs a test
    /// rather than an eyeball.
    /// </remarks>
    internal static async Task<CheckpointRoundTrip> RunAsync(
        DirectoryInfo checkpointDir,
        double initialRefund,
        double itemRefund)
    {
        // CheckpointManager wraps a backing store + JSON marshaller.
        // FileSystemJsonCheckpointStore writes one JSON file per checkpoint
        // under {dir}/{sessionId}_{checkpointId}.json plus an index.jsonl.
        //
        // Gotcha: the store takes an EXCLUSIVE lock on the directory, and the
        // lock outlives the run — constructing a second store over the same
        // directory throws "already in use by another process" even from the
        // same process, even after the first run has finished. So a directory
        // is effectively one-store-for-the-lifetime-of-the-process. Give each
        // run its own directory, or hold one store and reuse it across
        // sessions. The exception message names a *process*, which sends
        // people looking for a stale lockfile that does not exist.
        var store = new FileSystemJsonCheckpointStore(checkpointDir);
        CheckpointManager checkpointManager = CheckpointManager.CreateJson(store);

        string sessionId = Guid.NewGuid().ToString("N");

        // ─── Phase 1: run end-to-end, capturing every checkpoint ─────────────
        Workflow workflow1 = BuildWorkflow(initialRefund);
        await using StreamingRun run = await InProcessExecution
            .RunStreamingAsync(workflow1, input: itemRefund, checkpointManager, sessionId);

        double? finalOutput = null;
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent output && output.Data is double value)
            {
                finalOutput = value;
            }
        }

        // ─── Phase 2: rehydrate into a fresh workflow from the FIRST checkpoint
        var checkpoints = (await store.RetrieveIndexAsync(sessionId)).ToList();
        if (checkpoints.Count == 0)
        {
            return new CheckpointRoundTrip(finalOutput, null, Array.Empty<string>());
        }

        CheckpointInfo firstCheckpoint = checkpoints[0];

        // A completely new Workflow instance with a fresh
        // ReturnRequestExecutor (seeded the same way — the initial refund
        // is part of the executor's identity, not its checkpointable state).
        Workflow workflow2 = BuildWorkflow(initialRefund);
        await using StreamingRun resumed = await InProcessExecution
            .ResumeStreamingAsync(workflow2, firstCheckpoint, checkpointManager);

        double? replayed = null;
        await foreach (WorkflowEvent evt in resumed.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent output && output.Data is double value)
            {
                replayed = value;
            }
        }

        return new CheckpointRoundTrip(
            finalOutput,
            replayed,
            checkpoints.Select(c => c.CheckpointId).ToList());
    }

    internal static Workflow BuildWorkflow(double initialRefund)
    {
        var returnRequest = new ReturnRequestExecutor(initialRefund);
        var finalizeReturn = new FinalizeReturnExecutor();
        return new WorkflowBuilder(returnRequest)
            .AddEdge(returnRequest, finalizeReturn)
            .WithOutputFrom(finalizeReturn)
            .Build();
    }
}

// ─────────────── ReturnRequestExecutor ───────────────
//
// Receives a `itemRefund` amount, adds it to a running refund total
// seeded at construction, and forwards the new total to the next
// executor. State (`_refundAmount`) round-trips through the checkpoint
// via QueueStateUpdateAsync / ReadStateAsync.

[SendsMessage(typeof(double))]
internal sealed partial class ReturnRequestExecutor : Executor
{
    private const string StateKey = "refund_amount";

    private double _refundAmount;

    public ReturnRequestExecutor(double initialRefund) : base("return-request")
    {
        _refundAmount = initialRefund;
    }

    [MessageHandler]
    public async ValueTask HandleAsync(
        double itemRefund,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        _refundAmount += itemRefund;
        await context.SendMessageAsync(_refundAmount, cancellationToken: cancellationToken);
    }

    protected override ValueTask OnCheckpointingAsync(
        IWorkflowContext context,
        CancellationToken cancellationToken = default) =>
        context.QueueStateUpdateAsync(StateKey, _refundAmount, cancellationToken: cancellationToken);

    protected override async ValueTask OnCheckpointRestoredAsync(
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        _refundAmount = await context.ReadStateAsync<double>(StateKey, cancellationToken: cancellationToken);
    }
}

// ─────────────── FinalizeReturnExecutor ───────────────
//
// Yields whatever refund total it receives as the workflow output.
// Stateless — no checkpoint hooks needed.

[YieldsOutput(typeof(double))]
internal sealed partial class FinalizeReturnExecutor() : Executor("finalize-return")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        double refundAmount,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        await context.YieldOutputAsync(refundAmount, cancellationToken);
        await context.RequestHaltAsync();
    }
}
