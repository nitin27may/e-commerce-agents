namespace ECommerceAgents.Shared.Guardrails;

/// <summary>
/// Static guardrail policy: which tool results carry untrusted, user-
/// generated text — the .NET twin of Python's
/// <c>shared/guardrails/config.py::SANITIZE_TOOLS</c>.
/// </summary>
/// <remarks>
/// Only tools whose results include user-generated / stored text are
/// sanitized; everything else passes through untouched so structured/
/// numeric results are never mangled. Tool names match the strings passed
/// to <c>AIFunctionFactory.Create(method, nameof(method))</c> in each
/// specialist's tool list — i.e. the C# method name, not a snake_case
/// Python-style name.
///
/// Mapping value is the set of record-property names (PascalCase, matching
/// this codebase's C# property naming) to neutralize at any nesting depth;
/// <c>null</c> means neutralize every string reached in that tool's result.
/// Scoped to the same three specialists Python's table covers —
/// pricing-promotions and inventory-fulfillment carry no comparable free-
/// text fields in either stack.
/// </remarks>
public static class SanitizeToolsConfig
{
    public static readonly IReadOnlyDictionary<string, HashSet<string>?> SanitizeTools = new Dictionary<string, HashSet<string>?>
    {
        // review-sentiment: review bodies/titles are the top stored-injection vector
        ["GetProductReviews"] = ["Title", "Body", "Reviewer"],
        ["SearchReviews"] = ["Title", "Body", "Reviewer"],
        ["DetectFakeReviews"] = ["Title", "BodyPreview", "Reviewer", "Reason"],
        ["DraftSellerResponse"] = ["ReviewTitle", "ReviewBody", "Reviewer", "ResponseTemplate"],

        // product-discovery: descriptions/specs are seller-editable free text
        ["SearchProducts"] = ["Name", "Description"],
        ["GetProductDetails"] = ["Name", "Description"],
        ["FindSimilarProducts"] = ["Name", "Description"],
        ["SemanticSearch"] = ["Name", "Description"],
        ["GetTrendingProducts"] = ["Name", "Description"],
        ["CompareProducts"] = ["Name", "Description"],

        // order-management: free-text order notes / status history
        ["GetOrderDetails"] = ["Notes", "Location"],
        ["GetUserOrders"] = ["Notes"],
        ["GetOrderTracking"] = ["Notes", "Location"],
    };
}
