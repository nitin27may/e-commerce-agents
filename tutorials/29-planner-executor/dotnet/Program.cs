// MAF v1 — Chapter 29: Planner-Executor (.NET)
//
// Decompose a request into an ordered plan up front — structured output from a
// "planner" agent — then execute each step in sequence with an "executor"
// agent. A step might be a catalogue search or a reasoning step over earlier
// results. The plan is committed to before any step runs.
//
// Contrast with the router/tool pattern (chapters 02 and 12+, and this repo's
// own "tool" orchestration mode): there, the model decides one tool call at a
// time, reactively, with no advance plan. Here the whole plan is inspectable
// up front — more predictable, easier to approve or cost-estimate, and less
// adaptive to a step's surprise result unless you add re-planning, which this
// chapter deliberately does not.
//
// Two details carry the pattern, and both are easy to lose:
//
//   1. The planner has NO tools. Give it any and it will start executing
//      instead of planning, and you get a reactive agent with extra steps.
//   2. All steps share ONE AgentSession, so step N can see step N-1's result.
//      Without that, a "pick the best candidate" step has nothing to pick from
//      and will invent something plausible.
//
// Run:
//   cd tutorials/29-planner-executor/dotnet
//   dotnet run
//   dotnet run -- "help me find a photography gift under $200"

using System.ClientModel;
using System.ComponentModel;
using System.Text.Json.Serialization;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenAI;

namespace MafV1.Ch29.PlannerExecutor;

/// <summary>One step of a plan.</summary>
/// <param name="Step">1-based order of this step.</param>
/// <param name="Action">Short human-readable description of what it accomplishes.</param>
/// <param name="Query">
/// Catalogue search text, or null when the step only reasons over results
/// gathered by earlier steps. The nullability is the signal the executor uses
/// to decide whether to reach for a tool at all.
/// </param>
public sealed record PlanStep(
    [property: JsonPropertyName("step")] int Step,
    [property: JsonPropertyName("action")] string Action,
    [property: JsonPropertyName("query")] string? Query);

/// <summary>An ordered plan, produced before anything is executed.</summary>
public sealed record Plan(
    [property: JsonPropertyName("goal")] string Goal,
    [property: JsonPropertyName("steps")] IReadOnlyList<PlanStep> Steps);

/// <summary>A catalogue entry.</summary>
public sealed record CatalogItem(string Name, string Category, decimal Price, string Description);

public static class Program
{
    public const string DefaultRequest =
        "Help me put together a birthday gift for someone who likes photography, under $200.";

    public const string PlannerInstructions =
        "You are a planning assistant for an e-commerce store. Given a shopping request, "
        + "decompose it into a short ordered list of concrete steps needed to satisfy it — "
        + "typically: search the catalog for relevant products, narrow results by a constraint "
        + "such as price, pick the best candidates, and summarize a recommendation. "
        + "For any step that should search the product catalog, set `query` to the search text "
        + "for that step. For steps that only reason over results gathered by earlier steps "
        + "(filtering, picking, summarizing), leave `query` null. "
        + "Respond with the structured plan only — do not execute any step yourself.";

    public const string ExecutorInstructions =
        "You are an execution assistant for an e-commerce store, running one step of an "
        + "already-approved plan at a time. Each user message names the step to perform right "
        + "now. If the step needs to search the catalog, call the `search_products` tool. "
        + "Otherwise reason directly over the product results already visible earlier in this "
        + "conversation. Keep your answer to a few sentences and stay focused on this one step.";

    /// <summary>
    /// Toy in-memory catalogue. Deliberately self-contained — this chapter does
    /// not import chapter 24's RAG catalogue, so the two stay independent.
    /// </summary>
    public static readonly IReadOnlyList<CatalogItem> Catalog = new[]
    {
        new CatalogItem("Compact Mirrorless Camera", "photography", 189m, "Beginner-friendly mirrorless camera with a kit lens."),
        new CatalogItem("50mm Prime Lens", "photography", 129m, "Fast prime lens for portraits and low light."),
        new CatalogItem("Travel Camera Tripod", "photography", 39m, "Lightweight aluminum tripod, folds to 16 inches."),
        new CatalogItem("Padded Camera Strap", "photography", 19m, "Padded leather camera strap with quick-release buckles."),
        new CatalogItem("Professional Studio Light Kit", "photography", 349m, "Two-softbox studio lighting kit for indoor shoots."),
        new CatalogItem("Wireless Noise-Canceling Headphones", "audio", 179m, "Over-ear headphones with active noise cancellation."),
        new CatalogItem("Espresso Machine", "kitchen", 249m, "Semi-automatic espresso machine with a steam wand."),
    };

    [Description("Search the toy product catalog by keyword, optionally capped by an inclusive max price.")]
    public static string SearchProducts(
        [Description("Free-text search, matched against name/category/description.")] string query,
        [Description("Optional inclusive price ceiling in USD.")] double? maxPrice = null)
    {
        string[] terms = query.ToLowerInvariant().Split(' ', StringSplitOptions.RemoveEmptyEntries);

        List<CatalogItem> matches = Catalog
            .Where(item =>
                terms.Any(t => $"{item.Name} {item.Category} {item.Description}"
                    .Contains(t, StringComparison.OrdinalIgnoreCase))
                && (maxPrice is null || item.Price <= (decimal)maxPrice.Value))
            .ToList();

        if (matches.Count == 0)
        {
            string cap = maxPrice is null ? string.Empty : $" under ${maxPrice.Value:F0}";
            return $"No products found for '{query}'{cap}.";
        }

        return string.Join("\n", matches.Select(m => $"- {m.Name} (${m.Price:F0}): {m.Description}"));
    }

    // ─────────────── Agents ───────────────

    /// <summary>The planner: no tools, structured <see cref="Plan"/> output only.</summary>
    /// <remarks>
    /// The absence of tools is the design, not an omission. A planner holding a
    /// search tool will search — and then you have a reactive agent that also
    /// emits a plan it has already stopped following.
    /// </remarks>
    public static AIAgent BuildPlannerAgent(IChatClient chatClient) =>
        chatClient.AsAIAgent(instructions: PlannerInstructions, name: "planner-agent");

    /// <summary>The executor: one step at a time, with the catalogue tool.</summary>
    public static AIAgent BuildExecutorAgent(IChatClient chatClient) =>
        chatClient.AsAIAgent(
            instructions: ExecutorInstructions,
            name: "executor-agent",
            tools: new List<AITool> { AIFunctionFactory.Create(SearchProducts, "search_products") });

    // ─────────────── Plan and execute ───────────────

    /// <summary>Asks the planner for a structured plan.</summary>
    /// <exception cref="InvalidOperationException">
    /// The model did not return parseable JSON. Thrown rather than swallowed:
    /// executing a half-parsed plan is worse than not executing one.
    /// </exception>
    public static async Task<Plan> MakePlanAsync(AIAgent planner, string request)
    {
        var response = await planner
            .RunAsync<Plan>(request, serializerOptions: AIJsonUtilities.DefaultOptions)
            .ConfigureAwait(false);

        // AgentResponse<T>.Result deserializes lazily and throws on bad JSON.
        // Catching it here turns "the model ignored the schema" into one clear
        // message that quotes what it actually said, instead of a
        // JsonException from somewhere inside the framework.
        try
        {
            return response.Result
                   ?? throw new InvalidOperationException("planner returned a null plan");
        }
        catch (Exception ex) when (ex is not InvalidOperationException)
        {
            throw new InvalidOperationException(
                $"planner did not return a parseable plan; raw text: '{response.Text}'", ex);
        }
    }

    /// <summary>Executes exactly one step against the shared session.</summary>
    public static async Task<string> RunStepAsync(AIAgent executor, AgentSession session, PlanStep step)
    {
        string prompt = string.IsNullOrWhiteSpace(step.Query)
            ? $"Step {step.Step}: {step.Action}"
            : $"Step {step.Step}: {step.Action} Use search_products with query='{step.Query}'.";

        return (await executor.RunAsync(prompt, session).ConfigureAwait(false)).Text.Trim();
    }

    /// <summary>Plans the whole request up front, then executes each step in order.</summary>
    public static async Task<(Plan Plan, IReadOnlyList<string> Results)> RunPlanAsync(
        IChatClient chatClient,
        string request)
    {
        AIAgent planner = BuildPlannerAgent(chatClient);
        AIAgent executor = BuildExecutorAgent(chatClient);

        Plan plan = await MakePlanAsync(planner, request).ConfigureAwait(false);

        // One session for every step. This is what lets step 3 ("pick the best
        // candidate") see what step 1 found. Give each step its own session and
        // the later steps confabulate — plausibly, and without erroring.
        AgentSession session = await executor.CreateSessionAsync().ConfigureAwait(false);

        var results = new List<string>();
        foreach (PlanStep step in plan.Steps)
        {
            results.Add(await RunStepAsync(executor, session, step).ConfigureAwait(false));
        }

        return (plan, results);
    }

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();

        string request = args.Length > 0 ? args[0] : DefaultRequest;
        Console.WriteLine($"Request: {request}");
        Console.WriteLine();

        (Plan plan, IReadOnlyList<string> results) = await RunPlanAsync(BuildChatClient(), request);

        Console.WriteLine($"Plan: {plan.Goal}");
        for (int i = 0; i < plan.Steps.Count; i++)
        {
            Console.WriteLine();
            Console.WriteLine($"{plan.Steps[i].Step}. {plan.Steps[i].Action}");
            Console.WriteLine($"   -> {results[i]}");
        }

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
