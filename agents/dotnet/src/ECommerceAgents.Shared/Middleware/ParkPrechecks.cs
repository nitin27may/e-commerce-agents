using System.Text.Json;
using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Guardrails;
using ECommerceAgents.Shared.Logging;
using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.Middleware;

/// <summary>
/// Cheap deterministic checks that run BEFORE <see cref="HitlGate"/> writes an approval — the .NET twin of Python's
/// <c>shared/park_prechecks.py</c>.
/// </summary>
/// <remarks>
/// <para>
/// The gate used to open a <c>tool_approval_requests</c> row and its inbox approval before the tool's own role guard and its
/// status or ownership checks ran, so a customer asking for a backorder, or a cancel of a shipped order, parked an approval no
/// reviewer could ever make succeed. A non-null answer from here is the sentence the user reads: no row, nothing executed.
/// </para>
/// <para>
/// Two layers, in Python's order: the tool's declared roles (<see cref="RequiresRoleAttribute"/>) against the forwarded user's
/// role, then a per-tool precondition built from <see cref="GateRules"/>. A precheck never reveals another customer's data: an
/// order or return that is not the caller's, or does not exist, gets the same neutral sentence. A precheck that throws is the
/// caller's to fail closed on. Sentences are word for word Python's; tests pin them.
/// </para>
/// </remarks>
public static class ParkPrechecks
{
    public const string NotSubmitted = "I have not submitted anything for approval.";
    public const string OrderNotFound = "I could not find that order on your account. " + NotSubmitted;
    public const string ReturnNotFound = "I could not find that return on your account. " + NotSubmitted;

    /// <summary>Said when no identity was forwarded or a precheck or the write failed: nothing was submitted.</summary>
    public const string ParkFailedReply =
        "I could not submit that request for approval right now, so no changes have been made. Please try again in a moment.";

    /// <summary>Which layer refused: the role guard or a per-tool precondition.</summary>
    public enum RefusalKind { Role, Precondition }

    public sealed record Refusal(string Sentence, RefusalKind Kind);

    private delegate Task<string?> Check(DatabasePool pool, string email, JsonElement input);

    private static readonly Dictionary<string, string> Actions = new()
    {
        ["cancel_order"] = "cancel orders",
        ["modify_order"] = "change shipping addresses",
        ["initiate_return"] = "start returns",
        ["process_refund"] = "process refunds",
        ["place_backorder"] = "place backorders",
    };

    private static readonly Dictionary<string, Check> Checks = new()
    {
        ["cancel_order"] = CancelOrderAsync,
        ["modify_order"] = ModifyOrderAsync,
        ["initiate_return"] = InitiateReturnAsync,
        ["process_refund"] = ProcessRefundAsync,
        ["place_backorder"] = PlaceBackorderAsync,
    };

    /// <summary>Canonical (snake_case) names of the tools that have a precondition registered.</summary>
    public static IReadOnlyCollection<string> RegisteredTools => Checks.Keys;

    /// <summary>
    /// The sentence for a caller whose role the tool's roles exclude, else <c>null</c>. <c>admin</c> is always allowed (as in
    /// <see cref="RoleGuard"/>); nothing is refused when guardrails are off or the tool declares no roles.
    /// </summary>
    public static string? RoleRefusal(AgentSettings settings, string canonicalTool, IReadOnlyList<string>? roles, string? role, ILogger? logger = null)
    {
        if (!settings.GuardrailsEnabled || roles is null || RequiresRoleAttribute.Allows(roles, role ?? ""))
        {
            return null;
        }

        var allowed = roles.Append("admin").Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(r => r, StringComparer.Ordinal).ToList();
        logger?.LogWarning(
            "hitl.precheck_denied reason=role tool={Tool} role={Role}",
            LogSafe.Clean(canonicalTool),
            LogSafe.Clean(string.IsNullOrEmpty(role) ? "<none>" : role));
        var action = Actions.GetValueOrDefault(canonicalTool, canonicalTool.Replace('_', ' '));
        return $"Your account is not permitted to {action}; that is limited to {string.Join(", ", allowed)} accounts. {NotSubmitted}";
    }

    /// <summary>
    /// <c>null</c> when the call may be parked; otherwise the refusal that explains why it cannot succeed. Throws whatever a
    /// precondition's database access throws.
    /// </summary>
    public static async Task<Refusal?> CheckAsync(
        DatabasePool pool,
        AgentSettings settings,
        string canonicalTool,
        JsonElement input,
        string email,
        string? role,
        IReadOnlyList<string>? requiredRoles,
        ILogger? logger = null)
    {
        if (RoleRefusal(settings, canonicalTool, requiredRoles, role, logger) is { } roleSentence)
        {
            return new Refusal(roleSentence, RefusalKind.Role);
        }

        if (!Checks.TryGetValue(canonicalTool, out var check))
        {
            return null;
        }

        var sentence = await check(pool, email, input);
        if (sentence is null)
        {
            return null;
        }

        logger?.LogInformation("hitl.precheck_denied reason=precondition tool={Tool}", LogSafe.Clean(canonicalTool));
        return new Refusal(sentence, RefusalKind.Precondition);
    }

    // ───────────────────────── helpers ─────────────────────────

    /// <summary>The value at <paramref name="key"/> as a Guid, else <c>null</c> (a malformed id is simply not found).</summary>
    private static Guid? ValidGuid(JsonElement input, string key)
    {
        if (input.ValueKind != JsonValueKind.Object || !input.TryGetProperty(key, out var value))
        {
            return null;
        }

        var text = value.ValueKind == JsonValueKind.String ? value.GetString() : value.ToString();
        return Guid.TryParse(text?.Trim(), out var id) ? id : null;
    }

    private static int QuantityOf(JsonElement input)
    {
        if (input.ValueKind != JsonValueKind.Object || !input.TryGetProperty("quantity", out var value))
        {
            return 0;
        }

        return value.ValueKind switch
        {
            JsonValueKind.Number when value.TryGetDouble(out var d) && Math.Abs(d) < int.MaxValue => (int)d,
            JsonValueKind.String when int.TryParse(value.GetString()?.Trim(), out var n) => n,
            _ => 0,
        };
    }

    /// <summary>The caller's own order, or <c>null</c> when the id is malformed, missing or not theirs.</summary>
    private static async Task<(Guid Id, string Status)?> OwnedOrderAsync(DatabasePool pool, string email, JsonElement input)
    {
        if (ValidGuid(input, "order_id") is not { } id)
        {
            return null;
        }

        await using var conn = await pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.status FROM orders o JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email",
            new { id, email });
        return row is null ? null : ((Guid)row.id, (string)row.status);
    }

    // ───────────────────────── per-tool preconditions ─────────────────────────

    private static async Task<string?> CancelOrderAsync(DatabasePool pool, string email, JsonElement input)
    {
        if (await OwnedOrderAsync(pool, email, input) is not { } order)
        {
            return OrderNotFound;
        }

        return GateRules.CancelBlocker(order.Status) is { } reason
            ? $"Order {order.Id} {reason}, so it can no longer be cancelled. {NotSubmitted}"
            : null;
    }

    private static async Task<string?> ModifyOrderAsync(DatabasePool pool, string email, JsonElement input)
    {
        if (await OwnedOrderAsync(pool, email, input) is not { } order)
        {
            return OrderNotFound;
        }

        return GateRules.ModifyBlocker(order.Status) is { } reason
            ? $"Order {order.Id} {reason}, so its shipping address can no longer be changed. {NotSubmitted}"
            : null;
    }

    private static async Task<string?> InitiateReturnAsync(DatabasePool pool, string email, JsonElement input)
    {
        if (await OwnedOrderAsync(pool, email, input) is not { } order)
        {
            return OrderNotFound;
        }

        if (GateRules.ReturnStatusBlocker(order.Status) is { } reason)
        {
            return $"Order {order.Id} {reason}, so a return cannot be started for it. {NotSubmitted}";
        }

        await using var conn = await pool.OpenAsync();
        if (await conn.ExecuteScalarAsync<int?>("SELECT 1 FROM returns WHERE order_id = @id LIMIT 1", new { id = order.Id }) is not null)
        {
            return $"A return has already been started for order {order.Id}. {NotSubmitted}";
        }

        var days = await conn.ExecuteScalarAsync<int?>(
            @"SELECT EXTRACT(DAY FROM NOW() - timestamp)::int FROM order_status_history
              WHERE order_id = @id AND status = 'delivered'
              ORDER BY timestamp DESC LIMIT 1",
            new { id = order.Id });
        return GateRules.ReturnWindowExpired(days)
            ? $"Order {order.Id} was delivered {days} days ago, which is outside the {GateRules.ReturnWindowDays}-day return window. {NotSubmitted}"
            : null;
    }

    private static async Task<string?> ProcessRefundAsync(DatabasePool pool, string email, JsonElement input)
    {
        if (ValidGuid(input, "return_id") is not { } returnId)
        {
            return ReturnNotFound;
        }

        await using var conn = await pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT r.id, r.status FROM returns r JOIN users u ON r.user_id = u.id
              WHERE r.id = @id AND u.email = @email",
            new { id = returnId, email });
        if (row is null)
        {
            return ReturnNotFound;
        }

        return GateRules.RefundBlocker((string)row.status) is { } reason
            ? $"Return {(Guid)row.id} {reason}. {NotSubmitted}"
            : null;
    }

    private static async Task<string?> PlaceBackorderAsync(DatabasePool pool, string email, JsonElement input)
    {
        if (QuantityOf(input) <= 0)
        {
            return $"A backorder needs a quantity greater than zero. {NotSubmitted}";
        }

        var productId = ValidGuid(input, "product_id");
        await using var conn = await pool.OpenAsync();
        var product = productId is { } pid
            ? await conn.QueryFirstOrDefaultAsync("SELECT id, name FROM products WHERE id = @pid", new { pid })
            : null;
        if (product is null)
        {
            return $"I could not find that product. {NotSubmitted}";
        }

        var stock = await conn.ExecuteScalarAsync<long>(
            "SELECT COALESCE(SUM(quantity), 0) FROM warehouse_inventory WHERE product_id = @pid", new { pid = productId });
        return stock > 0
            ? $"{(string)product.name} is in stock ({stock} units available), so no backorder is needed. {NotSubmitted}"
            : null;
    }
}
