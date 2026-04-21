// MAF v1 — Chapter 19: Declarative Workflows (.NET)
//
// .NET also supports loading workflows from YAML. The official builder lives
// in Microsoft.Agents.AI.Workflows.Declarative. The Python chapter teaches
// the pattern by rolling a small custom YAML loader, and documents the
// built-in API here as a reference.

namespace MafV1.Ch19.Declarative;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 19 — Declarative Workflows");
        Console.WriteLine();
        Console.WriteLine("Python (runnable): python tutorials/19-declarative-workflows/python/main.py 'hello'");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  // Custom YAML with YamlDotNet:");
        Console.WriteLine("  var spec = new DeserializerBuilder().Build()");
        Console.WriteLine("      .Deserialize<WorkflowSpec>(File.ReadAllText(\"workflow.yaml\"));");
        Console.WriteLine("  var builder = new WorkflowBuilder(factory(spec.Start)).WithName(spec.Name);");
        Console.WriteLine("  foreach (var e in spec.Edges) builder.AddEdge(factory(e.From), factory(e.To));");
        Console.WriteLine("  var workflow = builder.Build();");
    }
}
