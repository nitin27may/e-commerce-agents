// MAF v1 — Chapter 27: Agent-as-tool (.NET)
//
// Wrap a small, single-purpose "product-lookup" agent as an AIFunction via
// AIAgentExtensions.AsAIFunction(...) and hand it to a "coordinator" agent's
// own toolset. No network hop, no handoff mesh — just an agent presented to
// another agent exactly the way any ordinary function tool would be.
//
// The distinction worth holding on to, since chapter 14 covered the other one:
//
//   Handoff       transfers control. The specialist takes over the
//                 conversation and answers the user directly.
//   Agent-as-tool keeps control. The coordinator calls the specialist, gets a
//                 string back, and carries on — free to call other tools and
//                 compose the results into one answer. The user never learns a
//                 second agent existed.
//
// So agent-as-tool is the right shape when you want composition, and handoff
// is right when you want delegation. Reaching for a handoff mesh where a tool
// would do is how a two-agent system acquires a routing problem it did not
// need to have.
//
// Run:
//   cd tutorials/27-agent-as-tool/dotnet
//   dotnet run
//   dotnet run -- "Look up the Wireless Headphones, then tell me the price after a 20% discount."

using System.ClientModel;
using System.ComponentModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenAI;

namespace MafV1.Ch27.AgentAsTool;

/// <summary>One catalogue entry.</summary>
public sealed record CatalogItem(string Sku, decimal Price, string Category, int Stock);

public static class Program
{
    // ─────────────── In-memory product catalogue ───────────────

    public static readonly IReadOnlyDictionary<string, CatalogItem> Catalog =
        new Dictionary<string, CatalogItem>(StringComparer.OrdinalIgnoreCase)
        {
            ["wireless headphones"] = new("SKU-1001", 149.99m, "Electronics", 42),
            ["running shoes"] = new("SKU-2044", 89.50m, "Sports", 17),
            ["coffee maker"] = new("SKU-3310", 64.00m, "Home", 0),
            ["yoga mat"] = new("SKU-4477", 24.99m, "Sports", 120),
        };

    public const string ProductLookupInstructions =
        "You are a product-lookup specialist. When asked about a product, call the "
        + "`search_catalog` tool with the product name and report back its price, "
        + "category, and stock level in one short sentence. Do not answer anything else.";

    public const string CoordinatorInstructions =
        "You are a shopping assistant coordinator. When the user asks about a product, "
        + "call the `product_lookup` tool with a short task description to get its details. "
        + "If the user also asks about a discount, call the `calculate_discount` tool with "
        + "the price you got back and the requested percentage, then combine both results "
        + "into one final answer. Never guess a price yourself — always use the tools.";

    public const string DefaultQuestion =
        "Look up the Wireless Headphones, then tell me the price after a 20% discount.";

    // ─────────────── Tools ───────────────

    /// <summary>The specialist's own tool — an ordinary function tool, nothing special.</summary>
    [Description("Look up a product in the catalog by name.")]
    public static string SearchCatalog(
        [Description("The product name to look up, e.g. 'Wireless Headphones'.")] string name)
    {
        if (!Catalog.TryGetValue(name.Trim(), out CatalogItem? item))
        {
            return $"No catalog entry for '{name}'.";
        }

        return $"{name}: ${item.Price:F2}, category {item.Category}, {item.Stock} in stock.";
    }

    /// <summary>
    /// An ordinary local tool the coordinator calls directly, after the wrapped
    /// agent has answered and handed control back.
    /// </summary>
    [Description("Compute a price after a percentage discount.")]
    public static string CalculateDiscount(
        [Description("The original price.")] double price,
        [Description("The discount percentage, e.g. 20 for 20%.")] double percent)
    {
        double discounted = price * (1 - (percent / 100));
        return $"${discounted:F2} (after {percent:F0}% off ${price:F2})";
    }

    // ─────────────── Agents ───────────────

    /// <summary>The small, well-scoped specialist that gets wrapped as a tool.</summary>
    public static AIAgent BuildProductLookupAgent(IChatClient chatClient) =>
        chatClient.AsAIAgent(
            instructions: ProductLookupInstructions,
            name: "product-lookup-agent",
            description: "Looks up product price, category, and stock in the catalog.",
            tools: new List<AITool> { AIFunctionFactory.Create(SearchCatalog, "search_catalog") });

    /// <summary>
    /// The coordinator — the agent this chapter drives directly.
    /// </summary>
    /// <remarks>
    /// Both agents share one chat client, so the demo needs only one provider
    /// and one credential. That also means the specialist's turns are billed to
    /// the same budget as the coordinator's; agent-as-tool is not free, it just
    /// hides the second agent from the user rather than from the invoice.
    /// </remarks>
    public static AIAgent BuildAgent(IChatClient chatClient)
    {
        AIAgent productLookup = BuildProductLookupAgent(chatClient);

        // The wrap. The coordinator sees an AIFunction like any other; it has
        // no idea there is an agent (and a second model call) behind it.
        AIFunction productLookupTool = productLookup.AsAIFunction(new AIFunctionFactoryOptions
        {
            Name = "product_lookup",
            Description = "Delegate a product question to the product-lookup specialist agent.",
        });

        return chatClient.AsAIAgent(
            instructions: CoordinatorInstructions,
            name: "coordinator-agent",
            tools: new List<AITool>
            {
                productLookupTool,
                AIFunctionFactory.Create(CalculateDiscount, "calculate_discount"),
            });
    }

    public static async Task<string> AskAsync(AIAgent agent, string question) =>
        (await agent.RunAsync(question).ConfigureAwait(false)).Text;

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();

        string question = args.Length > 0 ? args[0] : DefaultQuestion;
        AIAgent agent = BuildAgent(BuildChatClient());

        Console.WriteLine($"Q: {question}");
        Console.WriteLine($"A: {await AskAsync(agent, question)}");

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
