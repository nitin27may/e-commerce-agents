// MAF v1 — Chapter 13: Concurrent Orchestration (.NET)
//
// Reference: AgentWorkflowBuilder.BuildConcurrent(agents) — fires every
// agent in parallel on the same input, aggregates their AgentResponses.
// Full runnable lives in capstone Phase 7 (pre-purchase refactor).

namespace MafV1.Ch13.Concurrent;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 13 — Concurrent Orchestration");
        Console.WriteLine();
        Console.WriteLine("Python (runnable): python tutorials/13-concurrent-orchestration/python/main.py");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  var researcher = chatClient.AsAIAgent(instructions: \"You are a Market Researcher...\");");
        Console.WriteLine("  var marketer   = chatClient.AsAIAgent(instructions: \"You are a Marketer...\");");
        Console.WriteLine("  var legal      = chatClient.AsAIAgent(instructions: \"You are a Legal advisor...\");");
        Console.WriteLine();
        Console.WriteLine("  var workflow = AgentWorkflowBuilder.BuildConcurrent(new[] {");
        Console.WriteLine("      researcher, marketer, legal");
        Console.WriteLine("  });");
        Console.WriteLine();
        Console.WriteLine("  await foreach (var evt in InProcessExecution.StreamAsync(workflow, idea))");
        Console.WriteLine("      if (evt is AgentResponseEvent r) Console.WriteLine($\"{r.ExecutorId}: {r.Response.Text}\");");
    }
}
