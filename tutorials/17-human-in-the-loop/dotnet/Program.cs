// MAF v1 — Chapter 17: Human-in-the-Loop (.NET)
//
// A workflow that pauses mid-run to ask a human operator to guess a number,
// then resumes when the answer arrives. Demonstrates the canonical .NET HITL
// surface:
//
//   - RequestPort.Create<TRequest, TResponse>(id) — the pause/resume channel
//   - WorkflowBuilder(port).AddEdge(port, judge).AddEdge(judge, port)
//   - Judge sends hint signals back through the port; the port re-emits
//     RequestInfoEvent to the caller, who supplies the next guess
//   - Caller sees each RequestInfoEvent on the stream, calls
//     run.SendResponseAsync(evt.Request.CreateResponse(guess)) and keeps
//     iterating the same StreamingRun until WorkflowOutputEvent arrives
//
// No LLM required — HITL is framework plumbing, not model behaviour.
//
// Run:
//   cd tutorials/17-human-in-the-loop/dotnet
//   dotnet run           # interactive: prompts for each guess
//   dotnet run -- 7      # scripted: keeps guessing 7 until the game ends

using Microsoft.Agents.AI.Workflows;

namespace MafV1.Ch17.Hitl;

/// <summary>
/// Signal the workflow sends out through the <see cref="RequestPort"/> when it
/// needs a (new) guess. The <see cref="NumberSignal.Init"/> value kicks off the
/// first round; subsequent values tell the caller whether the previous guess
/// was too high or too low.
/// </summary>
internal enum NumberSignal
{
    Init,
    Above,
    Below,
}

/// <summary>
/// Holds the secret and judges each guess. On a miss it sends a hint back
/// through the request port; the port re-emits RequestInfoEvent to the caller,
/// who supplies the next guess.
/// </summary>
[SendsMessage(typeof(NumberSignal))]
[YieldsOutput(typeof(string))]
internal sealed class JudgeExecutor() : Executor<int>("judge")
{
    private readonly int _target;
    private int _tries;

    public JudgeExecutor(int target) : this()
    {
        this._target = target;
    }

    public override async ValueTask HandleAsync(
        int guess,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        this._tries++;

        if (guess == this._target)
        {
            await context.YieldOutputAsync(
                $"correct! the number was {this._target} (after {this._tries} tries)",
                cancellationToken);
            return;
        }

        NumberSignal hint = guess < this._target ? NumberSignal.Below : NumberSignal.Above;
        await context.SendMessageAsync(hint, cancellationToken: cancellationToken);
    }
}

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        const int Secret = 7;

        // Build the workflow. The request port is BOTH the starting executor
        // (it emits the first RequestInfoEvent when we kick the run off with a
        // NumberSignal.Init) and the downstream target of the judge, so the
        // loop keeps pausing until the judge yields output.
        RequestPort numberPort = RequestPort.Create<NumberSignal, int>("GuessNumber");
        JudgeExecutor judge = new(Secret);

        Workflow workflow = new WorkflowBuilder(numberPort)
            .AddEdge(numberPort, judge)
            .AddEdge(judge, numberPort)
            .WithOutputFrom(judge)
            .Build();

        // Optional scripted mode: `dotnet run -- 7` keeps replying with the
        // same guess every time, which is useful for CI and for readers who
        // just want to see a deterministic pass.
        int? scriptedGuess = null;
        if (args.Length > 0 && int.TryParse(args[0], out int canned))
        {
            scriptedGuess = canned;
        }

        Console.WriteLine("Chapter 17 — Human-in-the-Loop (guess the number 1..10)");
        Console.WriteLine();

        await using StreamingRun run = await InProcessExecution
            .RunStreamingAsync(workflow, NumberSignal.Init);

        // Single StreamingRun, single foreach. Each pause is handled inline
        // with run.SendResponseAsync(...); the framework routes the response
        // to the request port, which forwards it to the judge. Contrast with
        // Python where you make two separate workflow.run(...) calls.
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case RequestInfoEvent request:
                    int guess = scriptedGuess ?? ReadGuessFrom(request);
                    if (scriptedGuess is not null)
                    {
                        Console.WriteLine($"  -> sending scripted guess {guess}");
                    }
                    await run.SendResponseAsync(request.Request.CreateResponse(guess));
                    break;

                case WorkflowOutputEvent output:
                    Console.WriteLine();
                    Console.WriteLine(output.Data);
                    return 0;

                case ExecutorFailedEvent failed:
                    Console.Error.WriteLine($"  [fail] executor '{failed.ExecutorId}' failed: {failed.Data}");
                    return 1;

                case WorkflowErrorEvent error:
                    Console.Error.WriteLine($"  [err]  {error.Exception}");
                    return 1;
            }
        }

        return 0;
    }

    private static int ReadGuessFrom(RequestInfoEvent evt)
    {
        // The payload on the first round is NumberSignal.Init; on subsequent
        // rounds it's Above / Below so we can prompt better.
        string hint = evt.Request.TryGetDataAs<NumberSignal>(out NumberSignal signal) && signal != NumberSignal.Init
            ? $" (previous guess was too {signal.ToString().ToLowerInvariant()})"
            : "";
        Console.Write($"Your guess 1..10{hint}: ");

        string? line = Console.ReadLine();
        return int.TryParse(line, out int g) ? g : 1;
    }
}
