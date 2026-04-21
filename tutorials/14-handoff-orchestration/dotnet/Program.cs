// MAF v1 — Chapter 14: Handoff Orchestration (.NET)
//
// Reference: AgentWorkflowBuilder.CreateHandoffBuilderWith(...) + .WithHandoffs
// to wire a mesh topology. Start agent acts as triage and routes to
// specialists via tool calls. Full runnable lives in capstone Phase 7.

namespace MafV1.Ch14.Handoff;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 14 — Handoff Orchestration");
        Console.WriteLine();
        Console.WriteLine("Python (runnable): python tutorials/14-handoff-orchestration/python/main.py");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  var triage  = chatClient.AsAIAgent(instructions: \"You are a Triage agent...\", name: \"triage\");");
        Console.WriteLine("  var math    = chatClient.AsAIAgent(instructions: \"You are a Math expert...\", name: \"math\");");
        Console.WriteLine("  var history = chatClient.AsAIAgent(instructions: \"You are a History expert...\", name: \"history\");");
        Console.WriteLine();
        Console.WriteLine("  var workflow = AgentWorkflowBuilder");
        Console.WriteLine("      .CreateHandoffBuilderWith(triage)");
        Console.WriteLine("      .WithHandoffs(triage, new[] { math, history })");
        Console.WriteLine("      .WithHandoffs(math,   new[] { triage })");
        Console.WriteLine("      .WithHandoffs(history,new[] { triage })");
        Console.WriteLine("      .Build();");
        Console.WriteLine();
        Console.WriteLine("  await foreach (var evt in InProcessExecution.StreamAsync(workflow, question))");
        Console.WriteLine("      if (evt is HandoffEvent h) Console.WriteLine($\"{h.Source} -> {h.Target}\");");
    }
}
