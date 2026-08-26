// MAF v1 — Chapter 28: Reflection and Critique (.NET)
//
// The reflection / critic-loop pattern: an agent produces a draft, a second
// agent grades it against explicit named criteria and returns specific
// feedback, and — if it does not meet the bar — the draft agent revises using
// that feedback and the critic grades again. This repeats until the draft
// passes or a hard iteration cap is hit.
//
// Every other chapter in this series is a single LLM call, or a tool-calling
// loop MAF itself drives and bounds. This is the first chapter where OUR code
// drives a multi-turn loop with no framework-enforced bound. MaxIterations is
// the only thing standing between this and an unbounded token bill, and a
// critic with a strict rubric will happily spin it forever.
//
// The parsing is the other half of the design. A critic is a second LLM call,
// not framework magic — MAF has no opinion on reflection loops — so this file
// owns both the loop and the job of turning the critic's free text into
// something the loop can branch on. Any criterion the critic does not clearly
// mark PASS is treated as FAIL: a critic that did not say PASS has not earned
// one, and treating "unparseable" as "good enough" is how a loop silently
// stops enforcing anything at all.
//
// Run:
//   cd tutorials/28-reflection-and-critique/dotnet
//   dotnet run

using System.ClientModel;
using System.Text.RegularExpressions;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenAI;

namespace MafV1.Ch28.Reflection;

/// <summary>The product a description is being written for.</summary>
public sealed record Product(string Id, string Name, decimal Price, IReadOnlyList<string> Features);

/// <summary>One critic verdict, parsed from the critic's fixed-format reply.</summary>
public sealed record CritiqueResult(bool PriceOk, bool FeatureOk, bool LengthOk, string Feedback)
{
    public bool Passed => PriceOk && FeatureOk && LengthOk;
}

/// <summary>One pass of the loop: what was drafted, and how it was graded.</summary>
public sealed record Iteration(int Number, string Draft, CritiqueResult Critique);

public static partial class Program
{
    /// <summary>
    /// Hard cap on draft -> critique -> revise cycles.
    /// </summary>
    /// <remarks>
    /// Without this, a critic that never says PASS — a strict rubric, a flaky
    /// model, a genuinely unsatisfiable constraint — spins forever, burning one
    /// draft call and one critic call per turn indefinitely. Nothing in MAF
    /// bounds this loop; it is ours to bound.
    /// </remarks>
    public const int MaxIterations = 3;

    public const int WordLimit = 40;

    public const string DraftInstructions =
        "You write short e-commerce product descriptions. Follow the price, feature, and "
        + "word-limit constraints given in the prompt exactly — do not round the price and do not "
        + "invent features not listed. Return only the description text, no preamble, no quotes.";

    public const string CriticInstructions =
        "You are a strict copy editor grading a product description against three named criteria: "
        + "PRICE (does it mention the exact price given), FEATURE (does it mention at least one of "
        + "the listed features), LENGTH (is it at or under the given word limit). "
        + "Respond in EXACTLY this format, one line per criterion, nothing before or after it:\n"
        + "PRICE: PASS or FAIL\n"
        + "FEATURE: PASS or FAIL\n"
        + "LENGTH: PASS or FAIL\n"
        + "FEEDBACK: one sentence covering every FAIL, or 'none' if all three pass\n"
        + "Grade exactly what the text says — do not soften a FAIL into a PASS to be polite.";

    public static readonly Product DefaultProduct = new(
        "P010",
        "Aurora Desk Lamp",
        39.99m,
        new[] { "adjustable color temperature", "USB-C charging port", "touch dimmer" });

    // ─────────────── Prompts ───────────────

    public static string DraftPrompt(Product product) =>
        $"Write a product description for '{product.Name}'. "
        + $"Price: ${product.Price:F2}. Features: {string.Join(", ", product.Features)}. "
        + $"Keep it to {WordLimit} words or fewer.";

    public static string CriticPrompt(Product product, string draft) =>
        $"Product: {product.Name}\n"
        + $"Price: ${product.Price:F2}\n"
        + $"Features: {string.Join(", ", product.Features)}\n"
        + $"Word limit: {WordLimit}\n\n"
        + $"Description to grade:\n{draft}\n\n"
        + "Grade it against the PRICE, FEATURE, and LENGTH criteria.";

    public static string RevisePrompt(Product product, string draft, CritiqueResult critique) =>
        $"Revise this product description for '{product.Name}' to fix the critic's feedback. "
        + "Return only the revised description, no preamble.\n\n"
        + $"Previous draft:\n{draft}\n\n"
        + $"Critic feedback: {critique.Feedback}\n\n"
        + $"Reminder — price: ${product.Price:F2}, features: {string.Join(", ", product.Features)}, "
        + $"word limit: {WordLimit} words.";

    // ─────────────── Critic parsing ───────────────

    [GeneratedRegex(@"^\s*(PRICE|FEATURE|LENGTH)\s*:\s*(PASS|FAIL)",
        RegexOptions.IgnoreCase | RegexOptions.Multiline)]
    private static partial Regex CriterionPattern();

    [GeneratedRegex(@"^\s*FEEDBACK\s*:\s*(.+)$", RegexOptions.IgnoreCase | RegexOptions.Multiline)]
    private static partial Regex FeedbackPattern();

    /// <summary>
    /// Parses the critic's fixed-format response.
    /// </summary>
    /// <remarks>
    /// Any criterion line the critic omits is treated as FAIL, not PASS. That
    /// is the safe direction: an unparseable critique makes the loop revise
    /// and eventually hit the cap, rather than silently shipping a draft
    /// nobody graded.
    /// </remarks>
    public static CritiqueResult ParseCritique(string text)
    {
        var verdicts = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
        foreach (Match match in CriterionPattern().Matches(text))
        {
            verdicts[match.Groups[1].Value.ToUpperInvariant()] =
                match.Groups[2].Value.Equals("PASS", StringComparison.OrdinalIgnoreCase);
        }

        Match feedback = FeedbackPattern().Match(text);

        return new CritiqueResult(
            PriceOk: verdicts.GetValueOrDefault("PRICE"),
            FeatureOk: verdicts.GetValueOrDefault("FEATURE"),
            LengthOk: verdicts.GetValueOrDefault("LENGTH"),
            Feedback: feedback.Success ? feedback.Groups[1].Value.Trim() : string.Empty);
    }

    // ─────────────── The loop ───────────────

    /// <summary>
    /// Draft -> critique -> revise -> critique -> ... up to <paramref name="maxIterations"/>.
    /// </summary>
    /// <returns>
    /// Every iteration's draft and critique, in order — so the caller sees the
    /// whole trace, not just the final answer. A reflection loop that only
    /// returns its last draft hides whether it improved anything.
    /// </returns>
    public static async Task<IReadOnlyList<Iteration>> RunReflectionLoopAsync(
        AIAgent draftAgent,
        AIAgent criticAgent,
        Product product,
        int maxIterations = MaxIterations)
    {
        var iterations = new List<Iteration>();
        string draft = await AskAsync(draftAgent, DraftPrompt(product)).ConfigureAwait(false);

        for (int number = 1; number <= maxIterations; number++)
        {
            string critiqueText = await AskAsync(criticAgent, CriticPrompt(product, draft)).ConfigureAwait(false);
            CritiqueResult critique = ParseCritique(critiqueText);
            iterations.Add(new Iteration(number, draft, critique));

            // Stop early on a pass; otherwise stop at the cap even if the last
            // critique still fails. The caller can tell which happened by
            // checking the final iteration's Critique.Passed.
            if (critique.Passed || number == maxIterations)
            {
                break;
            }

            draft = await AskAsync(draftAgent, RevisePrompt(product, draft, critique)).ConfigureAwait(false);
        }

        return iterations;
    }

    public static async Task<string> AskAsync(AIAgent agent, string question) =>
        (await agent.RunAsync(question).ConfigureAwait(false)).Text.Trim();

    // ─────────────── Agents ───────────────

    public static AIAgent BuildDraftAgent(IChatClient chatClient) =>
        chatClient.AsAIAgent(instructions: DraftInstructions, name: "draft-agent");

    public static AIAgent BuildCriticAgent(IChatClient chatClient) =>
        chatClient.AsAIAgent(instructions: CriticInstructions, name: "critic-agent");

    public static async Task<int> Main()
    {
        LoadDotEnv();

        IChatClient client = BuildChatClient();
        IReadOnlyList<Iteration> iterations = await RunReflectionLoopAsync(
            BuildDraftAgent(client), BuildCriticAgent(client), DefaultProduct);

        foreach (Iteration iteration in iterations)
        {
            Console.WriteLine($"--- iteration {iteration.Number} ---");
            Console.WriteLine(iteration.Draft);
            Console.WriteLine($"  verdict: {FormatVerdict(iteration.Critique)}");
            Console.WriteLine($"  feedback: {iteration.Critique.Feedback}");
            Console.WriteLine();
        }

        Iteration last = iterations[^1];
        Console.WriteLine(last.Critique.Passed
            ? $"Passed after {last.Number} iteration(s)."
            : $"Gave up after {last.Number} iteration(s) — still failing: {FormatVerdict(last.Critique)}");

        return last.Critique.Passed ? 0 : 1;
    }

    private static string FormatVerdict(CritiqueResult c) =>
        $"PRICE={(c.PriceOk ? "PASS" : "FAIL")} "
        + $"FEATURE={(c.FeatureOk ? "PASS" : "FAIL")} "
        + $"LENGTH={(c.LengthOk ? "PASS" : "FAIL")}";

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
