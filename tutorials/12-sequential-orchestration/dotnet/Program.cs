// MAF v1 — Chapter 12: Sequential Orchestration (.NET)
//
// Reference: AgentWorkflowBuilder.BuildSequential(agents) returns a Workflow
// that chains agents in order. Each agent sees the full conversation so far.
//
// Full runnable .NET orchestration lives in the capstone Phase 7 refactor
// (see plans/refactor/09-return-replace-sequential-hitl.md). The Python
// chapter is the canonical working example for this tutorial.

namespace MafV1.Ch12.Sequential;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 12 — Sequential Orchestration");
        Console.WriteLine();
        Console.WriteLine("Python (runnable): python tutorials/12-sequential-orchestration/python/main.py");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  var writer    = chatClient.AsAIAgent(instructions: \"You are a Writer...\");");
        Console.WriteLine("  var reviewer  = chatClient.AsAIAgent(instructions: \"You are a Reviewer...\");");
        Console.WriteLine("  var finalizer = chatClient.AsAIAgent(instructions: \"You are a Finalizer...\");");
        Console.WriteLine();
        Console.WriteLine("  var workflow = AgentWorkflowBuilder.BuildSequential(new[] {");
        Console.WriteLine("      writer, reviewer, finalizer");
        Console.WriteLine("  });");
        Console.WriteLine();
        Console.WriteLine("  await foreach (var evt in InProcessExecution.StreamAsync(workflow, \"Why sleep matters\"))");
        Console.WriteLine("      if (evt is AgentResponseEvent r) Console.WriteLine($\"{r.ExecutorId}: {r.Response.Text}\");");
    }
}
