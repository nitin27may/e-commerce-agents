// MAF v1 — Chapter 10: Workflow Events and Builder (.NET)
//
// Extends the Ch09 Uppercase -> Validate -> Log pipeline with a *custom*
// ProgressEvent emitted from each executor before it does its work. The
// consumer interleaves lifecycle events (ExecutorInvokedEvent,
// ExecutorCompletedEvent, SuperStep*, WorkflowOutputEvent) with those
// custom events, so you see exactly what the workflow is doing while it
// runs.
//
// Run:
//   dotnet run                   # happy path ("hello world")
//   dotnet run -- "maf rocks"    # happy path, custom input
//   dotnet run -- ""             # empty -> Validate short-circuits, Log never fires

using Microsoft.Agents.AI.Workflows;

namespace MafV1.Ch10.Events;

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        string text = args.Length > 0 ? args[0] : "hello world";

        Workflow workflow = WorkflowFactory.Build();

        Console.WriteLine($"input:  '{text}'");
        Console.WriteLine();

        await foreach (string line in WorkflowRunner.RunAsync(workflow, text))
        {
            Console.WriteLine(line);
        }

        return 0;
    }
}

// ─────────────── Custom event ───────────────
//
// Custom events subclass WorkflowEvent. The base ctor takes an optional
// object payload that surfaces on Data; we model step + percent as a
// record so consumers can pattern-match on the concrete type.

/// <summary>
/// Payload emitted from every executor so the caller can render a live
/// progress indicator. Carries the executor's id and its completion
/// percentage for the pipeline.
/// </summary>
internal sealed class ProgressEvent(string step, int percent)
    : WorkflowEvent(new ProgressPayload(step, percent))
{
    public string Step => ((ProgressPayload)Data!).Step;
    public int Percent => ((ProgressPayload)Data!).Percent;
}

internal sealed record ProgressPayload(string Step, int Percent);

// ─────────────── Workflow assembly ───────────────

/// <summary>
/// Composes the three executors into a linear workflow:
/// <c>Uppercase -> Validate -> Log</c>.
/// Each executor emits a <see cref="ProgressEvent"/> before forwarding.
/// </summary>
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

// ─────────────── Event-stream consumer ───────────────

/// <summary>
/// Runs a workflow in streaming mode and yields each event formatted for
/// console display. Lifecycle events (what MAF emits automatically) are
/// prefixed with <c>[lifecycle]</c>; custom <see cref="ProgressEvent"/>
/// instances are prefixed with <c>[progress]</c>. The final output
/// appears as <c>[output]</c>.
/// </summary>
/// <remarks>
/// Showing both kinds side by side is the point of this chapter: it
/// makes the distinction between "events MAF emits for you" and
/// "events your executor emits" visible at the call site.
/// </remarks>
internal static class WorkflowRunner
{
    public static async IAsyncEnumerable<string> RunAsync(Workflow workflow, string input)
    {
        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, input);

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            string? line = Format(evt);
            if (line is not null)
            {
                yield return line;
            }
        }
    }

    private static string? Format(WorkflowEvent evt) => evt switch
    {
        // Custom events first — always match the concrete type before the base.
        ProgressEvent p => $"  [progress]  {p.Step,-10} -> {p.Percent,3}%",

        // Executor lifecycle.
        ExecutorInvokedEvent invoke => $"[lifecycle] executor_invoked    {invoke.ExecutorId}",
        ExecutorCompletedEvent done => $"[lifecycle] executor_completed  {done.ExecutorId}",
        ExecutorFailedEvent fail => $"[lifecycle] executor_failed     {fail.ExecutorId}: {fail.Data}",

        // Superstep lifecycle.
        SuperStepStartedEvent start => $"[lifecycle] superstep_started   (step starts)",
        SuperStepCompletedEvent stepDone => $"[lifecycle] superstep_completed (step ends)",

        // Workflow-level lifecycle.
        WorkflowStartedEvent => "[lifecycle] workflow_started",
        WorkflowOutputEvent output => $"  [output]    {output.Data}",
        WorkflowErrorEvent err => $"[lifecycle] workflow_error      {err.Data}",

        _ => null, // unknown event type; drop silently
    };
}

// ─────────────── Executors ───────────────
//
// Each executor is a partial class inheriting from Executor. [MessageHandler]
// marks the method that receives an inbound message; the source generator
// (Microsoft.Agents.AI.Workflows.Generators) wires it into the protocol.
//
// [SendsMessage] / [YieldsOutput] declare the outbound surface: the framework
// validates at build time that a handler only calls SendMessageAsync<T> /
// YieldOutputAsync<T> for declared types. These attributes also feed the
// graph visualizer (Ch20).
//
// Custom events are emitted via context.AddEventAsync(new MyEvent(...)).
// That emit is ordered — it arrives before any SendMessageAsync that
// follows it.

/// <summary>
/// Uppercases the input and forwards it. Emits a 33% progress event first.
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
        await context.AddEventAsync(new ProgressEvent("uppercase", 33), cancellationToken);
        await context.SendMessageAsync(message.ToUpperInvariant(), cancellationToken);
    }
}

/// <summary>
/// Routes valid inputs downstream; short-circuits empty/whitespace-only inputs
/// by yielding a terminal workflow output. Emits a 66% progress event.
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
        await context.AddEventAsync(new ProgressEvent("validate", 66), cancellationToken);

        if (string.IsNullOrWhiteSpace(message))
        {
            await context.YieldOutputAsync("[skipped: empty input]", cancellationToken);
            return;
        }

        await context.SendMessageAsync(message, cancellationToken);
    }
}

/// <summary>
/// Terminal executor: prefixes the text with "LOGGED:" and yields the final
/// workflow output. Emits a 100% progress event first.
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
        await context.AddEventAsync(new ProgressEvent("log", 100), cancellationToken);
        await context.YieldOutputAsync($"LOGGED: {message}", cancellationToken);
    }
}
