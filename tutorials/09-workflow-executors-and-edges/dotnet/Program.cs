// MAF v1 — Chapter 09: Workflow Executors and Edges (.NET)
//
// Three executors wired with edges:
//   Uppercase -> Validate -> Log
//
// The middle executor short-circuits via YieldOutputAsync when it receives
// an empty/whitespace string, skipping the downstream Log executor entirely.
// Mirror of tutorials/09-workflow-executors-and-edges/python/main.py.
//
// Run:
//   dotnet run                 # defaults to "hello world"
//   dotnet run -- ""           # empty  -> validate short-circuits
//   dotnet run -- "   "        # blank  -> validate short-circuits
//   dotnet run -- "maf rocks"  # happy path

using Microsoft.Agents.AI.Workflows;

namespace MafV1.Ch09.WorkflowDemo;

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        string text = args.Length > 0 ? args[0] : "hello world";

        Workflow workflow = WorkflowFactory.Build();

        Console.WriteLine($"input:  {Quote(text)}");

        await foreach (string output in WorkflowRunner.RunAsync(workflow, text))
        {
            Console.WriteLine($"output: {Quote(output)}");
        }

        return 0;
    }

    private static string Quote(string s) => $"'{s}'";
}

/// <summary>
/// Composes the three executors into a linear workflow:
/// <c>Uppercase -> Validate -> Log</c>.
/// </summary>
/// <remarks>
/// <see cref="ValidateExecutor"/> can terminate the run early with
/// <c>YieldOutputAsync</c>; when it does, <see cref="LogExecutor"/> never fires.
/// </remarks>
internal static class WorkflowFactory
{
    public static Workflow Build()
    {
        var uppercase = new UppercaseExecutor();
        var validate = new ValidateExecutor();
        var log = new LogExecutor();

        return new WorkflowBuilder(uppercase)
            .AddEdge(uppercase, validate)
            .AddEdge(validate, log)
            .WithOutputFrom(validate, log) // either can emit the final output
            .Build();
    }
}

/// <summary>
/// Runs a workflow in streaming mode and yields every workflow-level output
/// string emitted by <see cref="IWorkflowContext.YieldOutputAsync"/>.
/// </summary>
/// <remarks>
/// Streaming mode is used so the consumer can observe events as they happen.
/// For this tiny pipeline <see cref="InProcessExecution.RunAsync"/> would work
/// just as well, but streaming matches how real workflows (Ch12+) are run.
/// </remarks>
internal static class WorkflowRunner
{
    public static async IAsyncEnumerable<string> RunAsync(Workflow workflow, string input)
    {
        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, input);

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent output && output.Data is string s)
            {
                yield return s;
            }
        }
    }
}

// ─────────────── Executors ───────────────
//
// Each executor is a `partial` class that inherits from `Executor` (or the
// generic `Executor<TIn>` / `Executor<TIn, TOut>`) and declares a method
// decorated with `[MessageHandler]`. The framework uses that attribute at
// registration time to wire the handler to inbound edges.
//
// `[SendsMessage(typeof(...))]` and `[YieldsOutput(typeof(...))]` declare the
// executor's outbound surface. They're used for static validation and graph
// visualization (Ch20); the workflow won't accept a `SendMessageAsync<T>` call
// unless the executor has declared it can send `T`.

/// <summary>
/// Uppercases the incoming text and forwards it to the next executor.
/// </summary>
[SendsMessage(typeof(string))]
internal sealed partial class UppercaseExecutor() : Executor("uppercase")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        string message,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        await context.SendMessageAsync(message.ToUpperInvariant(), cancellationToken);
    }
}

/// <summary>
/// Routes valid inputs downstream; short-circuits empty/whitespace-only inputs
/// by yielding a terminal workflow output. When it short-circuits, no edge
/// out of this executor fires for that run.
/// </summary>
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
            await context.YieldOutputAsync("[skipped: empty input]", cancellationToken);
            return;
        }

        await context.SendMessageAsync(message, cancellationToken);
    }
}

/// <summary>
/// Terminal executor: decorates the text with a log prefix and yields
/// the final workflow output.
/// </summary>
[YieldsOutput(typeof(string))]
internal sealed partial class LogExecutor() : Executor("log")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        string message,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        await context.YieldOutputAsync($"LOGGED: {message}", cancellationToken);
    }
}
