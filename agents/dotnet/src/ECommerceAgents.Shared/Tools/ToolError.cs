using System.Text.Json.Serialization;

namespace ECommerceAgents.Shared.Tools;

/// <summary>
/// What a tool returns when it cannot do what was asked.
/// </summary>
/// <remarks>
/// <para>
/// Serialises to <c>{"error": "..."}</c>, matching Python's convention
/// (<c>order_management/tools.py</c> returns
/// <c>{"error": f"Order not found or access denied: {order_id}"}</c>).
/// </para>
/// <para>
/// The .NET tools previously returned bare <c>null</c> in these cases, which is
/// correct C# and useless to a model: it learns that something did not happen
/// but not what or why, so it cannot recover. Observed end to end — the agent
/// called <c>get_order_details</c> without a UUID, got <c>null</c>, and told a
/// customer with eleven orders "there may be a temporary issue accessing your
/// order data". That is the same friendly lie this repo has now fixed four
/// times in different places.
/// </para>
/// <para>
/// <b>Not every empty result is an error.</b> <c>GetOrderTracking</c> on a
/// freshly-placed order legitimately has no tracking events, and returns a
/// populated record saying so — turning that into an error would be a
/// different lie. Only genuine failures use this type: an unparseable
/// identifier, a missing user context, or a row the caller may not see.
/// </para>
/// <para>
/// A message here is read by a model, so it should say what to do next, not
/// just what went wrong. "Order not found" is a dead end; naming the listing
/// tool that produces valid ids is a recovery.
/// </para>
/// </remarks>
/// <param name="Error">Human- and model-readable explanation, including the offending value.</param>
public sealed record ToolError([property: JsonPropertyName("error")] string Error)
{
    /// <summary>The caller's identity is missing, so no user-scoped query can run.</summary>
    /// <remarks>
    /// Deliberately does not say "log in": by the time a tool runs, the request
    /// already authenticated. This is an internal propagation failure, and
    /// telling the model to ask the customer to sign in would send them round a
    /// loop that cannot fix it.
    /// </remarks>
    public static ToolError NoUserContext(string tool) =>
        new($"{tool}: no user context is available on this request, so user-scoped data cannot be read. "
            + "This is a server-side problem — do not ask the customer to sign in again.");

    /// <summary>An identifier that is not a UUID.</summary>
    public static ToolError NotAnId(string tool, string parameter, string value, string listingTool) =>
        new($"{tool}: '{value}' is not a valid {parameter}. Call {listingTool} NOW to get the real "
            + $"identifier, then call {tool} again with it. Do not guess an identifier, and do not "
            + "ask the customer for it — they do not know it and you can look it up yourself.");

    /// <summary>A row that does not exist, or that this caller may not see.</summary>
    /// <remarks>
    /// The two are deliberately not distinguished. Telling a caller that a
    /// record exists but belongs to someone else is an enumeration oracle, and
    /// Python's message conflates them for the same reason.
    /// </remarks>
    public static ToolError NotFound(string tool, string what, string id) =>
        new($"{tool}: no {what} found with id {id}, or it is not accessible to this user. "
            + $"If that identifier came from earlier in the conversation it may be stale or "
            + $"truncated — look it up again rather than asking the customer to supply it.");
}
