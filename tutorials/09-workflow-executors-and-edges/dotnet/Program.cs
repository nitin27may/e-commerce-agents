// MAF v1 — Chapter 09: Workflow Executors and Edges (.NET)
//
// NOTE: The .NET Workflows API uses a source-generator-driven Executor
// pattern (partial classes + [MessageHandler] attributes). That source
// generator requires a specific NuGet + MSBuild setup that is involved
// to configure standalone. This chapter demonstrates the *concept* with a
// minimum-viable scaffold, and points at the capstone's Phase 7 refactor
// (`plans/refactor/08-pre-purchase-concurrent.md`) for the full, working
// .NET workflow example with source-generator plumbing.
//
// The Python side of this chapter is the canonical working example —
// see tutorials/09-workflow-executors-and-edges/python/main.py for a
// fully runnable 3-executor pipeline with conditional short-circuit.

namespace MafV1.Ch09.WorkflowDemo;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 09 — Workflow Executors and Edges");
        Console.WriteLine();
        Console.WriteLine("The .NET Workflows API is demonstrated end-to-end in the capstone");
        Console.WriteLine("(see ../../plans/refactor/08-pre-purchase-concurrent.md).");
        Console.WriteLine();
        Console.WriteLine("For a runnable workflow example today, use the Python version:");
        Console.WriteLine("  python tutorials/09-workflow-executors-and-edges/python/main.py 'hello'");
        Console.WriteLine();
        Console.WriteLine("API surface (reference):");
        Console.WriteLine("  using Microsoft.Agents.AI.Workflows;");
        Console.WriteLine("  partial class MyExecutor() : Executor(\"id\") {");
        Console.WriteLine("      [MessageHandler]");
        Console.WriteLine("      public ValueTask RunAsync(string input, IWorkflowContext ctx) => ...;");
        Console.WriteLine("  }");
        Console.WriteLine("  var workflow = new WorkflowBuilder(start).AddEdge(a, b).Build();");
    }
}
