// MAF v1 — Chapter 20: Workflow Visualization (.NET)
//
// Render a MAF workflow as Mermaid and Graphviz DOT so you can commit diagrams
// alongside code, include them in docs, and diff graph changes in PRs.
//
// The .NET surface is WorkflowVisualizer — two static methods:
//
//   string mermaid = WorkflowVisualizer.ToMermaidString(workflow);
//   string dot     = WorkflowVisualizer.ToDotString(workflow);
//
// They are STATIC methods, not extension methods. `workflow.ToMermaidString()`
// does not compile, which matters because that is the shape most people try
// first (Python's WorkflowViz wraps the workflow, and the name reads like an
// extension).
//
// The same three-executor pipeline as the Python chapter — uppercase ->
// validate -> log — so both runtimes emit comparable graphs.
//
// Run:
//   cd tutorials/20-visualization/dotnet
//   dotnet run        # prints both, and writes workflow.mmd / workflow.dot
//
// No LLM and no API key: visualization is a pure function of the graph.

using Microsoft.Agents.AI.Workflows;

namespace MafV1.Ch20.Visualization;

/// <summary>Uppercases the message and forwards it.</summary>
[SendsMessage(typeof(string))]
internal sealed partial class UppercaseExecutor() : Executor("uppercase")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        string message,
        IWorkflowContext context,
        CancellationToken cancellationToken = default) =>
        await context.SendMessageAsync(message.ToUpperInvariant(), cancellationToken: cancellationToken);
}

/// <summary>Short-circuits on blank input, otherwise forwards.</summary>
[SendsMessage(typeof(string))]
[YieldsOutput(typeof(string))]
internal sealed partial class ValidateExecutor() : Executor("validate")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        string message,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(message))
        {
            await context.YieldOutputAsync("[skipped]", cancellationToken);
            return;
        }

        await context.SendMessageAsync(message, cancellationToken: cancellationToken);
    }
}

/// <summary>Terminal executor: yields the logged line.</summary>
[YieldsOutput(typeof(string))]
internal sealed partial class LogExecutor() : Executor("log")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        string message,
        IWorkflowContext context,
        CancellationToken cancellationToken = default) =>
        await context.YieldOutputAsync($"LOGGED: {message}", cancellationToken);
}

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        Workflow workflow = BuildWorkflow();

        string mermaid = RenderMermaid(workflow);
        string dot = RenderDot(workflow);

        string outDir = args.Length > 0 ? args[0] : Directory.GetCurrentDirectory();
        await File.WriteAllTextAsync(Path.Combine(outDir, "workflow.mmd"), mermaid);
        await File.WriteAllTextAsync(Path.Combine(outDir, "workflow.dot"), dot);

        Console.WriteLine("=== Mermaid ===");
        Console.WriteLine(mermaid);
        Console.WriteLine();
        Console.WriteLine("=== Graphviz DOT ===");
        Console.WriteLine(dot);
        Console.WriteLine();
        Console.WriteLine($"Wrote workflow.mmd and workflow.dot to {outDir}");

        return 0;
    }

    /// <summary>The uppercase -> validate -> log pipeline this chapter draws.</summary>
    public static Workflow BuildWorkflow()
    {
        var uppercase = new UppercaseExecutor();
        var validate = new ValidateExecutor();
        var log = new LogExecutor();

        return new WorkflowBuilder(uppercase)
            .AddEdge(uppercase, validate)
            .AddEdge(validate, log)
            .WithOutputFrom(validate)
            .WithOutputFrom(log)
            .Build();
    }

    /// <summary>Mermaid flowchart source for <paramref name="workflow"/>.</summary>
    public static string RenderMermaid(Workflow workflow) => WorkflowVisualizer.ToMermaidString(workflow);

    /// <summary>Graphviz DOT source for <paramref name="workflow"/>.</summary>
    public static string RenderDot(Workflow workflow) => WorkflowVisualizer.ToDotString(workflow);
}
