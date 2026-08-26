// MAF v1 — Chapter 24: RAG and Grounding (.NET)
//
// Two mechanisms, deliberately kept separate, because conflating them is the
// most common mistake in this area:
//
//   1. Retrieval — search_products is a tool the agent calls to read real data
//      instead of relying on whatever the model's training data "remembers".
//      Naive keyword match; the point is not search quality, it is that
//      retrieval exists at all.
//   2. Grounding verification — VerifyClaims runs AFTER the model answers. It
//      extracts the product ids and prices the answer claims and checks them
//      against the same catalogue.
//
// Retrieval only guarantees the model SAW the truth. Nothing stops its final
// sentence from citing the wrong id or rounding a price, and a rounded price
// in a confident sentence is indistinguishable from a correct one. Verification
// is the separate, after-the-fact step that closes that gap — which is why
// "we do RAG" is not the same claim as "our answers are grounded".
//
// No pgvector, no Postgres — see agents/python/product_discovery/tools.py
// (semantic_search) and agents/python/shared/grounding/verifier.py
// (verify_claims) for the production versions this mirrors at toy scale.
//
// Run:
//   cd tutorials/24-rag-and-grounding/dotnet
//   dotnet run
//   dotnet run -- "Do you have noise-cancelling headphones?"

using System.ClientModel;
using System.ComponentModel;
using System.Text.RegularExpressions;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenAI;

namespace MafV1.Ch24.Rag;

/// <summary>A catalogue row — the source of truth.</summary>
public sealed record CatalogProduct(string Id, string Name, decimal Price, string Category);

/// <summary>A product id the model's prose claimed, plus a nearby price if it stated one.</summary>
public sealed record ProductClaim(string Id, decimal? Price);

/// <summary>How a claim fared against the catalogue.</summary>
public enum ClaimStatus
{
    Verified,
    PriceMismatch,
    NotFound,
}

/// <summary>One claim's verdict.</summary>
public sealed record ClaimVerdict(string Identifier, ClaimStatus Status, string? Detail = null);

/// <summary>Every verdict for one answer.</summary>
public sealed record GroundingReport(IReadOnlyList<ClaimVerdict> Verdicts)
{
    public int TotalCount => Verdicts.Count;

    public int VerifiedCount => Verdicts.Count(v => v.Status == ClaimStatus.Verified);

    /// <summary>
    /// True when every claim checked out.
    /// </summary>
    /// <remarks>
    /// An answer that made NO claims is vacuously grounded. That is correct —
    /// "we have three colours in stock" cites nothing and so cannot be
    /// contradicted by the catalogue — but it is also why a grounding rate is
    /// not a quality score. An agent that never cites anything scores 100%.
    /// </remarks>
    public bool AllVerified => Verdicts.All(v => v.Status == ClaimStatus.Verified);
}

public static partial class Program
{
    public const string Instructions =
        "You are a shopping assistant for a small store. "
        + "When the user asks about products, call the `search_products` tool — never answer "
        + "from memory. When you mention a product in your answer, always include its exact "
        + "product id (e.g. 'P001') and its exact price, copied verbatim from the tool result, "
        + "not rounded or paraphrased. For other questions, answer directly in one short sentence.";

    public const string DefaultQuestion =
        "Do you have any noise-cancelling headphones? What's the price and product id?";

    /// <summary>
    /// A handful of records standing in for a real product table. Production
    /// uses Postgres + pgvector; the mechanics this chapter teaches — a search
    /// tool, then a verification step — do not depend on that.
    /// </summary>
    public static readonly IReadOnlyList<CatalogProduct> Catalog = new[]
    {
        new CatalogProduct("P001", "Wireless Noise-Cancelling Headphones", 129.99m, "Electronics"),
        new CatalogProduct("P002", "Stainless Steel Water Bottle", 24.50m, "Home"),
        new CatalogProduct("P003", "Organic Cotton Hoodie", 54.00m, "Clothing"),
        new CatalogProduct("P004", "Bluetooth Portable Speaker", 39.99m, "Electronics"),
        new CatalogProduct("P005", "Yoga Mat with Carry Strap", 19.95m, "Sports"),
    };

    // ─────────────── Retrieval ───────────────

    /// <summary>
    /// Naive substring match over name + category — no ranking, no embeddings.
    /// </summary>
    [Description("Search the product catalog by keyword. Returns matching products with id, name, and price.")]
    public static string SearchProducts(
        [Description("Keyword(s) to match against product name or category, e.g. 'headphones'.")] string query)
    {
        List<CatalogProduct> matches = Search(query);

        return matches.Count == 0
            ? $"No products matched '{query}'."
            : string.Join("\n", matches.Select(p => $"{p.Id}: {p.Name} — ${p.Price:F2} ({p.Category})"));
    }

    /// <summary>The same match, typed, for tests and for callers that want rows.</summary>
    public static List<CatalogProduct> Search(string query, IReadOnlyList<CatalogProduct>? catalog = null)
    {
        string[] words = query.ToLowerInvariant().Split(' ', StringSplitOptions.RemoveEmptyEntries);

        return (catalog ?? Catalog)
            .Where(p => words.Any(w =>
                $"{p.Name} {p.Category}".Contains(w, StringComparison.OrdinalIgnoreCase)))
            .ToList();
    }

    // ─────────────── Verification ───────────────

    private const decimal PriceTolerance = 0.01m;

    [GeneratedRegex(@"\bP0\d{2}\b")]
    private static partial Regex IdPattern();

    [GeneratedRegex(@"\$(\d+(?:\.\d{1,2})?)")]
    private static partial Regex PricePattern();

    /// <summary>
    /// Pulls out every product id the answer claims, plus a nearby price.
    /// </summary>
    /// <remarks>
    /// Deliberately dumb. A real extractor parses structured card payloads, not
    /// free text with a regex — see agents/python/shared/grounding/extractor.py.
    /// This is enough to demonstrate the SHAPE of the problem: the model's prose
    /// can drift from what the tool actually returned.
    ///
    /// The 40-character window after an id is the heuristic, and it is the part
    /// most likely to be wrong on real prose. It is generous enough for
    /// "P001 costs $129.99" and tight enough not to attach the next product's
    /// price to this one.
    /// </remarks>
    public static List<ProductClaim> ExtractClaims(string answer)
    {
        var claims = new List<ProductClaim>();

        foreach (Match match in IdPattern().Matches(answer))
        {
            int start = match.Index + match.Length;
            int length = Math.Min(40, answer.Length - start);
            string window = length > 0 ? answer.Substring(start, length) : string.Empty;

            Match price = PricePattern().Match(window);
            claims.Add(new ProductClaim(
                match.Value,
                price.Success ? decimal.Parse(price.Groups[1].Value) : null));
        }

        return claims;
    }

    /// <summary>
    /// Checks each claimed id and price against the catalogue.
    /// </summary>
    /// <remarks>
    /// The step retrieval alone does not give you. A price claim only counts as
    /// a mismatch when the model actually stated one — an answer that names a
    /// product without quoting a price is not wrong, it is just less specific.
    /// </remarks>
    public static GroundingReport VerifyClaims(
        IEnumerable<ProductClaim> claims,
        IReadOnlyList<CatalogProduct>? catalog = null)
    {
        Dictionary<string, CatalogProduct> byId =
            (catalog ?? Catalog).ToDictionary(p => p.Id, StringComparer.OrdinalIgnoreCase);

        var verdicts = new List<ClaimVerdict>();

        foreach (ProductClaim claim in claims)
        {
            if (!byId.TryGetValue(claim.Id, out CatalogProduct? product))
            {
                verdicts.Add(new ClaimVerdict(
                    claim.Id, ClaimStatus.NotFound, "no product with this id in the catalog"));
                continue;
            }

            if (claim.Price is not null && Math.Abs(claim.Price.Value - product.Price) >= PriceTolerance)
            {
                verdicts.Add(new ClaimVerdict(
                    claim.Id,
                    ClaimStatus.PriceMismatch,
                    $"catalog price is ${product.Price:F2}, not ${claim.Price.Value:F2}"));
                continue;
            }

            verdicts.Add(new ClaimVerdict(claim.Id, ClaimStatus.Verified));
        }

        return new GroundingReport(verdicts);
    }

    /// <summary>Extract and verify in one step — what a caller usually wants.</summary>
    public static GroundingReport VerifyAnswer(string answer, IReadOnlyList<CatalogProduct>? catalog = null) =>
        VerifyClaims(ExtractClaims(answer), catalog);

    // ─────────────── Agent ───────────────

    public static AIAgent BuildAgent(IChatClient chatClient) =>
        chatClient.AsAIAgent(
            instructions: Instructions,
            name: "grounded-shopping-agent",
            tools: new List<AITool> { AIFunctionFactory.Create(SearchProducts, "search_products") });

    public static async Task<string> AskAsync(AIAgent agent, string question) =>
        (await agent.RunAsync(question).ConfigureAwait(false)).Text;

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();

        string question = args.Length > 0 ? args[0] : DefaultQuestion;
        string answer = await AskAsync(BuildAgent(BuildChatClient()), question);

        Console.WriteLine($"Q: {question}");
        Console.WriteLine($"A: {answer}");

        GroundingReport report = VerifyAnswer(answer);
        Console.WriteLine($"Grounding: {report.VerifiedCount}/{report.TotalCount} claims verified");

        foreach (ClaimVerdict verdict in report.Verdicts.Where(v => v.Status != ClaimStatus.Verified))
        {
            Console.WriteLine($"  ! {verdict.Identifier}: {verdict.Status} ({verdict.Detail})");
        }

        return report.AllVerified ? 0 : 1;
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
