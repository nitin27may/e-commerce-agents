// MAF v1 — Chapter 11: Agents in Workflows (.NET)
//
// Reference scaffold. The .NET workflow API wraps AIAgent instances with
// AIAgentHostExecutor so they participate in the workflow graph. The full
// runnable chain lives in the capstone (Phase 7 refactor). This chapter's
// Python implementation is the canonical working example.

namespace MafV1.Ch11.AgentsInWorkflows;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 11 — Agents in Workflows");
        Console.WriteLine();
        Console.WriteLine("Python (runnable): python tutorials/11-agents-in-workflows/python/main.py");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  // Wrap an AIAgent as an Executor — AgentWorkflowBuilder exposes");
        Console.WriteLine("  // BuildSequential / BuildConcurrent that do this for you.");
        Console.WriteLine("  var workflow = AgentWorkflowBuilder.BuildSequential(new[] {");
        Console.WriteLine("      chatClient.AsAIAgent(instructions: \"Translate to French\"),");
        Console.WriteLine("      chatClient.AsAIAgent(instructions: \"Translate to Spanish\"),");
        Console.WriteLine("  });");
        Console.WriteLine();
        Console.WriteLine("  await foreach (var evt in InProcessExecution.StreamAsync(workflow, input))");
        Console.WriteLine("      if (evt is WorkflowOutputEvent o && o.Data is AgentResponse r) Console.WriteLine(r.Text);");
    }
}
