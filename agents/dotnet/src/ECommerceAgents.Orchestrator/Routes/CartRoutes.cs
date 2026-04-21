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
/// <c>/api/cart</c> endpoints. Mirrors the Python cart handlers.
/// Cart is lazy-created on first write/read.
/// </summary>
public static class CartRoutes
{
    public sealed record AddItemRequest(string ProductId, int Quantity = 1);
    public sealed record UpdateItemRequest(int Quantity);
    public sealed record ApplyCouponRequest(string Code);
    public sealed record CartAddressRequest(
        Dictionary<string, object>? ShippingAddress = null,
        Dictionary<string, object>? BillingAddress = null,
        bool BillingSameAsShipping = true
    );

    public static IEndpointRouteBuilder MapCartRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/cart", GetCart);
        routes.MapPost("/api/cart/items", AddItem);
        routes.MapPut("/api/cart/items/{itemId}", UpdateItem);
        routes.MapDelete("/api/cart/items/{itemId}", RemoveItem);
        routes.MapPost("/api/cart/coupon", ApplyCoupon);
        routes.MapDelete("/api/cart/coupon", RemoveCoupon);
        routes.MapPut("/api/cart/address", UpdateAddress);
        return routes;
    }

    // ─────────────────────── get cart ────────────────────────

    private static async Task<IResult> GetCart(DatabasePool pool)
    {
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var cart = await conn.QueryFirstOrDefaultAsync(
            @"SELECT id, coupon_code, discount_amount, shipping_address, billing_address,
                     billing_same_as_shipping
              FROM carts WHERE user_id = @uid",
            new { uid = userId }
        );
        if (cart is null)
        {
            cart = await conn.QueryFirstAsync(
                @"INSERT INTO carts (user_id) VALUES (@uid)
                  RETURNING id, coupon_code, discount_amount, shipping_address, billing_address,
                            billing_same_as_shipping",
                new { uid = userId }
            );
        }

        Guid cartId = (Guid)cart.id;

        var items = (await conn.QueryAsync(
            @"SELECT ci.id, ci.product_id, ci.quantity,
                     p.name, p.brand, p.category, p.price, p.original_price, p.image_url,
                     COALESCE((SELECT SUM(wi.quantity) FROM warehouse_inventory wi
                               WHERE wi.product_id = ci.product_id), 0) AS available_qty
              FROM cart_items ci
              JOIN products p ON ci.product_id = p.id
              WHERE ci.cart_id = @cid
              ORDER BY ci.added_at",
            new { cid = cartId }
        )).ToList();

        decimal subtotal = items.Sum(i => (decimal)i.price * (int)i.quantity);
        decimal discount = cart.discount_amount is null ? 0m : (decimal)cart.discount_amount;
        decimal total = Math.Max(subtotal - discount, 0m);
        int itemCount = items.Sum(i => (int)i.quantity);

        return Results.Ok(new
        {
            id = cartId.ToString(),
            items = items.Select(i => new
            {
                id = ((Guid)i.id).ToString(),
                product_id = ((Guid)i.product_id).ToString(),
                name = (string)i.name,
                brand = (string?)i.brand,
                category = (string?)i.category,
                price = (decimal)i.price,
                original_price = i.original_price is null ? (decimal?)null : (decimal)i.original_price,
                quantity = (int)i.quantity,
                subtotal = Math.Round((decimal)i.price * (int)i.quantity, 2),
                image_url = (string?)i.image_url,
                in_stock = Convert.ToInt64(i.available_qty) > 0,
                available_qty = Convert.ToInt64(i.available_qty),
            }),
            item_count = itemCount,
            subtotal = Math.Round(subtotal, 2),
            discount_amount = discount,
            coupon_code = (string?)cart.coupon_code,
            total = Math.Round(total, 2),
            shipping_address = ParseJson(cart.shipping_address),
            billing_address = ParseJson(cart.billing_address),
            billing_same_as_shipping = cart.billing_same_as_shipping is null || (bool)cart.billing_same_as_shipping,
        });
    }

    // ─────────────────────── add item ────────────────────────

    private static async Task<IResult> AddItem(
        [FromBody] AddItemRequest body,
        DatabasePool pool
    )
    {
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();
        if (body is null || string.IsNullOrWhiteSpace(body.ProductId))
        {
            return Results.BadRequest(new { detail = "product_id is required" });
        }
        if (!Guid.TryParse(body.ProductId, out var productId))
        {
            return Results.NotFound(new { detail = "Product not found" });
        }
        int qty = body.Quantity <= 0 ? 1 : body.Quantity;

        await using var conn = await pool.OpenAsync();
        var product = await conn.QueryFirstOrDefaultAsync(
            "SELECT id, is_active FROM products WHERE id = @id",
            new { id = productId }
        );
        if (product is null)
        {
            return Results.NotFound(new { detail = "Product not found" });
        }
        if (!(bool)product.is_active)
        {
            return Results.BadRequest(new { detail = "Product is no longer available" });
        }

        var cartId = await conn.ExecuteScalarAsync<Guid?>(
            "SELECT id FROM carts WHERE user_id = @uid",
            new { uid = userId }
        );
        if (cartId is null)
        {
            cartId = await conn.ExecuteScalarAsync<Guid>(
                "INSERT INTO carts (user_id) VALUES (@uid) RETURNING id",
                new { uid = userId }
            );
        }

        await conn.ExecuteAsync(
            @"INSERT INTO cart_items (cart_id, product_id, quantity)
              VALUES (@cid, @pid, @q)
              ON CONFLICT (cart_id, product_id)
              DO UPDATE SET quantity = cart_items.quantity + EXCLUDED.quantity",
            new { cid = cartId, pid = productId, q = qty }
        );
        await conn.ExecuteAsync("UPDATE carts SET updated_at = NOW() WHERE id = @cid",
            new { cid = cartId });

        return Results.Ok(new
        {
            status = "added",
            product_id = productId.ToString(),
            quantity = qty,
        });
    }

    // ─────────────────────── update item ─────────────────────

    private static async Task<IResult> UpdateItem(
        string itemId,
        [FromBody] UpdateItemRequest body,
        DatabasePool pool
    )
    {
        if (!Guid.TryParse(itemId, out var iid))
        {
            return Results.NotFound(new { detail = "Cart item not found" });
        }
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var item = await conn.QueryFirstOrDefaultAsync(
            @"SELECT ci.id, ci.cart_id
              FROM cart_items ci
              JOIN carts c ON ci.cart_id = c.id
              WHERE ci.id = @id AND c.user_id = @uid",
            new { id = iid, uid = userId }
        );
        if (item is null)
        {
            return Results.NotFound(new { detail = "Cart item not found" });
        }

        if (body.Quantity <= 0)
        {
            await conn.ExecuteAsync("DELETE FROM cart_items WHERE id = @id", new { id = iid });
        }
        else
        {
            await conn.ExecuteAsync(
                "UPDATE cart_items SET quantity = @q WHERE id = @id",
                new { q = body.Quantity, id = iid }
            );
        }
        await conn.ExecuteAsync(
            "UPDATE carts SET updated_at = NOW() WHERE id = @cid",
            new { cid = (Guid)item.cart_id }
        );

        return Results.Ok(new { status = "updated" });
    }

    // ─────────────────────── remove item ─────────────────────

    private static async Task<IResult> RemoveItem(string itemId, DatabasePool pool)
    {
        if (!Guid.TryParse(itemId, out var iid))
        {
            return Results.NotFound(new { detail = "Cart item not found" });
        }
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var item = await conn.QueryFirstOrDefaultAsync(
            @"SELECT ci.id, ci.cart_id
              FROM cart_items ci
              JOIN carts c ON ci.cart_id = c.id
              WHERE ci.id = @id AND c.user_id = @uid",
            new { id = iid, uid = userId }
        );
        if (item is null)
        {
            return Results.NotFound(new { detail = "Cart item not found" });
        }

        await conn.ExecuteAsync("DELETE FROM cart_items WHERE id = @id", new { id = iid });
        await conn.ExecuteAsync(
            "UPDATE carts SET updated_at = NOW() WHERE id = @cid",
            new { cid = (Guid)item.cart_id }
        );
        return Results.Ok(new { status = "removed" });
    }

    // ─────────────────────── apply coupon ────────────────────

    private static async Task<IResult> ApplyCoupon(
        [FromBody] ApplyCouponRequest body,
        DatabasePool pool
    )
    {
        if (body is null || string.IsNullOrWhiteSpace(body.Code))
        {
            return Results.BadRequest(new { detail = "code is required" });
        }
        var email = RequestContext.CurrentUserEmail;
        var userId = await UserResolver.ResolveUserIdAsync(pool, email);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var cartId = await conn.ExecuteScalarAsync<Guid?>(
            "SELECT id FROM carts WHERE user_id = @uid", new { uid = userId });
        if (cartId is null)
        {
            return Results.BadRequest(new { detail = "Cart not found" });
        }

        var items = (await conn.QueryAsync(
            @"SELECT ci.quantity, p.price
              FROM cart_items ci
              JOIN products p ON ci.product_id = p.id
              WHERE ci.cart_id = @cid",
            new { cid = cartId }
        )).ToList();
        if (items.Count == 0)
        {
            return Results.BadRequest(new { detail = "Cart is empty" });
        }
        decimal subtotal = items.Sum(i => (decimal)i.price * (int)i.quantity);

        var coupon = await conn.QueryFirstOrDefaultAsync(
            @"SELECT id, code, description, discount_type, discount_value,
                     min_spend, max_discount, usage_limit, times_used,
                     valid_from, valid_until, user_specific_email, is_active
              FROM coupons WHERE code = @code",
            new { code = body.Code.ToUpperInvariant() }
        );
        if (coupon is null)
        {
            return Results.NotFound(new { detail = "Coupon not found" });
        }
        if (!(bool)coupon.is_active)
        {
            return Results.BadRequest(new { detail = "Coupon is no longer active" });
        }
        if (coupon.valid_until is not null && (DateTime)coupon.valid_until < DateTime.UtcNow)
        {
            return Results.BadRequest(new { detail = "Coupon has expired" });
        }
        if (coupon.usage_limit is not null
            && Convert.ToInt32(coupon.times_used) >= (int)coupon.usage_limit)
        {
            return Results.BadRequest(new { detail = "Coupon usage limit reached" });
        }
        if (coupon.min_spend is not null && subtotal < (decimal)coupon.min_spend)
        {
            return Results.BadRequest(new
            {
                detail = $"Minimum spend of ${(decimal)coupon.min_spend:F2} required",
            });
        }
        if (coupon.user_specific_email is not null
            && !string.Equals((string)coupon.user_specific_email, email, StringComparison.OrdinalIgnoreCase))
        {
            return Results.BadRequest(new { detail = "This coupon is not valid for your account" });
        }

        decimal discount;
        if ((string)coupon.discount_type == "percentage")
        {
            discount = subtotal * (decimal)coupon.discount_value / 100m;
            if (coupon.max_discount is not null)
            {
                discount = Math.Min(discount, (decimal)coupon.max_discount);
            }
        }
        else
        {
            discount = (decimal)coupon.discount_value;
        }
        discount = Math.Round(Math.Min(discount, subtotal), 2);

        await conn.ExecuteAsync(
            "UPDATE carts SET coupon_code = @code, discount_amount = @d, updated_at = NOW() WHERE id = @cid",
            new { code = (string)coupon.code, d = discount, cid = cartId }
        );

        return Results.Ok(new
        {
            status = "applied",
            code = (string)coupon.code,
            discount_amount = discount,
            description = (string?)coupon.description,
        });
    }

    private static async Task<IResult> RemoveCoupon(DatabasePool pool)
    {
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        await conn.ExecuteAsync(
            "UPDATE carts SET coupon_code = NULL, discount_amount = 0, updated_at = NOW() WHERE user_id = @uid",
            new { uid = userId }
        );
        return Results.Ok(new { status = "removed" });
    }

    // ─────────────────────── address ─────────────────────────

    private static async Task<IResult> UpdateAddress(
        [FromBody] CartAddressRequest body,
        DatabasePool pool
    )
    {
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var cartId = await conn.ExecuteScalarAsync<Guid?>(
            "SELECT id FROM carts WHERE user_id = @uid", new { uid = userId });
        if (cartId is null)
        {
            cartId = await conn.ExecuteScalarAsync<Guid>(
                "INSERT INTO carts (user_id) VALUES (@uid) RETURNING id",
                new { uid = userId }
            );
        }

        string? shipping = body.ShippingAddress is null ? null : JsonSerializer.Serialize(body.ShippingAddress);
        string? billing = body.BillingAddress is null ? null : JsonSerializer.Serialize(body.BillingAddress);
        if (body.BillingSameAsShipping && shipping is not null)
        {
            billing = shipping;
        }

        await conn.ExecuteAsync(
            @"UPDATE carts
              SET shipping_address = COALESCE(@ship::jsonb, shipping_address),
                  billing_address  = COALESCE(@bill::jsonb, billing_address),
                  billing_same_as_shipping = @same,
                  updated_at = NOW()
              WHERE id = @cid",
            new { ship = shipping, bill = billing, same = body.BillingSameAsShipping, cid = cartId }
        );

        return Results.Ok(new { status = "updated" });
    }

    // ─────────────────────── helpers ─────────────────────────

    private static Dictionary<string, JsonElement>? ParseJson(object? raw)
    {
        if (raw is null) return null;
        var text = raw is string s ? s : raw.ToString();
        if (string.IsNullOrWhiteSpace(text)) return null;
        try
        {
            return JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(text!);
        }
        catch
        {
            return null;
        }
    }
}
