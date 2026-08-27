using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using System.ComponentModel;

namespace ECommerceAgents.Shared.Tools;

/// <summary>
/// Who the caller is and what they have bought — the .NET twin of Python's
/// <c>shared/tools/user_tools.py</c>.
/// </summary>
/// <remarks>
/// <c>get_user_profile</c> was missing from <b>all five</b> .NET specialists,
/// making it the single highest-leverage gap in the tool audit (#18): every
/// agent that wants to personalise an answer — loyalty pricing, tier
/// benefits, "your recent orders" — had no way to ask who it was talking to
/// beyond the context block injected into its prompt.
///
/// Named <c>UserProfileTools</c> rather than <c>UserTools</c> to keep it
/// distinct at a glance from the per-specialist tool classes; the Python
/// counterpart is named in this docstring instead of mirrored exactly, since
/// the .NET tree already has several <c>*Tools</c> types and a collision here
/// is a maintenance hazard rather than a parity win.
///
/// Identity comes from <see cref="RequestContext.CurrentUserEmail"/>, never a
/// tool argument — the same "identity via ambient context, not threaded
/// parameters" rule the rest of this codebase follows, and the reason the
/// model cannot ask about somebody else's account by passing a different
/// email.
/// </remarks>
public sealed class UserProfileTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AgentTool.Create(GetUserProfile, nameof(GetUserProfile)),
        AgentTool.Create(GetPurchaseHistory, nameof(GetPurchaseHistory)),
    };

    [Description("Get the current user's profile including loyalty tier and spending history.")]
    public async Task<object> GetUserProfile()
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new { error = "No user context available" };
        }

        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT id, email, name, role, loyalty_tier, total_spend, created_at
              FROM users WHERE email = @email",
            new { email }
        );

        if (row is null)
        {
            return new { error = $"User not found: {email}" };
        }

        var tier = await conn.QueryFirstOrDefaultAsync(
            "SELECT discount_pct, free_shipping_threshold, priority_support FROM loyalty_tiers WHERE name = @name",
            new { name = (string)row.loyalty_tier }
        );

        return new
        {
            user_id = ((Guid)row.id).ToString(),
            email = (string)row.email,
            name = (string)row.name,
            role = (string)row.role,
            loyalty_tier = (string)row.loyalty_tier,
            total_spend = (decimal)row.total_spend,
            member_since = ((DateTime)row.created_at).ToString("o"),
            tier_benefits = tier is null
                ? null
                : (object)new
                {
                    discount_pct = tier.discount_pct is null ? 0m : (decimal)tier.discount_pct,
                    free_shipping_threshold = tier.free_shipping_threshold is null
                        ? (decimal?)null
                        : (decimal)tier.free_shipping_threshold,
                    priority_support = tier.priority_support is not null && (bool)tier.priority_support,
                },
        };
    }

    [Description("Get the current user's recent purchase history for personalized recommendations.")]
    public async Task<object> GetPurchaseHistory(
        [Description("Max number of orders to return")] int limit = 10
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            // Python returns an empty list rather than an error here, because
            // "no history" is a reasonable answer for an anonymous caller and
            // a recommendation prompt can carry on without it.
            return Array.Empty<object>();
        }

        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(
            @"SELECT o.id, o.status, o.total, o.created_at,
                     array_agg(DISTINCT p.category) AS categories,
                     array_agg(p.name) AS product_names
              FROM orders o
              JOIN users u ON o.user_id = u.id
              JOIN order_items oi ON oi.order_id = o.id
              JOIN products p ON oi.product_id = p.id
              WHERE u.email = @email
              GROUP BY o.id, o.status, o.total, o.created_at
              ORDER BY o.created_at DESC
              LIMIT @limit",
            new { email, limit = Math.Clamp(limit, 1, 50) }
        );

        return rows.Select(r => new
        {
            order_id = ((Guid)r.id).ToString(),
            status = (string)r.status,
            total = (decimal)r.total,
            date = ((DateTime)r.created_at).ToString("o"),
            categories = (string[])r.categories,
            products = (string[])r.product_names,
        }).ToList();
    }
}
