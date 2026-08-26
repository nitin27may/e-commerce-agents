// MAF v1 — Chapter 32: Cost Control and Budgets (.NET)
//
// A DelegatingChatClient that tracks the cumulative estimated USD cost of
// every LLM turn in a run and, once a configured ceiling is crossed, refuses
// to start the NEXT turn. The .NET counterpart of the Python chapter's
// CostBudgetChatMiddleware, and a toy stand-in for production's
// agents/python/shared/guardrails/cost_budget_middleware.py.
//
// Two things about the mechanic are worth being precise about, because both
// look like bugs until you think about them:
//
//   1. Enforcement is one turn behind. Cost is only knowable AFTER a turn
//      completes, from its Usage. So the turn that crosses the ceiling always
//      runs to completion; what gets refused is the one after it. A budget
//      that promised a hard cap would be lying.
//   2. The refusal is a short-circuit, not an exception. The middleware sets a
//      refusal response and never calls the inner client, so the caller gets a
//      normal-looking answer explaining what happened rather than a stack
//      trace it has to special-case.
//
// Two modes, mirroring production's COST_BUDGET_MODE:
//
//   Observe — accumulate and report; never blocks, even past the ceiling.
//             This is production's default, and the one to ship first.
//   Enforce — same accumulation, plus the refusal above.
//
// A note on the Python/.NET difference, because it is the interesting one:
// the Python chapter cannot test its enforcement path under LLM_PROVIDER=replay,
// because the replay client composes FunctionInvocationLayer directly and skips
// ChatMiddlewareLayer entirely. .NET has no such gap — a DelegatingChatClient
// wraps whatever it is given, scripted or real — so the .NET side of this
// chapter is the one where the budget behaviour is actually gated in CI.
//
// Run:
//   cd tutorials/32-cost-control-and-budgets/dotnet
//   dotnet run
//   dotnet run -- "What's the price of product P-100?"

using System.ClientModel;
using System.ComponentModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenAI;

namespace MafV1.Ch32.CostControl;

/// <summary>How a budget behaves once the ceiling is crossed.</summary>
public enum BudgetMode
{
    /// <summary>No accounting at all.</summary>
    Off,

    /// <summary>Accumulate and report, never block. Production's default.</summary>
    Observe,

    /// <summary>Accumulate, and refuse the next turn once over budget.</summary>
    Enforce,
}

/// <summary>
/// Simplified single-model pricing, USD per 1K tokens. The same numbers as
/// production's shared/cost.py::_PRICING["gpt-4.1"], so the dollar amounts
/// this demo prints are realistic rather than invented.
/// </summary>
public static class Pricing
{
    public const decimal InputPer1K = 0.002m;
    public const decimal OutputPer1K = 0.008m;

    /// <summary>Estimated USD cost of one turn from its token counts.</summary>
    public static decimal EstimateUsd(int tokensIn, int tokensOut) =>
        (tokensIn / 1000m * InputPer1K) + (tokensOut / 1000m * OutputPer1K);
}

/// <summary>
/// Tracks cumulative per-run cost and, in <see cref="BudgetMode.Enforce"/>,
/// caps it.
/// </summary>
public sealed class CostBudgetChatClient : DelegatingChatClient
{
    public const string RefusalMessage =
        "This run has been stopped because it exceeded its configured cost budget. "
        + "Start a new request, or raise the budget if this ceiling is too low.";

    private readonly Budget _budget;
    private readonly Action<string> _log;

    public CostBudgetChatClient(IChatClient inner, Budget budget, Action<string>? log = null)
        : base(inner)
    {
        _budget = budget;
        _log = log ?? (_ => { });
    }

    public override async Task<ChatResponse> GetResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        if (_budget.Mode == BudgetMode.Off)
        {
            return await base.GetResponseAsync(messages, options, cancellationToken).ConfigureAwait(false);
        }

        if (_budget.ShouldRefuse())
        {
            _budget.RecordBlocked();
            _log($"  [budget] refused turn {_budget.TurnsRecorded + _budget.TurnsBlocked} — "
                 + $"running total ${_budget.TotalUsd:F4} already exceeds ${_budget.LimitUsd:F4}");

            // The short-circuit: base is never called, so no further LLM turn
            // is made once the run is already over budget.
            return new ChatResponse(new ChatMessage(ChatRole.Assistant, RefusalMessage))
            {
                FinishReason = ChatFinishReason.Length,
            };
        }

        ChatResponse response = await base
            .GetResponseAsync(messages, options, cancellationToken)
            .ConfigureAwait(false);

        Record(response.Usage);
        return response;
    }

    public override async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        if (_budget.Mode != BudgetMode.Off && _budget.ShouldRefuse())
        {
            _budget.RecordBlocked();
            yield return new ChatResponseUpdate(ChatRole.Assistant, RefusalMessage);
            yield break;
        }

        await foreach (ChatResponseUpdate update in base
            .GetStreamingResponseAsync(messages, options, cancellationToken)
            .ConfigureAwait(false))
        {
            // Usage arrives as a content item on the stream rather than on a
            // response object. Missing this is how a streaming agent ends up
            // with a budget that never accumulates and therefore never trips.
            foreach (AIContent content in update.Contents)
            {
                if (content is UsageContent usage)
                {
                    Record(usage.Details);
                }
            }

            yield return update;
        }
    }

    private void Record(UsageDetails? usage)
    {
        if (usage is null)
        {
            // No usage data — nothing to price. A provider that omits usage
            // silently disables the budget, which is worth knowing about
            // rather than treating as zero cost.
            _budget.RecordUnpriced();
            return;
        }

        int tokensIn = (int)(usage.InputTokenCount ?? 0);
        int tokensOut = (int)(usage.OutputTokenCount ?? 0);
        decimal cost = Pricing.EstimateUsd(tokensIn, tokensOut);

        _budget.RecordTurn(cost);
        _log($"  [budget] turn {_budget.TurnsRecorded}: +${cost:F4} "
             + $"(in={tokensIn} out={tokensOut}) -> running total ${_budget.TotalUsd:F4}");
    }
}

/// <summary>
/// The running total for one run. Kept separate from the client so several
/// clients in one pipeline share one budget, and so a test can inspect it.
/// </summary>
public sealed class Budget(decimal limitUsd, BudgetMode mode = BudgetMode.Enforce)
{
    public decimal LimitUsd { get; } = limitUsd;
    public BudgetMode Mode { get; } = mode;
    public decimal TotalUsd { get; private set; }
    public int TurnsRecorded { get; private set; }
    public int TurnsBlocked { get; private set; }

    /// <summary>Turns that completed but reported no usage, so could not be priced.</summary>
    public int TurnsUnpriced { get; private set; }

    public bool IsOverBudget => TotalUsd > LimitUsd;

    internal bool ShouldRefuse() => Mode == BudgetMode.Enforce && IsOverBudget;

    internal void RecordTurn(decimal cost)
    {
        TotalUsd += cost;
        TurnsRecorded++;
    }

    internal void RecordBlocked() => TurnsBlocked++;

    internal void RecordUnpriced() => TurnsUnpriced++;
}

public static class Program
{
    public const string Instructions =
        "You are a shopping assistant. When the user asks about a product's price, call the "
        + "`get_product_price` tool with the product ID and answer in one short sentence.";

    /// <summary>
    /// Deliberately tiny — a fraction of a cent. Real production ceilings are
    /// set for real workloads (dollars, not cents); this number exists only so
    /// the ceiling trips within two or three short demo questions instead of
    /// requiring hundreds of paid turns.
    /// </summary>
    public const decimal DemoBudgetUsdPerRun = 0.0015m;

    public static readonly string[] DefaultQuestions =
    {
        "What's the price of product P-100?",
        "What's the price of product P-200?",
        "What's the price of product P-300?",
    };

    /// <summary>Canned catalogue lookup — no real data source.</summary>
    [Description("Look up the current price for a product by ID.")]
    public static string GetProductPrice(
        [Description("The product ID to look up, e.g. 'P-100'.")] string productId)
    {
        var canned = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["P-100"] = "$129.99",
            ["P-200"] = "$49.50",
            ["P-300"] = "$899.00",
        };

        return canned.GetValueOrDefault(productId.Trim(), $"No price found for product {productId}.");
    }

    /// <summary>
    /// Wraps <paramref name="inner"/> in the budget client and builds the agent.
    /// </summary>
    /// <remarks>
    /// Order matters. The budget client sits OUTSIDE function invocation, so
    /// each model round trip in a tool-calling loop is one budgeted turn —
    /// which is what makes a two-turn tool call cost twice, as it should.
    /// </remarks>
    public static AIAgent BuildAgent(IChatClient inner, Budget budget, Action<string>? log = null)
    {
        IChatClient pipeline = inner
            .AsBuilder()
            .Use(next => new CostBudgetChatClient(next, budget, log))
            .Build();

        return pipeline.AsAIAgent(
            instructions: Instructions,
            name: "cost-budget-agent",
            tools: new List<AITool> { AIFunctionFactory.Create(GetProductPrice) });
    }

    public static async Task<string> AskAsync(AIAgent agent, string question) =>
        (await agent.RunAsync(question).ConfigureAwait(false)).Text;

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();

        string[] questions = args.Length > 0 ? args : DefaultQuestions;
        var budget = new Budget(DemoBudgetUsdPerRun, BudgetMode.Enforce);
        AIAgent agent = BuildAgent(BuildChatClient(), budget, Console.WriteLine);

        Console.WriteLine($"budget: ${budget.LimitUsd:F4} per run (mode={budget.Mode})");
        Console.WriteLine();

        foreach (string question in questions)
        {
            string answer = await AskAsync(agent, question);
            Console.WriteLine($"Q: {question}");
            Console.WriteLine($"A: {answer}");
            Console.WriteLine();
        }

        Console.WriteLine($"turns recorded: {budget.TurnsRecorded}");
        Console.WriteLine($"turns blocked:  {budget.TurnsBlocked}");
        Console.WriteLine($"running total:  ${budget.TotalUsd:F4} (budget ${budget.LimitUsd:F4})");

        return 0;
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
