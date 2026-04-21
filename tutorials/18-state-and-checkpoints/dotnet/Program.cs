// MAF v1 — Chapter 18: State and Checkpoints (.NET)
//
// Two-executor workflow: Accumulator adds to a running total and forwards
// to Finalizer, which yields the total as workflow output. The framework
// checkpoints at the end of each superstep; we persist every snapshot to
// disk via FileSystemJsonCheckpointStore.
//
// After the first end-to-end run, we throw away the run object, build a
// fresh workflow instance, and resume from the second-to-last checkpoint
// — proving that executor state (Accumulator's _total) round-trips
// through the JSON on disk.
//
// Run:
//   cd tutorials/18-state-and-checkpoints/dotnet
//   dotnet run                 # default: seed 10, add 5 -> total 15, resume from checkpoint
//   dotnet run -- 10 5         # explicit seed + add

using Microsoft.Agents.AI.Workflows;
using Microsoft.Agents.AI.Workflows.Checkpointing;

namespace MafV1.Ch18.Checkpoints;

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        int seed = args.Length > 0 && int.TryParse(args[0], out int s) ? s : 10;
        int add = args.Length > 1 && int.TryParse(args[1], out int a) ? a : 5;

        var checkpointDir = new DirectoryInfo(
            Path.Combine(Directory.GetCurrentDirectory(), ".checkpoints"));
        if (checkpointDir.Exists) checkpointDir.Delete(recursive: true);
        checkpointDir.Create();

        // CheckpointManager wraps a backing store + JSON marshaller.
        // FileSystemJsonCheckpointStore writes one JSON file per checkpoint
        // under {dir}/{sessionId}_{checkpointId}.json plus an index.jsonl.
        var store = new FileSystemJsonCheckpointStore(checkpointDir);
        CheckpointManager checkpointManager = CheckpointManager.CreateJson(store);

        string sessionId = Guid.NewGuid().ToString("N");

        // ─── Phase 1: run the workflow end-to-end, capturing every checkpoint ─────
        Console.WriteLine($"Phase 1: seed={seed}, add={add}");
        Workflow workflow1 = BuildWorkflow(seed);
        await using StreamingRun run = await InProcessExecution
            .RunStreamingAsync(workflow1, input: add, checkpointManager, sessionId);

        int? finalOutput = null;
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case SuperStepCompletedEvent step when step.CompletionInfo?.Checkpoint is { } cp:
                    Console.WriteLine($"  superstep complete — checkpoint {cp.CheckpointId[..8]}");
                    break;
                case WorkflowOutputEvent output when output.Data is int value:
                    finalOutput = value;
                    break;
            }
        }
        Console.WriteLine($"Phase 1 result: total = {finalOutput}");
        Console.WriteLine();

        // ─── Phase 2: rehydrate into a fresh workflow from the FIRST checkpoint ──
        // This is the moment that matters: a brand-new process, new Workflow
        // object, new Accumulator instance with _total = seed. Resuming from
        // the checkpoint taken *after superstep 1* must restore _total to
        // (seed + add) and let the Finalizer yield it.
        var checkpoints = (await store.RetrieveIndexAsync(sessionId)).ToList();
        Console.WriteLine($"{checkpoints.Count} checkpoint(s) on disk for session {sessionId[..8]}.");

        if (checkpoints.Count == 0)
        {
            Console.Error.WriteLine("No checkpoints produced — nothing to resume.");
            return 1;
        }

        CheckpointInfo firstCheckpoint = checkpoints[0];
        Console.WriteLine($"Resuming from {firstCheckpoint.CheckpointId[..8]} into a fresh Workflow...");

        // Build a completely new Workflow instance with a fresh Accumulator
        // (seeded the same way — seed is part of the executor's identity,
        // not its checkpointable state).
        Workflow workflow2 = BuildWorkflow(seed);
        await using StreamingRun resumed = await InProcessExecution
            .ResumeStreamingAsync(workflow2, firstCheckpoint, checkpointManager);

        int? replayed = null;
        await foreach (WorkflowEvent evt in resumed.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent output && output.Data is int value)
            {
                replayed = value;
            }
        }
        Console.WriteLine($"Phase 2 result: total = {replayed} (expected {finalOutput})");

        return replayed == finalOutput ? 0 : 2;
    }

    private static Workflow BuildWorkflow(int seed)
    {
        var accumulator = new AccumulatorExecutor(seed);
        var finalizer = new FinalizerExecutor();
        return new WorkflowBuilder(accumulator)
            .AddEdge(accumulator, finalizer)
            .WithOutputFrom(finalizer)
            .Build();
    }
}

// ─────────────── AccumulatorExecutor ───────────────
//
// Receives an integer `amount`, adds it to a seeded running total, and
// forwards the new total to the next executor. State (`_total`) round-trips
// through the checkpoint via QueueStateUpdateAsync / ReadStateAsync.

[SendsMessage(typeof(int))]
internal sealed partial class AccumulatorExecutor : Executor
{
    private const string StateKey = "total";

    private int _total;

    public AccumulatorExecutor(int seed) : base("accumulator")
    {
        _total = seed;
    }

    [MessageHandler]
    public async ValueTask HandleAsync(
        int amount,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        _total += amount;
        await context.SendMessageAsync(_total, cancellationToken: cancellationToken);
    }

    protected override ValueTask OnCheckpointingAsync(
        IWorkflowContext context,
        CancellationToken cancellationToken = default) =>
        context.QueueStateUpdateAsync(StateKey, _total, cancellationToken: cancellationToken);

    protected override async ValueTask OnCheckpointRestoredAsync(
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        _total = await context.ReadStateAsync<int>(StateKey, cancellationToken: cancellationToken);
    }
}

// ─────────────── FinalizerExecutor ───────────────
//
// Yields whatever total it receives as the workflow output. Stateless —
// no checkpoint hooks needed.

[YieldsOutput(typeof(int))]
internal sealed partial class FinalizerExecutor() : Executor("finalizer")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        int total,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        await context.YieldOutputAsync(total, cancellationToken);
        await context.RequestHaltAsync();
    }
}
