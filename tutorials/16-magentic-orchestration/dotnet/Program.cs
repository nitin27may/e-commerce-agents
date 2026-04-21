// MAF v1 — Chapter 16: Magentic Orchestration (.NET status stub)
//
// Microsoft's official docs state:
//
//   "Magentic Orchestration is not yet supported in C#."
//   https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic?pivots=programming-language-csharp
//
// Confirmed against Microsoft.Agents.AI.Workflows 1.1.0 — there is no
// MagenticBuilder / StandardMagenticManager symbol in the .NET assembly.
// Any .NET sample that claims otherwise is invented API.
//
// This file is a placeholder that prints the current status so the chapter
// dotnet project stays runnable (`dotnet build`, `dotnet run`). Swap it
// for a real sample once the types land in a future package version.

namespace MafV1.Ch16.Magentic;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 16 — Magentic Orchestration");
        Console.WriteLine();
        Console.WriteLine("Magentic orchestration is Python-only in Microsoft Agent Framework v1.1.");
        Console.WriteLine("From the official docs:");
        Console.WriteLine();
        Console.WriteLine("    \"Magentic Orchestration is not yet supported in C#.\"");
        Console.WriteLine();
        Console.WriteLine("    https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic?pivots=programming-language-csharp");
        Console.WriteLine();
        Console.WriteLine("Run the Python reference implementation instead:");
        Console.WriteLine();
        Console.WriteLine("    python tutorials/16-magentic-orchestration/python/main.py \\");
        Console.WriteLine("        \"plan a short launch brief for an AI meal planner\"");
        Console.WriteLine();
        Console.WriteLine("The chapter README walks the Python manager loop end-to-end — facts");
        Console.WriteLine("ledger, plan, progress ledger, stall detection, reset — and explains");
        Console.WriteLine("what to expect when Magentic lands in Microsoft.Agents.AI.Workflows.");
    }
}
