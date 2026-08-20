// MAF v1 — Chapter 09: Workflow Executors and Edges (.NET)
//
// Three executors wired with edges:
//   NormalizeOrder -> ValidateOrder -> LogOrder
//
// The middle executor short-circuits via YieldOutputAsync when it receives
// an empty/whitespace order id, skipping the downstream LogOrder executor
// entirely.
// Mirror of tutorials/09-workflow-executors-and-edges/python/main.py.
//
// Run:
//   dotnet run                    # defaults to "ord-8842"
//   dotnet run -- ""              # empty  -> validate short-circuits
//   dotnet run -- "   "           # blank  -> validate short-circuits
//   dotnet run -- "ord-42a"       # happy path

using Microsoft.Agents.AI.Workflows;

namespace MafV1.Ch09.WorkflowDemo;

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        string orderId = args.Length > 0 ? args[0] : "ord-8842";

        Workflow workflow = WorkflowFactory.Build();

        Console.WriteLine($"input:  {Quote(orderId)}");

        await foreach (string output in WorkflowRunner.RunAsync(workflow, orderId))
        {
            Console.WriteLine($"output: {Quote(output)}");
        }

        return 0;
    }

    private static string Quote(string s) => $"'{s}'";
}

/// <summary>
/// Composes the three executors into a linear workflow:
/// <c>NormalizeOrder -> ValidateOrder -> LogOrder</c>.
/// </summary>
/// <remarks>
/// <see cref="ValidateOrderExecutor"/> can terminate the run early with
/// <c>YieldOutputAsync</c>; when it does, <see cref="LogOrderExecutor"/> never fires.
/// </remarks>
internal static class WorkflowFactory
{
    public static Workflow Build()
    {
        var normalize = new NormalizeOrderExecutor();
        var validate = new ValidateOrderExecutor();
        var log = new LogOrderExecutor();

        return new WorkflowBuilder(normalize)
            .AddEdge(normalize, validate)
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
/// Normalizes the incoming order id (trims + uppercases) and forwards it to
/// the next executor.
/// </summary>
[SendsMessage(typeof(string))]
internal sealed partial class NormalizeOrderExecutor() : Executor("normalize-order")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        string orderId,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        await context.SendMessageAsync(orderId.Trim().ToUpperInvariant(), cancellationToken);
    }
}

/// <summary>
/// Routes valid order ids downstream; short-circuits empty/whitespace-only
/// ids by yielding a terminal workflow output. When it short-circuits, no
/// edge out of this executor fires for that run.
/// </summary>
[SendsMessage(typeof(string))]
[YieldsOutput(typeof(string))]
internal sealed partial class ValidateOrderExecutor() : Executor("validate-order")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        string orderId,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(orderId))
        {
            await context.YieldOutputAsync("[rejected: empty order id]", cancellationToken);
            return;
        }

        await context.SendMessageAsync(orderId, cancellationToken);
    }
}

/// <summary>
/// Terminal executor: decorates the order id with a log prefix and yields
/// the final workflow output.
/// </summary>
[YieldsOutput(typeof(string))]
internal sealed partial class LogOrderExecutor() : Executor("log-order")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        string orderId,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        await context.YieldOutputAsync($"ORDER LOGGED: {orderId}", cancellationToken);
    }
}
