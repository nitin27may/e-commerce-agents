// MAF v1 — Chapter 10: Workflow Events and Builder (.NET)
//
// The .NET event model mirrors Python: workflows emit WorkflowEvent
// instances during execution, which the caller consumes via
// InProcessExecution.StreamAsync(...). Custom events attach arbitrary
// data payloads the caller can pattern-match on.
//
// Reference scaffold — full runnable example lives in the capstone
// refactor (plans/refactor/08-pre-purchase-concurrent.md). The Python
// chapter is the canonical working implementation for this tutorial.

namespace MafV1.Ch10.Events;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 10 — Workflow Events and Builder");
        Console.WriteLine();
        Console.WriteLine("Python (runnable):");
        Console.WriteLine("  python tutorials/10-workflow-events-and-builder/python/main.py 'hi'");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  // Inside an executor's MessageHandler:");
        Console.WriteLine("  await ctx.EmitEventAsync(new ProgressEvent(\"step\", percent: 50));");
        Console.WriteLine();
        Console.WriteLine("  // Consuming the stream:");
        Console.WriteLine("  await foreach (var evt in InProcessExecution.StreamAsync(workflow, input)) {");
        Console.WriteLine("      switch (evt) {");
        Console.WriteLine("          case ExecutorEvent exe:          // executor lifecycle");
        Console.WriteLine("          case AgentResponseEvent resp:    // agent-in-workflow output");
        Console.WriteLine("          case WorkflowOutputEvent output: // final workflow output");
        Console.WriteLine("          // custom events surface as the concrete type you emitted");
        Console.WriteLine("      }");
        Console.WriteLine("  }");
    }
}
