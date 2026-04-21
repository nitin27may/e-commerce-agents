// MAF v1 — Chapter 17: Human-in-the-Loop (.NET)
//
// Reference: .NET uses RequestPort<TRequest, TResponse> to pause a workflow.
// Callers provide responses via the same streaming-events loop that reports
// RequestInfoEvents. Python is the canonical runnable example.

namespace MafV1.Ch17.Hitl;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 17 — Human-in-the-Loop");
        Console.WriteLine();
        Console.WriteLine("Python (runnable): python tutorials/17-human-in-the-loop/python/main.py");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  // Inside an executor (partial class + source-gen):");
        Console.WriteLine("  [MessageHandler]");
        Console.WriteLine("  public async ValueTask RunAsync(string prompt, IWorkflowContext ctx) =>");
        Console.WriteLine("      await ctx.RequestInfoAsync<GuessRequest, int>(new GuessRequest(prompt));");
        Console.WriteLine();
        Console.WriteLine("  [ResponseHandler]");
        Console.WriteLine("  public async ValueTask OnGuessAsync(GuessRequest req, int guess, IWorkflowContext ctx) =>");
        Console.WriteLine("      await ctx.YieldOutputAsync(JudgeGuess(guess));");
        Console.WriteLine();
        Console.WriteLine("  // Consumer pauses, then resumes with the response:");
        Console.WriteLine("  await foreach (var evt in InProcessExecution.StreamAsync(workflow, \"Pick a number:\"))");
        Console.WriteLine("      if (evt is RequestInfoEvent r) response = await PromptUser(r);");
        Console.WriteLine("  await foreach (var evt in workflow.ResumeAsync(responses)) { ... }");
    }
}
