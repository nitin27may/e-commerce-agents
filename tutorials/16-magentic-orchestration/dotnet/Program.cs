// MAF v1 — Chapter 16: Magentic Orchestration (.NET)
//
// Reference: MagenticBuilder in .NET builds a workflow where a manager
// agent plans tasks, maintains a task ledger, and delegates to workers.
// Python is the canonical runnable example.

namespace MafV1.Ch16.Magentic;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 16 — Magentic Orchestration");
        Console.WriteLine();
        Console.WriteLine("Python (runnable): python tutorials/16-magentic-orchestration/python/main.py");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  var manager = new StandardMagenticManager(managerAgent) {");
        Console.WriteLine("      MaxRoundCount = 6, MaxStallCount = 2,");
        Console.WriteLine("  };");
        Console.WriteLine();
        Console.WriteLine("  var workflow = new MagenticBuilder {");
        Console.WriteLine("      Participants = new[] { researcher, marketer, legal },");
        Console.WriteLine("      Manager = manager,");
        Console.WriteLine("  }.Build();");
        Console.WriteLine();
        Console.WriteLine("  await foreach (var evt in InProcessExecution.StreamAsync(workflow, task)) {");
        Console.WriteLine("      if (evt is MagenticOrchestratorEvent m) Console.WriteLine($\"manager: {m.EventType}\");");
        Console.WriteLine("      else if (evt is GroupChatRequestSentEvent g) Console.WriteLine($\"-> {g.ParticipantName}\");");
        Console.WriteLine("  }");
    }
}
