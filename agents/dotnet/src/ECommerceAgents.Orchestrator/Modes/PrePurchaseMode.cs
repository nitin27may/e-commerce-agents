using Dapper;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Workflows;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace ECommerceAgents.Orchestrator.Modes;

/// <summary>
/// Fan-out/fan-in product research: reviews, stock and price history are
/// gathered in parallel, merged, and synthesised into a recommendation.
/// Wraps <see cref="PrePurchaseWorkflow"/>, a real MAF
/// <c>WorkflowBuilder</c> graph. Mirrors Python's <c>PrePurchaseMode</c>.
/// </summary>
/// <remarks>
/// The workflow takes a product id; a chat message is free text. So this
/// resolves one the same way Python's mode does — a UUID literal if the
/// message contains one, otherwise a name lookup against the catalog. If
/// neither finds a product the mode says so rather than running the graph
/// against a guess, which would produce four confidently empty nodes.
/// </remarks>
public sealed partial class PrePurchaseMode(DatabasePool pool) : IOrchestrationMode
{
    private readonly DatabasePool _pool = pool;

    public string Name => "workflow:pre-purchase";
    public string Label => "Pre-Purchase Research (fan-out/fan-in)";
    public string Description =>
        "Gathers reviews, stock and price history in parallel, then synthesises a buy recommendation.";

    public ModeCapabilities Capabilities => new(
        Streams: false,
        SupportsHitl: false,
        SupportsCheckpoints: true,
        IsGraph: true
    );

    /// <summary>
    /// Node ids match the workflow's real executor ids with dashes swapped for
    /// underscores. That convention is what lets the UI correlate a live
    /// <c>node</c> event to a node in this diagram — the same contract Python's
    /// modes follow, and the reason these aren't just decorative labels.
    /// </summary>
    public string? GraphMermaid() => """
        graph TD
            fan_out[Fan out] --> reviews[Reviews]
            fan_out --> stock[Stock]
            fan_out --> price_history[Price history]
            reviews --> merge[Merge]
            stock --> merge
            price_history --> merge
            merge --> synthesis[Synthesis]
        """;

    [GeneratedRegex(@"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")]
    private static partial Regex UuidPattern();

    public async Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default)
    {
        var productId = await ResolveProductIdAsync(message, ct);
        if (productId is null)
        {
            return new ModeRunResult(
                "I couldn't work out which product you mean. Tell me the product name or id and I'll research it.",
                ["orchestrator"],
                0
            );
        }

        var workflow = new PrePurchaseWorkflow(new PrePurchaseTools(_pool));
        var state = await workflow.ExecuteAsync(new ResearchState(productId), ct);

        return new ModeRunResult(
            Summarise(state),
            ["product-discovery", "review-sentiment", "inventory-fulfillment", "pricing-promotions"],
            state.CompletedSteps.Count
        );
    }

    private async Task<string?> ResolveProductIdAsync(string message, CancellationToken ct)
    {
        var literal = UuidPattern().Match(message);
        if (literal.Success)
        {
            return literal.Value;
        }

        await using var conn = await _pool.OpenAsync();

        // Same word-splitting the catalog search uses, so "Sony headphones"
        // still resolves "Sony WH-1000XM5".
        var words = message
            .Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(w => w.Length >= 3)
            .Select(w => w.Trim(',', '.', '?', '!'))
            .Where(w => w.Length >= 3)
            .Take(6)
            .ToList();

        foreach (var word in words)
        {
            var hit = await conn.ExecuteScalarAsync<Guid?>(
                "SELECT id FROM products WHERE is_active = TRUE AND name ILIKE @pattern LIMIT 1",
                new { pattern = $"%{word}%" }
            );
            if (hit is not null)
            {
                return hit.Value.ToString();
            }
        }

        return null;
    }

    /// <summary>
    /// The workflow yields structured state; chat needs prose. Kept in the
    /// mode rather than the workflow so the graph stays reusable by anything
    /// that wants the data rather than a sentence.
    /// </summary>
    private static string Summarise(ResearchState state)
    {
        if (!string.IsNullOrWhiteSpace(state.Recommendation))
        {
            return state.Recommendation;
        }

        var parts = new List<string>();

        if (Read(state.Stock, "in_stock") is { } inStock)
        {
            var qty = Read(state.Stock, "total_quantity") ?? "0";
            parts.Add(inStock == "True" || inStock == "true"
                ? $"In stock ({qty} units across warehouses)."
                : "Currently out of stock.");
        }

        if (Read(state.Reviews, "average_rating") is { } rating)
        {
            var count = Read(state.Reviews, "review_count") ?? "0";
            parts.Add($"Rated {rating} from {count} reviews.");
        }

        if (Read(state.PriceHistory, "trend") is { } trend)
        {
            var current = Read(state.PriceHistory, "current_price");
            parts.Add(current is null
                ? $"Price trend is {trend}."
                : $"Currently ${current}, with a {trend} price trend.");
        }

        if (state.Errors.Count > 0)
        {
            parts.Add($"({state.Errors.Count} research step(s) failed.)");
        }

        return parts.Count > 0
            ? string.Join(" ", parts)
            : "I researched this product but couldn't retrieve enough detail to make a recommendation.";
    }

    private static string? Read(JsonElement? element, string property) =>
        element is { } e && e.ValueKind == JsonValueKind.Object && e.TryGetProperty(property, out var value)
            ? value.ToString()
            : null;
}
