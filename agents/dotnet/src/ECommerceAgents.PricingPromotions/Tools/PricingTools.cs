using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using System.ComponentModel;
using System.Text.Json;

namespace ECommerceAgents.PricingPromotions.Tools;

/// <summary>
/// MAF tools for the PricingPromotions specialist. Mirrors
/// <c>agents/python/pricing_promotions/tools.py</c> 1:1 — same SQL,
/// same coupon/promotion/loyalty logic.
/// </summary>
public sealed class PricingTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AIFunctionFactory.Create(ValidateCoupon, nameof(ValidateCoupon)),
        AIFunctionFactory.Create(OptimizeCart, nameof(OptimizeCart)),
        AIFunctionFactory.Create(GetActiveDeals, nameof(GetActiveDeals)),
        AIFunctionFactory.Create(CheckBundleEligibility, nameof(CheckBundleEligibility)),
    };

    // ─────────────────────── validate_coupon ──────────────────

    [Description("Validate a coupon code. Checks expiry, min spend, usage limit, applicable categories, and user-specific restrictions.")]
    public async Task<CouponValidationResult> ValidateCoupon(
        [Description("Coupon code to validate")] string code,
        [Description("Current cart total before discount")] decimal cartTotal,
        [Description("Product category to check applicability")] string? category = null
    )
    {
        var email = RequestContext.CurrentUserEmail;
        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT id, code, description, discount_type, discount_value,
                     min_spend, max_discount, usage_limit, times_used,
                     valid_from, valid_until, applicable_categories,
                     user_specific_email, is_active
              FROM coupons WHERE UPPER(code) = UPPER(@code)",
            new { code }
        );
        if (row is null)
        {
            return CouponValidationResult.Invalid($"Coupon '{code}' not found");
        }

        if (!(bool)row.is_active)
        {
            return CouponValidationResult.Invalid("Coupon is no longer active", code);
        }

        var now = DateTime.UtcNow;
        if (row.valid_until is not null)
        {
            var validUntil = (DateTime)row.valid_until;
            if (now > validUntil)
            {
                return CouponValidationResult.Invalid("Coupon has expired", code);
            }
        }
        if (row.valid_from is not null && now < (DateTime)row.valid_from)
        {
            return CouponValidationResult.Invalid("Coupon is not yet valid", code);
        }

        if (row.usage_limit is not null && (int)row.times_used >= (int)row.usage_limit)
        {
            return CouponValidationResult.Invalid("Coupon usage limit reached", code);
        }

        var minSpend = row.min_spend is null ? 0m : (decimal)row.min_spend;
        if (minSpend > 0m && cartTotal < minSpend)
        {
            return CouponValidationResult.Invalid(
                $"Minimum spend of ${minSpend:F2} not met (cart: ${cartTotal:F2})",
                code
            );
        }

        var applicableCategories = row.applicable_categories as string[];
        if (applicableCategories is { Length: > 0 } && !string.IsNullOrEmpty(category))
        {
            if (!applicableCategories.Contains(category))
            {
                return CouponValidationResult.Invalid(
                    $"Coupon not valid for category '{category}'. Valid for: {string.Join(", ", applicableCategories)}",
                    code
                );
            }
        }

        var userSpecific = (string?)row.user_specific_email;
        if (!string.IsNullOrEmpty(userSpecific) && userSpecific != email)
        {
            return CouponValidationResult.Invalid("This coupon is restricted to a specific user", code);
        }

        var discountType = (string)row.discount_type;
        var discountValue = (decimal)row.discount_value;
        decimal discountAmount;
        if (discountType == "percentage")
        {
            discountAmount = cartTotal * (discountValue / 100m);
            if (row.max_discount is not null)
            {
                discountAmount = Math.Min(discountAmount, (decimal)row.max_discount);
            }
        }
        else
        {
            discountAmount = Math.Min(discountValue, cartTotal);
        }

        return new CouponValidationResult(
            Valid: true,
            Error: null,
            Code: (string)row.code,
            Description: (string?)row.description,
            DiscountType: discountType,
            DiscountValue: discountValue,
            DiscountAmount: Math.Round(discountAmount, 2),
            NewTotal: Math.Round(cartTotal - discountAmount, 2),
            ApplicableCategories: applicableCategories?.ToList()
        );
    }

    // ─────────────────────── optimize_cart ────────────────────

    [Description("Find the best combination of coupons, promotions, and loyalty discounts for a cart. Returns the optimal savings breakdown.")]
    public async Task<OptimizeCartResult> OptimizeCart(
        [Description("List of items: each {product_id, quantity}")] List<CartItemInput> items
    )
    {
        var email = RequestContext.CurrentUserEmail;
        await using var conn = await _pool.OpenAsync();

        var cartItems = new List<CartLineItem>();
        foreach (var input in items)
        {
            if (!Guid.TryParse(input.ProductId, out var pid))
            {
                return OptimizeCartResult.Failure($"Product not found: {input.ProductId}");
            }
            var product = await conn.QueryFirstOrDefaultAsync(
                "SELECT id, name, price, category FROM products WHERE id = @pid",
                new { pid }
            );
            if (product is null)
            {
                return OptimizeCartResult.Failure($"Product not found: {input.ProductId}");
            }
            var qty = Math.Max(1, input.Quantity);
            var price = (decimal)product.price;
            cartItems.Add(new CartLineItem(
                ProductId: ((Guid)product.id).ToString(),
                Name: (string)product.name,
                Price: price,
                Category: (string)product.category,
                Quantity: qty,
                Subtotal: price * qty
            ));
        }

        var originalTotal = cartItems.Sum(i => i.Subtotal);
        var categories = cartItems.Select(i => i.Category).Distinct().ToList();
        var productIds = cartItems.Select(i => i.ProductId).ToList();
        var savings = new List<SavingsLine>();

        // 1. Find best applicable coupon.
        var coupons = await conn.QueryAsync(
            @"SELECT code, description, discount_type, discount_value,
                     min_spend, max_discount, applicable_categories,
                     user_specific_email
              FROM coupons
              WHERE is_active = TRUE
                AND (valid_until IS NULL OR valid_until > NOW())
                AND valid_from <= NOW()
                AND (usage_limit IS NULL OR times_used < usage_limit)
                AND (user_specific_email IS NULL OR user_specific_email = @email)
              ORDER BY discount_value DESC",
            new { email }
        );

        decimal bestCouponSavings = 0m;
        dynamic? bestCoupon = null;
        foreach (var c in coupons)
        {
            var minSpend = c.min_spend is null ? 0m : (decimal)c.min_spend;
            if (minSpend > 0m && originalTotal < minSpend) continue;

            var applicable = c.applicable_categories as string[];
            if (applicable is { Length: > 0 } && !categories.Any(cat => applicable.Contains(cat))) continue;

            decimal amount;
            if ((string)c.discount_type == "percentage")
            {
                amount = originalTotal * ((decimal)c.discount_value / 100m);
                if (c.max_discount is not null)
                {
                    amount = Math.Min(amount, (decimal)c.max_discount);
                }
            }
            else
            {
                amount = Math.Min((decimal)c.discount_value, originalTotal);
            }
            if (amount > bestCouponSavings)
            {
                bestCouponSavings = amount;
                bestCoupon = c;
            }
        }

        if (bestCoupon is not null)
        {
            savings.Add(new SavingsLine(
                Type: "coupon",
                Code: (string)bestCoupon.code,
                Name: null,
                Description: (string?)bestCoupon.description,
                Product: null,
                Tier: null,
                DiscountPct: null,
                Amount: Math.Round(bestCouponSavings, 2)
            ));
        }

        // 2. Promotions
        var promos = await conn.QueryAsync(
            @"SELECT name, type, rules
              FROM promotions
              WHERE is_active = TRUE
                AND start_date <= NOW()
                AND end_date >= NOW()"
        );

        foreach (var promo in promos)
        {
            // Coerce dynamic Dapper outputs to typed locals before any
            // LINQ/lambda use, otherwise the C# compiler dispatches the
            // lambda dynamically and refuses to bind it.
            Dictionary<string, object> rules = ParseRules((object?)promo.rules);
            string promoType = (string)promo.type;
            string promoName = (string)promo.name;
            List<string> currentIds = productIds;
            List<CartLineItem> currentItems = cartItems;

            if (promoType == "bundle")
            {
                List<string> requiredIds = RuleStringList(rules, "product_ids");
                if (requiredIds.Count > 0 && requiredIds.All(rid => currentIds.Contains(rid)))
                {
                    var pct = RuleDecimal(rules, "discount_pct");
                    var bundleTotal = currentItems.Where(i => requiredIds.Contains(i.ProductId)).Sum(i => i.Subtotal);
                    var amount = bundleTotal * (pct / 100m);
                    savings.Add(new SavingsLine(
                        Type: "bundle_promotion",
                        Code: null,
                        Name: promoName,
                        Description: null,
                        Product: null,
                        Tier: null,
                        DiscountPct: null,
                        Amount: Math.Round(amount, 2)
                    ));
                }
            }
            else if (promoType == "buy_x_get_y")
            {
                var buyQty = RuleInt(rules, "buy_quantity");
                var freeQty = RuleInt(rules, "free_quantity");
                var applicableCats = RuleStringList(rules, "categories");
                foreach (var item in currentItems)
                {
                    if (applicableCats.Count > 0 && !applicableCats.Contains(item.Category)) continue;
                    if (item.Quantity >= buyQty + freeQty && buyQty + freeQty > 0)
                    {
                        var freeUnits = item.Quantity / (buyQty + freeQty) * freeQty;
                        var amount = item.Price * freeUnits;
                        savings.Add(new SavingsLine(
                            Type: "buy_x_get_y",
                            Code: null,
                            Name: promoName,
                            Description: null,
                            Product: item.Name,
                            Tier: null,
                            DiscountPct: null,
                            Amount: Math.Round(amount, 2)
                        ));
                    }
                }
            }
            else if (promoType == "flash_sale")
            {
                var flashIds = RuleStringList(rules, "product_ids");
                var pct = RuleDecimal(rules, "discount_pct");
                foreach (var item in currentItems)
                {
                    if (flashIds.Contains(item.ProductId))
                    {
                        var amount = item.Subtotal * (pct / 100m);
                        savings.Add(new SavingsLine(
                            Type: "flash_sale",
                            Code: null,
                            Name: promoName,
                            Description: null,
                            Product: item.Name,
                            Tier: null,
                            DiscountPct: null,
                            Amount: Math.Round(amount, 2)
                        ));
                    }
                }
            }
        }

        // 3. Loyalty discount.
        if (!string.IsNullOrEmpty(email))
        {
            var loyalty = await conn.QueryFirstOrDefaultAsync(
                @"SELECT u.loyalty_tier, lt.discount_pct
                  FROM users u
                  JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
                  WHERE u.email = @email",
                new { email }
            );
            if (loyalty is not null && (decimal)loyalty.discount_pct > 0m)
            {
                var pct = (decimal)loyalty.discount_pct;
                var amount = originalTotal * (pct / 100m);
                savings.Add(new SavingsLine(
                    Type: "loyalty_discount",
                    Code: null,
                    Name: null,
                    Description: null,
                    Product: null,
                    Tier: (string)loyalty.loyalty_tier,
                    DiscountPct: pct,
                    Amount: Math.Round(amount, 2)
                ));
            }
        }

        var totalSavings = savings.Sum(s => s.Amount);
        var finalTotal = Math.Max(0m, originalTotal - totalSavings);
        var savingsPct = originalTotal > 0m
            ? Math.Round(totalSavings / originalTotal * 100m, 1)
            : 0m;

        return new OptimizeCartResult(
            Error: null,
            CartItems: cartItems,
            OriginalTotal: Math.Round(originalTotal, 2),
            Savings: savings,
            TotalSavings: Math.Round(totalSavings, 2),
            FinalTotal: Math.Round(finalTotal, 2),
            SavingsPercentage: savingsPct
        );
    }

    // ─────────────────────── get_active_deals ─────────────────

    [Description("List all currently active promotions and non-expired coupons.")]
    public async Task<ActiveDealsResult> GetActiveDeals()
    {
        await using var conn = await _pool.OpenAsync();

        var coupons = (await conn.QueryAsync(
            @"SELECT code, description, discount_type, discount_value,
                     min_spend, max_discount, valid_until, applicable_categories
              FROM coupons
              WHERE is_active = TRUE
                AND (valid_until IS NULL OR valid_until > NOW())
                AND valid_from <= NOW()
                AND (usage_limit IS NULL OR times_used < usage_limit)
                AND user_specific_email IS NULL
              ORDER BY discount_value DESC"
        )).Select(c => new CouponSummary(
            Code: (string)c.code,
            Description: (string?)c.description,
            DiscountType: (string)c.discount_type,
            DiscountValue: (decimal)c.discount_value,
            MinSpend: c.min_spend is null ? null : (decimal?)c.min_spend,
            MaxDiscount: c.max_discount is null ? null : (decimal?)c.max_discount,
            ValidUntil: c.valid_until is null ? null : ((DateTime)c.valid_until).ToString("o"),
            ApplicableCategories: (c.applicable_categories as string[])?.ToList()
        )).ToList();

        var promos = (await conn.QueryAsync(
            @"SELECT name, type, rules, start_date, end_date
              FROM promotions
              WHERE is_active = TRUE
                AND start_date <= NOW()
                AND end_date >= NOW()
              ORDER BY end_date ASC"
        )).Select(p => new PromotionSummary(
            Name: (string)p.name,
            Type: (string)p.type,
            Rules: ParseRules(p.rules),
            StartDate: ((DateTime)p.start_date).ToString("o"),
            EndDate: ((DateTime)p.end_date).ToString("o")
        )).ToList();

        return new ActiveDealsResult(
            Coupons: coupons,
            Promotions: promos,
            TotalDeals: coupons.Count + promos.Count
        );
    }

    // ─────────────────────── check_bundle_eligibility ─────────

    [Description("Check if a set of products qualifies for any bundle promotions.")]
    public async Task<BundleEligibilityResult> CheckBundleEligibility(
        [Description("List of product UUIDs to check for bundle deals")] List<string> productIds
    )
    {
        await using var conn = await _pool.OpenAsync();

        var products = new List<ProductInfo>();
        foreach (var pidStr in productIds)
        {
            if (!Guid.TryParse(pidStr, out var pid)) continue;
            var row = await conn.QueryFirstOrDefaultAsync(
                "SELECT id, name, price, category FROM products WHERE id = @pid",
                new { pid }
            );
            if (row is not null)
            {
                products.Add(new ProductInfo(
                    ProductId: ((Guid)row.id).ToString(),
                    Name: (string)row.name,
                    Price: (decimal)row.price,
                    Category: (string)row.category
                ));
            }
        }

        if (products.Count == 0)
        {
            return BundleEligibilityResult.Empty("No valid products found");
        }

        var bundlePromos = await conn.QueryAsync(
            @"SELECT name, type, rules, start_date, end_date
              FROM promotions
              WHERE is_active = TRUE
                AND type = 'bundle'
                AND start_date <= NOW()
                AND end_date >= NOW()"
        );

        var eligibleBundles = new List<BundleDeal>();
        List<string> productIdList = productIds;
        List<ProductInfo> productList = products;

        foreach (var promo in bundlePromos)
        {
            Dictionary<string, object> rules = ParseRules((object?)promo.rules);
            string promoName = (string)promo.name;
            string endDate = ((DateTime)promo.end_date).ToString("o");
            List<string> requiredIds = RuleStringList(rules, "product_ids");
            List<string> requiredCategories = RuleStringList(rules, "categories");
            decimal pct = RuleDecimal(rules, "discount_pct");

            if (requiredIds.Count > 0)
            {
                var matching = productIdList.Where(pid => requiredIds.Contains(pid)).ToList();
                if (matching.Count == requiredIds.Count)
                {
                    var bundleTotal = productList.Where(p => requiredIds.Contains(p.ProductId)).Sum(p => p.Price);
                    var sav = bundleTotal * (pct / 100m);
                    eligibleBundles.Add(new BundleDeal(
                        PromotionName: promoName,
                        DiscountPct: pct,
                        BundleTotal: Math.Round(bundleTotal, 2),
                        Savings: Math.Round(sav, 2),
                        EndDate: endDate,
                        QualifyingProducts: productList.Where(p => requiredIds.Contains(p.ProductId)).Select(p => p.Name).ToList()
                    ));
                }
            }

            if (requiredCategories.Count > 0)
            {
                HashSet<string> cartCategories = productList.Select(p => p.Category).ToHashSet();
                if (requiredCategories.All(cat => cartCategories.Contains(cat)))
                {
                    var matchingProducts = productList.Where(p => requiredCategories.Contains(p.Category)).ToList();
                    var bundleTotal = matchingProducts.Sum(p => p.Price);
                    var sav = bundleTotal * (pct / 100m);
                    eligibleBundles.Add(new BundleDeal(
                        PromotionName: promoName,
                        DiscountPct: pct,
                        BundleTotal: Math.Round(bundleTotal, 2),
                        Savings: Math.Round(sav, 2),
                        EndDate: endDate,
                        QualifyingProducts: matchingProducts.Select(p => p.Name).ToList()
                    ));
                }
            }
        }

        var bxgyPromos = await conn.QueryAsync(
            @"SELECT name, type, rules, end_date
              FROM promotions
              WHERE is_active = TRUE
                AND type = 'buy_x_get_y'
                AND start_date <= NOW()
                AND end_date >= NOW()"
        );

        var bxgyEligible = new List<BuyXGetYDeal>();
        foreach (var promo in bxgyPromos)
        {
            Dictionary<string, object> rules = ParseRules((object?)promo.rules);
            string promoName = (string)promo.name;
            string endDate = ((DateTime)promo.end_date).ToString("o");
            List<string> applicableCats = RuleStringList(rules, "categories");
            int buyQty = RuleInt(rules, "buy_quantity");
            int freeQty = RuleInt(rules, "free_quantity");
            var matching = productList
                .Where(p => applicableCats.Count == 0 || applicableCats.Contains(p.Category))
                .ToList();
            if (matching.Count > 0)
            {
                bxgyEligible.Add(new BuyXGetYDeal(
                    PromotionName: promoName,
                    BuyQuantity: buyQty,
                    FreeQuantity: freeQty,
                    ApplicableProducts: matching.Select(p => p.Name).ToList(),
                    EndDate: endDate
                ));
            }
        }

        return new BundleEligibilityResult(
            Eligible: eligibleBundles.Count > 0 || bxgyEligible.Count > 0,
            Error: null,
            ProductsChecked: products.Select(p => p.Name).ToList(),
            BundleDeals: eligibleBundles,
            BuyXGetYDeals: bxgyEligible
        );
    }

    // ─────────────────────── helpers ──────────────────────────

    /// <summary>Parse Postgres jsonb (returned as string by Npgsql/Dapper) into a dict.</summary>
    private static Dictionary<string, object> ParseRules(object? rules)
    {
        if (rules is null) return new();
        var raw = rules is string s ? s : rules.ToString() ?? "{}";
        var doc = JsonDocument.Parse(raw);
        var dict = new Dictionary<string, object>();
        foreach (var prop in doc.RootElement.EnumerateObject())
        {
            dict[prop.Name] = prop.Value.Clone();
        }
        return dict;
    }

    private static decimal RuleDecimal(Dictionary<string, object> rules, string key, decimal fallback = 0m)
    {
        if (rules.TryGetValue(key, out var v) && v is JsonElement el && el.ValueKind == JsonValueKind.Number)
        {
            return el.GetDecimal();
        }
        return fallback;
    }

    private static int RuleInt(Dictionary<string, object> rules, string key, int fallback = 0)
    {
        if (rules.TryGetValue(key, out var v) && v is JsonElement el && el.ValueKind == JsonValueKind.Number)
        {
            return el.GetInt32();
        }
        return fallback;
    }

    private static List<string> RuleStringList(Dictionary<string, object> rules, string key)
    {
        if (rules.TryGetValue(key, out var v) && v is JsonElement el && el.ValueKind == JsonValueKind.Array)
        {
            return el.EnumerateArray().Select(x => x.GetString() ?? "").ToList();
        }
        return [];
    }
}

// ─────────────────────── DTOs ───────────────────────

public sealed record CartItemInput(
    [property: Description("Product UUID")] string ProductId,
    [property: Description("Quantity (>=1)")] int Quantity = 1
);

public sealed record CartLineItem(
    string ProductId,
    string Name,
    decimal Price,
    string Category,
    int Quantity,
    decimal Subtotal
);

public sealed record CouponValidationResult(
    bool Valid,
    string? Error,
    string? Code,
    string? Description,
    string? DiscountType,
    decimal? DiscountValue,
    decimal? DiscountAmount,
    decimal? NewTotal,
    List<string>? ApplicableCategories
)
{
    public static CouponValidationResult Invalid(string error, string? code = null) =>
        new(false, error, code, null, null, null, null, null, null);
}

public sealed record SavingsLine(
    string Type,
    string? Code,
    string? Name,
    string? Description,
    string? Product,
    string? Tier,
    decimal? DiscountPct,
    decimal Amount
);

public sealed record OptimizeCartResult(
    string? Error,
    List<CartLineItem>? CartItems,
    decimal? OriginalTotal,
    List<SavingsLine>? Savings,
    decimal? TotalSavings,
    decimal? FinalTotal,
    decimal? SavingsPercentage
)
{
    public static OptimizeCartResult Failure(string error) =>
        new(error, null, null, null, null, null, null);
}

public sealed record CouponSummary(
    string Code,
    string? Description,
    string DiscountType,
    decimal DiscountValue,
    decimal? MinSpend,
    decimal? MaxDiscount,
    string? ValidUntil,
    List<string>? ApplicableCategories
);

public sealed record PromotionSummary(
    string Name,
    string Type,
    Dictionary<string, object> Rules,
    string StartDate,
    string EndDate
);

public sealed record ActiveDealsResult(
    List<CouponSummary> Coupons,
    List<PromotionSummary> Promotions,
    int TotalDeals
);

public sealed record ProductInfo(string ProductId, string Name, decimal Price, string Category);

public sealed record BundleDeal(
    string PromotionName,
    decimal DiscountPct,
    decimal BundleTotal,
    decimal Savings,
    string EndDate,
    List<string> QualifyingProducts
);

public sealed record BuyXGetYDeal(
    string PromotionName,
    int BuyQuantity,
    int FreeQuantity,
    List<string> ApplicableProducts,
    string EndDate
);

public sealed record BundleEligibilityResult(
    bool Eligible,
    string? Error,
    List<string>? ProductsChecked,
    List<BundleDeal> BundleDeals,
    List<BuyXGetYDeal> BuyXGetYDeals
)
{
    public static BundleEligibilityResult Empty(string error) =>
        new(false, error, null, [], []);
}
