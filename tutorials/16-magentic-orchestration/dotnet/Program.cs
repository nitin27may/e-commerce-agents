// MAF v1 — Chapter 16: Magentic Orchestration (.NET status stub)
//
// Microsoft's docs used to say:
//
//   "Magentic Orchestration is not yet supported in C#."
//
// That stopped being true: Microsoft.Agents.AI.Workflows 1.28.0 exports
// MagenticWorkflowBuilder plus MagenticPlanCreatedEvent / MagenticReplannedEvent /
// MagenticProgressLedgerUpdatedEvent and friends (the Python names
// MagenticBuilder / StandardMagenticManager still do not exist in .NET).
// This chapter has not been ported yet; the file below is a placeholder that
// says so, so the project stays runnable (`dotnet build`, `dotnet run`).
// Replace it with a real sample built on MagenticWorkflowBuilder.

namespace MafV1.Ch16.Magentic;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 16 — Magentic Orchestration");
        Console.WriteLine();
        Console.WriteLine("Magentic orchestration now exists in .NET (MagenticWorkflowBuilder in");
        Console.WriteLine("Microsoft.Agents.AI.Workflows 1.28), but this chapter's C# sample is not yet ported.");
        Console.WriteLine();
        Console.WriteLine("Run the Python reference implementation instead:");
        Console.WriteLine();
        Console.WriteLine("    python tutorials/16-magentic-orchestration/python/main.py \\");
        Console.WriteLine("        \"plan a short launch brief for an AI meal planner\"");
        Console.WriteLine();
        Console.WriteLine("The chapter README walks the Python manager loop end-to-end — facts");
        Console.WriteLine("ledger, plan, progress ledger, stall detection, reset — and explains");
        Console.WriteLine("the manager loop the .NET MagenticWorkflowBuilder is expected to mirror.");
    }
}
