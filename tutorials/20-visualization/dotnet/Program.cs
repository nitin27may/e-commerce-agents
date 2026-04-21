// MAF v1 — Chapter 20: Workflow Visualization (.NET)
//
// Reference: .NET ships extension methods to serialize a Workflow to
// Mermaid or Graphviz DOT. Python is the canonical runnable example.

namespace MafV1.Ch20.Visualization;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 20 — Workflow Visualization");
        Console.WriteLine();
        Console.WriteLine("Python (runnable): python tutorials/20-visualization/python/main.py");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  using Microsoft.Agents.AI.Workflows;");
        Console.WriteLine();
        Console.WriteLine("  string mermaid = workflow.ToMermaidString();");
        Console.WriteLine("  string dot     = workflow.ToDotString();");
        Console.WriteLine();
        Console.WriteLine("  File.WriteAllText(\"workflow.mmd\", mermaid);");
        Console.WriteLine("  File.WriteAllText(\"workflow.dot\", dot);");
    }
}
