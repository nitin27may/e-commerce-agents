// MAF v1 — Chapter 26: Evals (.NET)
//
// A tiny standalone eval loop: a handful of {prompt, expected facts} cases run
// against a small e-commerce Q&A agent, each scored two ways — a deterministic
// "did the expected fact appear" check, and a structured-output judge — and
// printed as a scorecard.
//
// The two tiers exist because they fail differently, and knowing which one is
// which is most of what makes an eval suite useful:
//
//   Deterministic  Cheap, exact, CI-safe. Can only check what is mechanically
//                  checkable — a price string, a stock number. Says nothing
//                  about whether the prose around that number is any good, and
//                  will happily pass an answer that is rude, off-topic, or
//                  three paragraphs long as long as "24.99" appears somewhere.
//   Judge          Catches the things the first tier cannot see. Costs a
//                  second model call per case, and is itself a model, so it is
//                  not a source of truth — it is a second opinion.
//
// The judge here is a stub with the same STRUCTURED SHAPE as the real one
// (agents/python/evals/scorers/llm_judge.py::JudgeVerdict — score, reasoning,
// failure mode), using a cheap heuristic instead of a model call. Spending a
// live call per case is not worth it for a teaching demo. Swap the body of
// JudgeResponseStub for a real judge call and nothing else in the loop changes;
// that substitutability is the point of giving a stub a real verdict type.
//
// This chapter's agent is intentionally toy-sized. The real harness this
// mirrors is agents/python/evals/harness.py, which runs cases through the
// actual production code path rather than a hand-rolled loop.
//
// Run:
//   cd tutorials/26-evals/dotnet
//   dotnet run

using System.ClientModel;
using System.ComponentModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenAI;

namespace MafV1.Ch26.Evals;

/// <summary>One eval case.</summary>
/// <param name="ExpectedFacts">
/// Substrings that MUST appear (case-insensitively) in a correct answer. This
/// is what a good eval case needs: not "does it sound plausible", but a
/// specific string a script can grep for.
/// </param>
public sealed record EvalCase(string CaseId, string Prompt, IReadOnlyList<string> ExpectedFacts);

/// <summary>The deterministic tier's verdict.</summary>
public sealed record DeterministicResult(
    double Score,
    IReadOnlyList<string> Found,
    IReadOnlyList<string> Missing);

/// <summary>
/// The judge tier's verdict — the same structured shape as the production
/// judge, so the stub and a real judge are drop-in replacements.
/// </summary>
public sealed record JudgeVerdict(double Score, string Reasoning, string? FailureMode = null);

/// <summary>One scored case.</summary>
public sealed record EvalResult(
    string CaseId,
    string Prompt,
    string Answer,
    DeterministicResult Deterministic,
    JudgeVerdict Judge);

public static class Program
{
    public const string Instructions =
        "You are a shopping assistant for a small electronics store. "
        + "When the user asks about a product's price, stock, or availability, call the "
        + "`search_catalog` tool with the product name and answer using the exact numbers it returns. "
        + "Never guess a price or stock count. For anything else, answer directly in one short sentence.";

    // ─────────────── Toy catalogue + tool ───────────────

    public static readonly IReadOnlyDictionary<string, (decimal Price, int Stock)> Catalog =
        new Dictionary<string, (decimal, int)>(StringComparer.OrdinalIgnoreCase)
        {
            ["wireless mouse"] = (24.99m, 42),
            ["mechanical keyboard"] = (89.99m, 15),
            ["usb-c hub"] = (34.50m, 0),
            ["noise-cancelling headphones"] = (149.99m, 8),
            ["portable charger"] = (19.99m, 120),
        };

    [Description("Look up the price and stock count for a product in the catalog by name.")]
    public static string SearchCatalog(
        [Description("The product name to look up, e.g. 'Wireless Mouse'.")] string productName)
    {
        if (!Catalog.TryGetValue(productName.Trim(), out (decimal Price, int Stock) item))
        {
            return $"No catalog entry for '{productName}'.";
        }

        string availability = item.Stock > 0 ? "in stock" : "out of stock";
        return $"{productName}: ${item.Price:F2}, {item.Stock} units ({availability}).";
    }

    // ─────────────── Eval cases ───────────────

    public static readonly IReadOnlyList<EvalCase> EvalCases = new[]
    {
        new EvalCase("mouse-price", "How much does the Wireless Mouse cost?", new[] { "24.99" }),
        new EvalCase("keyboard-stock", "How many Mechanical Keyboards are in stock?", new[] { "15" }),
        new EvalCase("hub-out-of-stock", "Is the USB-C Hub in stock?", new[] { "out of stock" }),
        new EvalCase("headphones-price", "What does the Noise-Cancelling Headphones cost?", new[] { "149.99" }),
        new EvalCase(
            "charger-price-and-stock",
            "Give me the price and stock count for the Portable Charger.",
            new[] { "19.99", "120" }),
    };

    // ─────────────── Scoring: deterministic tier ───────────────

    /// <summary>
    /// Did each expected fact literally appear in the response?
    /// </summary>
    /// <remarks>
    /// The same shape as the real evals/scorers/db_groundedness.py — a ratio of
    /// verified/total claims from a mechanical check. No model call, no
    /// ambiguity, and no opinion about the prose.
    ///
    /// A case with no expected facts scores 1.0. That is the only defensible
    /// answer (there was nothing to get wrong) and also a trap: a suite of
    /// empty cases reports a perfect pass rate.
    /// </remarks>
    public static DeterministicResult ScoreDeterministic(string responseText, IReadOnlyList<string> expectedFacts)
    {
        List<string> found = expectedFacts
            .Where(f => responseText.Contains(f, StringComparison.OrdinalIgnoreCase))
            .ToList();

        List<string> missing = expectedFacts.Except(found).ToList();

        double score = expectedFacts.Count == 0 ? 1.0 : (double)found.Count / expectedFacts.Count;

        return new DeterministicResult(score, found, missing);
    }

    // ─────────────── Scoring: judge tier (stub) ───────────────

    /// <summary>
    /// Stand-in for a second LLM call judging relevance and completeness.
    /// </summary>
    /// <remarks>
    /// The real judge sends the question, the expected fields, and the response
    /// to a second model and parses a <see cref="JudgeVerdict"/> back out. This
    /// reproduces the same output shape with a heuristic.
    ///
    /// Being a stub, it agrees with the deterministic tier by construction —
    /// which is exactly what a real judge must NOT do. The value of a second
    /// tier is disagreement; two scorers that always agree are one scorer
    /// costing twice as much.
    /// </remarks>
    public static JudgeVerdict JudgeResponseStub(
        string prompt,
        string responseText,
        IReadOnlyList<string> expectedFacts)
    {
        int covered = expectedFacts.Count(f => responseText.Contains(f, StringComparison.OrdinalIgnoreCase));
        int total = expectedFacts.Count == 0 ? 1 : expectedFacts.Count;
        double score = (double)covered / total;

        return score switch
        {
            1.0 => new JudgeVerdict(score, "Response covers every expected fact."),
            0.0 => new JudgeVerdict(score, "Response covers none of the expected facts.", "missing_field"),
            _ => new JudgeVerdict(score, $"Response covers {covered}/{total} expected facts.", "partial_coverage"),
        };
    }

    // ─────────────── The loop ───────────────

    public static AIAgent BuildAgent(IChatClient chatClient) =>
        chatClient.AsAIAgent(
            instructions: Instructions,
            name: "catalog-eval-agent",
            tools: new List<AITool> { AIFunctionFactory.Create(SearchCatalog, "search_catalog") });

    public static async Task<string> AskAsync(AIAgent agent, string question) =>
        (await agent.RunAsync(question).ConfigureAwait(false)).Text;

    public static async Task<IReadOnlyList<EvalResult>> RunEvalSuiteAsync(
        AIAgent agent,
        IReadOnlyList<EvalCase>? cases = null)
    {
        var results = new List<EvalResult>();

        foreach (EvalCase evalCase in cases ?? EvalCases)
        {
            string answer = await AskAsync(agent, evalCase.Prompt).ConfigureAwait(false);

            results.Add(new EvalResult(
                evalCase.CaseId,
                evalCase.Prompt,
                answer,
                ScoreDeterministic(answer, evalCase.ExpectedFacts),
                JudgeResponseStub(evalCase.Prompt, answer, evalCase.ExpectedFacts)));
        }

        return results;
    }

    public static void PrintScorecard(IReadOnlyList<EvalResult> results, Action<string>? write = null)
    {
        write ??= Console.WriteLine;

        write($"{"Case",-26}{"Deterministic",-15}{"Judge",-8}Notes");
        write(new string('-', 80));

        foreach (EvalResult r in results)
        {
            string notes = r.Deterministic.Missing.Count > 0
                ? $"missing: [{string.Join(", ", r.Deterministic.Missing)}]"
                : r.Judge.Reasoning;

            write($"{r.CaseId,-26}{r.Deterministic.Score,-15:F2}{r.Judge.Score,-8:F2}{notes}");
        }

        write(new string('-', 80));

        int passed = results.Count(r => r.Deterministic.Score == 1.0);
        write($"{passed}/{results.Count} cases fully grounded (deterministic score == 1.0)");
    }

    public static async Task<int> Main()
    {
        LoadDotEnv();

        IReadOnlyList<EvalResult> results = await RunEvalSuiteAsync(BuildAgent(BuildChatClient()));
        PrintScorecard(results);

        // Non-zero when anything failed, so this is usable as a CI gate rather
        // than a report nobody reads.
        return results.All(r => r.Deterministic.Score == 1.0) ? 0 : 1;
    }

    private static IChatClient BuildChatClient()
    {
        string provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";
        if (provider == "azure")
        {
            return new AzureOpenAIClient(
                    new Uri(Required("AZURE_OPENAI_ENDPOINT")),
                    new ApiKeyCredential(
                        Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                        ?? Required("AZURE_OPENAI_API_KEY")))
                .GetChatClient(Required("AZURE_OPENAI_DEPLOYMENT"))
                .AsIChatClient();
        }

        return new OpenAIClient(new ApiKeyCredential(Required("OPENAI_API_KEY")))
            .GetChatClient(Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1")
            .AsIChatClient();
    }

    private static string Required(string name) =>
        Environment.GetEnvironmentVariable(name)
            ?? throw new InvalidOperationException($"{name} must be set (see repo-root .env).");

    private static void LoadDotEnv()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, ".env")))
        {
            dir = dir.Parent;
        }

        if (dir is null) return;

        foreach (string raw in File.ReadAllLines(Path.Combine(dir.FullName, ".env")))
        {
            string line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            int eq = line.IndexOf('=');
            if (eq < 0) continue;
            string key = line[..eq].Trim();
            string value = line[(eq + 1)..].Trim().Trim('"').Trim('\'');
            if (Environment.GetEnvironmentVariable(key) is null)
            {
                Environment.SetEnvironmentVariable(key, value);
            }
        }
    }
}
