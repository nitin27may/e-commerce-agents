// MAF v1 — Chapter 18: State and Checkpoints (.NET)
//
// Reference: FileCheckpointStorage or InMemoryCheckpointStorage provide
// durable/ephemeral checkpoint backends. Executors implement
// IResettableExecutor (or the OnCheckpointSaveAsync/RestoreAsync pair)
// to snapshot and rehydrate state.

namespace MafV1.Ch18.Checkpoints;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 18 — State and Checkpoints");
        Console.WriteLine();
        Console.WriteLine("Python (runnable): python tutorials/18-state-and-checkpoints/python/main.py 3");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  var storage = new FileCheckpointStorage(\"./.checkpoints\");");
        Console.WriteLine("  var workflow = new WorkflowBuilder(counter)");
        Console.WriteLine("      .WithName(\"counter-workflow\")");
        Console.WriteLine("      .WithCheckpointStorage(storage)");
        Console.WriteLine("      .Build();");
        Console.WriteLine();
        Console.WriteLine("  partial class CounterExecutor() : Executor(\"counter\") {");
        Console.WriteLine("      int total;");
        Console.WriteLine("      [MessageHandler]");
        Console.WriteLine("      public ValueTask IncrementAsync(int amount, IWorkflowContext ctx) {");
        Console.WriteLine("          total += amount;");
        Console.WriteLine("          return ctx.YieldOutputAsync(total);");
        Console.WriteLine("      }");
        Console.WriteLine("      protected override ValueTask<object?> OnCheckpointSaveAsync(CancellationToken ct) =>");
        Console.WriteLine("          ValueTask.FromResult<object?>(new { total });");
        Console.WriteLine("      protected override ValueTask OnCheckpointRestoreAsync(object? state, CancellationToken ct) { ... }");
        Console.WriteLine("  }");
        Console.WriteLine();
        Console.WriteLine("  var latest = await storage.GetLatestAsync(workflowName: \"counter-workflow\");");
        Console.WriteLine("  await foreach (var evt in InProcessExecution.StreamAsync(workflow, checkpointId: latest.Id)) { ... }");
    }
}
