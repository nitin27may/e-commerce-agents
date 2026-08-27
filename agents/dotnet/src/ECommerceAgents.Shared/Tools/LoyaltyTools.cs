using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using System.ComponentModel;

namespace ECommerceAgents.Shared.Tools;

/// <summary>
/// Loyalty tier, discount and benefit comparison — the .NET twin of Python's
/// <c>shared/tools/loyalty_tools.py</c> (#18).
/// </summary>
/// <remarks>
/// Tier data was previously hardcoded inside <c>PricingTools.OptimizeCart</c>,
/// so a customer could be quoted a loyalty discount but had no way to ask what
/// tier they were on or what it entitled them to. These read the
/// <c>loyalty_tiers</c> table rather than repeating those constants, so the
/// two can't drift.
/// </remarks>
public sealed class LoyaltyTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AgentTool.Create(GetLoyaltyTier, nameof(GetLoyaltyTier)),
        AgentTool.Create(CalculateLoyaltyDiscount, nameof(CalculateLoyaltyDiscount)),
        AgentTool.Create(GetLoyaltyBenefits, nameof(GetLoyaltyBenefits)),
    };

    [Description("Get the current user's loyalty tier and associated benefits.")]
    public async Task<object> GetLoyaltyTier()
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new { error = "No user context available" };
        }

        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT u.loyalty_tier, u.total_spend,
                     lt.discount_pct, lt.free_shipping_threshold, lt.priority_support, lt.min_spend
              FROM users u
              LEFT JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
              WHERE u.email = @email",
            new { email }
        );

        if (row is null)
        {
            return new { error = $"User not found: {email}" };
        }

        return new
        {
            tier = (string)row.loyalty_tier,
            total_spend = (decimal)row.total_spend,
            discount_pct = row.discount_pct is null ? 0m : (decimal)row.discount_pct,
            free_shipping_threshold = row.free_shipping_threshold is null
                ? (decimal?)null
                : (decimal)row.free_shipping_threshold,
            priority_support = row.priority_support is not null && (bool)row.priority_support,
            min_spend = row.min_spend is null ? 0m : (decimal)row.min_spend,
        };
    }

    [Description("Calculate the loyalty discount amount for a given cart total based on the current user's tier.")]
    public async Task<object> CalculateLoyaltyDiscount(
        [Description("Cart total before discount")] decimal cartTotal
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new { error = "No user context available" };
        }

        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT u.loyalty_tier, lt.discount_pct, lt.free_shipping_threshold
              FROM users u
              LEFT JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
              WHERE u.email = @email",
            new { email }
        );

        if (row is null)
        {
            return new { error = $"User not found: {email}" };
        }

        var pct = row.discount_pct is null ? 0m : (decimal)row.discount_pct;
        var discount = Math.Round(cartTotal * pct / 100m, 2);
        var threshold = row.free_shipping_threshold is null ? (decimal?)null : (decimal)row.free_shipping_threshold;

        return new
        {
            tier = (string)row.loyalty_tier,
            cart_total = cartTotal,
            discount_pct = pct,
            discount_amount = discount,
            total_after_discount = Math.Round(cartTotal - discount, 2),
            // A threshold of 0 means "always free", which is how gold is
            // seeded — treating it as "spend more than nothing" would be
            // technically true and useless to say.
            free_shipping = threshold is not null && cartTotal >= threshold,
        };
    }

    [Description("Compare all loyalty tiers (bronze, silver, gold) and their benefits.")]
    public async Task<object> GetLoyaltyBenefits()
    {
        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(
            @"SELECT name, min_spend, discount_pct, free_shipping_threshold, priority_support
              FROM loyalty_tiers ORDER BY min_spend"
        );

        return rows.Select(r => new
        {
            tier = (string)r.name,
            min_spend = (decimal)r.min_spend,
            discount_pct = (decimal)r.discount_pct,
            free_shipping_threshold = r.free_shipping_threshold is null
                ? (decimal?)null
                : (decimal)r.free_shipping_threshold,
            priority_support = (bool)r.priority_support,
        }).ToList();
    }
}
