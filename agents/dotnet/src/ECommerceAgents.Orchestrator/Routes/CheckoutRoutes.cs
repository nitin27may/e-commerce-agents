using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Routing;
using System.Text.Json;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>POST /api/checkout</c>. Mirrors the Python transaction:
/// validate cart → check stock → compute discounts (coupon + loyalty) →
/// insert order + items + status history → decrement warehouse inventory →
/// update <c>users.total_spend</c> → clear cart.
/// </summary>
public static class CheckoutRoutes
{
    public sealed record CheckoutRequest(
        Dictionary<string, object> ShippingAddress,
        Dictionary<string, object>? BillingAddress = null,
        bool BillingSameAsShipping = true,
        string PaymentMethod = "demo"
    );

    public static IEndpointRouteBuilder MapCheckoutRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapPost("/api/checkout", Checkout);
        return routes;
    }

    private static async Task<IResult> Checkout(
        [FromBody] CheckoutRequest body,
        DatabasePool pool
    )
    {
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();
        if (body is null || body.ShippingAddress is null || body.ShippingAddress.Count == 0)
        {
            return Results.BadRequest(new { detail = "shipping_address is required" });
        }

        await using var conn = await pool.OpenAsync();
        await using var tx = await conn.BeginTransactionAsync();

        // 1. Fetch cart
        var cart = await conn.QueryFirstOrDefaultAsync(
            "SELECT id, coupon_code, discount_amount FROM carts WHERE user_id = @uid",
            new { uid = userId },
            tx
        );
        if (cart is null)
        {
            return Results.BadRequest(new { detail = "No cart found" });
        }
        Guid cartId = (Guid)cart.id;

        // 2. Fetch items
        var itemRows = (await conn.QueryAsync(
            @"SELECT ci.id, ci.product_id, ci.quantity,
                     p.name, p.price, p.is_active
              FROM cart_items ci
              JOIN products p ON ci.product_id = p.id
              WHERE ci.cart_id = @cid",
            new { cid = cartId },
            tx
        )).ToList();
        if (itemRows.Count == 0)
        {
            return Results.BadRequest(new { detail = "Cart is empty" });
        }

        // 3. Validate active
        var inactive = itemRows
            .Where(i => !(bool)i.is_active)
            .Select(i => (string)i.name)
            .ToList();
        if (inactive.Count > 0)
        {
            return Results.BadRequest(new
            {
                detail = $"The following products are no longer available: {string.Join(", ", inactive)}",
            });
        }

        // 4. Check stock
        foreach (var item in itemRows)
        {
            long stock = await conn.ExecuteScalarAsync<long>(
                "SELECT COALESCE(SUM(quantity), 0) FROM warehouse_inventory WHERE product_id = @pid",
                new { pid = (Guid)item.product_id },
                tx
            );
            int wanted = (int)item.quantity;
            if (stock < wanted)
            {
                return Results.BadRequest(new
                {
                    detail = $"Insufficient stock for '{(string)item.name}'. Available: {stock}, requested: {wanted}",
                });
            }
        }

        // 5. Subtotal
        decimal subtotal = itemRows.Sum(i => (decimal)i.price * (int)i.quantity);

        // 6. Coupon discount
        decimal couponDiscount = 0m;
        string? couponCode = (string?)cart.coupon_code;
        if (!string.IsNullOrWhiteSpace(couponCode))
        {
            var coupon = await conn.QueryFirstOrDefaultAsync(
                @"SELECT discount_type, discount_value, max_discount, is_active,
                         valid_until, usage_limit, times_used, min_spend
                  FROM coupons WHERE code = @code",
                new { code = couponCode },
                tx
            );
            bool valid = coupon is not null && (bool)coupon.is_active;
            if (valid && coupon!.valid_until is not null
                && (DateTime)coupon.valid_until < DateTime.UtcNow)
            {
                valid = false;
            }
            if (valid && coupon!.usage_limit is not null
                && Convert.ToInt32(coupon.times_used) >= (int)coupon.usage_limit)
            {
                valid = false;
            }
            if (valid && coupon!.min_spend is not null
                && subtotal < (decimal)coupon.min_spend)
            {
                valid = false;
            }

            if (valid && coupon is not null)
            {
                if ((string)coupon.discount_type == "percentage")
                {
                    couponDiscount = subtotal * (decimal)coupon.discount_value / 100m;
                    if (coupon.max_discount is not null)
                    {
                        couponDiscount = Math.Min(couponDiscount, (decimal)coupon.max_discount);
                    }
                }
                else
                {
                    couponDiscount = (decimal)coupon.discount_value;
                }
                await conn.ExecuteAsync(
                    "UPDATE coupons SET times_used = times_used + 1 WHERE code = @code",
                    new { code = couponCode },
                    tx
                );
            }
            else
            {
                couponCode = null;
            }
        }

        // 7. Loyalty discount
        decimal loyaltyDiscount = 0m;
        var loyalty = await conn.QueryFirstOrDefaultAsync(
            @"SELECT lt.discount_pct
              FROM users u
              JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
              WHERE u.id = @uid",
            new { uid = userId },
            tx
        );
        if (loyalty is not null && loyalty.discount_pct is not null)
        {
            loyaltyDiscount = subtotal * (decimal)loyalty.discount_pct / 100m;
        }

        // 8. Total
        decimal total = Math.Round(Math.Max(subtotal - couponDiscount - loyaltyDiscount, 0m), 2);
        decimal totalDiscount = Math.Round(couponDiscount + loyaltyDiscount, 2);

        // 9. Addresses
        string shipping = JsonSerializer.Serialize(body.ShippingAddress);
        string billing = body.BillingSameAsShipping || body.BillingAddress is null
            ? shipping
            : JsonSerializer.Serialize(body.BillingAddress);

        // 10. Carrier
        var carrier = await conn.QueryFirstOrDefaultAsync(
            "SELECT id, name FROM carriers ORDER BY base_rate LIMIT 1",
            transaction: tx
        );
        string carrierName = carrier is null ? "Standard Shipping" : (string)carrier.name;

        // 11. Tracking
        string tracking = $"TRK-{Guid.NewGuid().ToString("N")[..12].ToUpperInvariant()}";

        // 12. Order
        var orderId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address, billing_address,
                                  shipping_carrier, tracking_number, coupon_code, discount_amount)
              VALUES (@uid, 'placed', @total, @ship::jsonb, @bill::jsonb, @carrier, @tracking, @coupon, @d)
              RETURNING id",
            new
            {
                uid = userId,
                total,
                ship = shipping,
                bill = billing,
                carrier = carrierName,
                tracking,
                coupon = couponCode,
                d = totalDiscount,
            },
            tx
        );

        // 13. Order items
        foreach (var item in itemRows)
        {
            int qty = (int)item.quantity;
            decimal price = (decimal)item.price;
            decimal itemSubtotal = Math.Round(price * qty, 2);
            await conn.ExecuteAsync(
                @"INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal)
                  VALUES (@oid, @pid, @q, @p, @s)",
                new
                {
                    oid = orderId,
                    pid = (Guid)item.product_id,
                    q = qty,
                    p = price,
                    s = itemSubtotal,
                },
                tx
            );
        }

        // 14. Status history
        await conn.ExecuteAsync(
            @"INSERT INTO order_status_history (order_id, status, notes)
              VALUES (@oid, 'placed', 'Order placed via checkout')",
            new { oid = orderId },
            tx
        );

        // 15. Decrement inventory
        foreach (var item in itemRows)
        {
            int remaining = (int)item.quantity;
            var warehouses = (await conn.QueryAsync(
                @"SELECT warehouse_id, product_id, quantity FROM warehouse_inventory
                  WHERE product_id = @pid AND quantity > 0
                  ORDER BY quantity DESC",
                new { pid = (Guid)item.product_id },
                tx
            )).ToList();
            foreach (var wh in warehouses)
            {
                if (remaining <= 0) break;
                int avail = (int)wh.quantity;
                int deduct = Math.Min(remaining, avail);
                await conn.ExecuteAsync(
                    @"UPDATE warehouse_inventory
                      SET quantity = quantity - @d
                      WHERE warehouse_id = @wid AND product_id = @pid",
                    new
                    {
                        d = deduct,
                        wid = (Guid)wh.warehouse_id,
                        pid = (Guid)wh.product_id,
                    },
                    tx
                );
                remaining -= deduct;
            }
        }

        // 16. Total spend
        await conn.ExecuteAsync(
            "UPDATE users SET total_spend = total_spend + @t WHERE id = @uid",
            new { t = total, uid = userId },
            tx
        );

        // 17. Clear cart
        await conn.ExecuteAsync(
            "DELETE FROM cart_items WHERE cart_id = @cid",
            new { cid = cartId },
            tx
        );
        await conn.ExecuteAsync(
            @"UPDATE carts
              SET coupon_code = NULL, discount_amount = 0, notes = NULL, updated_at = NOW()
              WHERE id = @cid",
            new { cid = cartId },
            tx
        );

        await tx.CommitAsync();

        return Results.Ok(new
        {
            order_id = orderId.ToString(),
            total,
            item_count = itemRows.Count,
            status = "placed",
            tracking_number = tracking,
            carrier = carrierName,
        });
    }
}
