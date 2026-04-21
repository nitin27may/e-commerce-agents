using System.Text.Json;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// Mutable state carried through the pre-purchase workflow. Mirrors
/// the Python <c>ResearchState</c> dataclass in
/// <c>agents/python/workflows/pre_purchase.py</c> so tests across
/// stacks can assert the same shape.
/// </summary>
public sealed class ResearchState
{
    public ResearchState(string productId, string userRegion = "east")
    {
        ProductId = productId;
        UserRegion = userRegion;
    }

    public string ProductId { get; init; }
    public string UserRegion { get; init; }

    public JsonElement? Reviews { get; set; }
    public JsonElement? Stock { get; set; }
    public JsonElement? PriceHistory { get; set; }
    public JsonElement? Shipping { get; set; }

    public string Recommendation { get; set; } = "";
    public List<string> CompletedSteps { get; } = new();
    public List<string> Errors { get; } = new();
}
