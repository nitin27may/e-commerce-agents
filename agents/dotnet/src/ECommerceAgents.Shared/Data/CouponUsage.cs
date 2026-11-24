using System.Data;
using Dapper;

namespace ECommerceAgents.Shared.Data;

/// <summary>
/// One use of a coupon per customer. Mirrors Python's <c>shared/coupons.py</c>: the record is
/// <c>orders.coupon_code</c> (non-cancelled orders only), and the error code and sentence are identical on both
/// stacks.
///
/// The check alone does not stop two simultaneous checkouts; <see cref="LockUserForCheckoutAsync"/> takes a row lock
/// on the user inside the checkout transaction, before the cart is read, so a second checkout by the same user waits
/// and then sees the first one's committed order. A unique partial index was rejected because an installed database
/// may already hold the same code on two orders of one user.
/// </summary>
public static class CouponUsage
{
    public const string AlreadyUsedCode = "coupon_already_used";
    public const string AlreadyUsedMessage = "You have already used this coupon";

    /// <summary>Serialise this user's checkouts; the lock holds until the transaction ends.</summary>
    public static Task LockUserForCheckoutAsync(IDbConnection conn, Guid userId, IDbTransaction tx) =>
        conn.ExecuteAsync(
            "SELECT 1 FROM users WHERE id = @uid FOR NO KEY UPDATE",
            new { uid = userId },
            tx
        );

    /// <summary>True when the user has a non-cancelled order carrying <paramref name="code"/> (case-insensitive).</summary>
    public static Task<bool> HasUsedAsync(
        IDbConnection conn,
        Guid userId,
        string code,
        IDbTransaction? tx = null
    ) =>
        conn.ExecuteScalarAsync<bool>(
            @"SELECT EXISTS (
                  SELECT 1 FROM orders
                  WHERE user_id = @uid
                    AND UPPER(coupon_code) = UPPER(@code)
                    AND status <> 'cancelled'
              )",
            new { uid = userId, code },
            tx
        );
}
