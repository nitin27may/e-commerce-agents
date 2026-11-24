using System.Globalization;
using Dapper;
using ECommerceAgents.Shared.Data;

namespace ECommerceAgents.Shared.Grounding;

/// <summary>
/// One claim's verdict. Field names match the client's <c>GroundingClaim</c>.
/// </summary>
/// <param name="Status">
/// <c>verified</c>, <c>user_supplied</c>, <c>derived</c>, <c>price_mismatch</c>, <c>not_found</c> or
/// <c>unverifiable</c>.
/// </param>
/// <param name="CorrectedValue">
/// The real price or total, set only for <c>price_mismatch</c>: it lets <c>enforce</c> mode correct a card in
/// place instead of only stripping it. Not on the wire.
/// </param>
public sealed record ClaimVerdict(
    string Type,
    string Id,
    string Status,
    string? Detail = null,
    string? Source = null,
    decimal? CorrectedValue = null
);

public sealed record GroundingReport(IReadOnlyList<ClaimVerdict> Verdicts)
{
    /// <summary>
    /// Statuses that count as grounded. <c>user_supplied</c> (the caller typed the value, or passed it as a tool
    /// argument) and <c>derived</c> (an exact sum or difference of two grounded amounts) keep their own name so a
    /// report never hides how a claim was settled.
    /// </summary>
    public static readonly IReadOnlySet<string> GroundedStatuses =
        new HashSet<string> { "verified", "user_supplied", "derived" };

    public int Total => Verdicts.Count;
    public int Verified => Verdicts.Count(v => GroundedStatuses.Contains(v.Status));
    public int UserSupplied => Verdicts.Count(v => v.Status == "user_supplied");
    public int Derived => Verdicts.Count(v => v.Status == "derived");
    public int Unverified => Total - Verified;

    /// <summary>
    /// Snake-case shape the client's GroundingReport parses. The Python twin is
    /// <c>report_to_wire</c>; the field names are pinned on both sides.
    /// </summary>
    public object ToWire() => new
    {
        total = Total,
        verified = Verified,
        unverified = Unverified,
        // Counted in verified above and reported separately: the caller typed the value, or it is an exact
        // sum or difference of two grounded amounts.
        user_supplied = UserSupplied,
        derived = Derived,
        claims = Verdicts.Select(v => new
        {
            type = v.Type,
            id = v.Id,
            status = v.Status,
            detail = v.Detail,
            source = v.Source,
        }).ToList(),
    };
}

/// <summary>
/// Checks extracted claims against what this request's tools returned, then against Postgres — the .NET twin of
/// Python's <c>shared/grounding/verifier.py</c>.
/// </summary>
/// <remarks>
/// <para>
/// Tiers, cheapest first, as on Python:
/// </para>
/// <list type="number">
/// <item><b>Ledger</b> (free): the id, figure or tracking number appeared in a tool result this same request.</item>
/// <item><b>Database</b>: a batched <c>WHERE id = ANY(@ids)</c> query per entity type for what the ledger did not
/// cover. A real id with a wrong price is <c>price_mismatch</c>, never <c>verified</c>.</item>
/// <item><b>Caller</b>: a figure or id present only in the user's message or a tool call's arguments is
/// <c>user_supplied</c>. It never excuses a figure the model made up, because only a value that literally appears
/// in the caller's own text lands in the ledger's user set.</item>
/// <item><b>Derived</b>: an exact sum or difference, to the cent, of two distinct grounded amounts.</item>
/// </list>
/// <para>
/// Prose amounts are settled in this order: ledger, then the database rows this answer's own cards cite (a card
/// and its price restated in the sentence above it, which .NET could check before it had a ledger), then
/// <c>user_supplied</c>, then <c>derived</c>. The rows a card cites also count as operands for a derived figure.
/// </para>
/// <para>
/// A ledger is only as complete as the process that owns it. Tool results are produced in the specialist
/// processes, so the orchestrator's own ledger holds its own tools' results, the user's message and its tool
/// arguments; the specialists verify against theirs. That is the same split as Python.
/// </para>
/// <para>
/// DB lookups check existence and field consistency, not ownership: a real order id belonging to another user
/// still verifies. Grounding answers "is this a fabrication", not "is this authorized".
/// </para>
/// </remarks>
public sealed class GroundingVerifier(DatabasePool? pool)
{
    private readonly DatabasePool? _pool = pool;

    /// <summary>Matches Python's <c>_PRICE_TOLERANCE</c>: a cent of float slop.</summary>
    private const decimal PriceTolerance = 0.01m;

    /// <summary>Bound on the pairs tried for derived arithmetic: a turn's grounded amounts are a handful.</summary>
    private const int MaxDerivedOperands = 60;

    public async Task<GroundingReport> VerifyAsync(
        ExtractedClaims claims, GroundingLedger? ledger = null, CancellationToken ct = default)
    {
        if (claims.Total == 0)
        {
            return new GroundingReport([]);
        }

        ledger ??= new GroundingLedger();
        var verdicts = new List<ClaimVerdict>();

        // Ledger tier first; whatever it cannot settle goes to the database in one batch per type.
        var productVerdicts = new Dictionary<int, ClaimVerdict>();
        var unresolvedProducts = new List<(int Index, ProductClaim Claim)>();
        for (var i = 0; i < claims.Products.Count; i++)
        {
            var verdict = VerifyProductAgainstLedger(claims.Products[i], ledger);
            if (verdict is null)
            {
                unresolvedProducts.Add((i, claims.Products[i]));
            }
            else
            {
                productVerdicts[i] = verdict;
            }
        }

        var orderVerdicts = new Dictionary<int, ClaimVerdict>();
        var unresolvedOrders = new List<(int Index, OrderClaim Claim)>();
        for (var i = 0; i < claims.Orders.Count; i++)
        {
            var verdict = VerifyOrderAgainstLedger(claims.Orders[i], ledger);
            if (verdict is null)
            {
                unresolvedOrders.Add((i, claims.Orders[i]));
            }
            else
            {
                orderVerdicts[i] = verdict;
            }
        }

        var bareVerdicts = new Dictionary<int, ClaimVerdict>();
        var unresolvedBare = new List<(int Index, string Id)>();
        for (var i = 0; i < claims.BareIds.Count; i++)
        {
            if (ledger.UserIds.Contains(claims.BareIds[i]))
            {
                bareVerdicts[i] = new ClaimVerdict(
                    "bare_id", claims.BareIds[i], "user_supplied", "id appears in the user's message or tool arguments");
            }
            else
            {
                unresolvedBare.Add((i, claims.BareIds[i]));
            }
        }

        // Rows the database returned for this answer's own cards: they ground a restated price or tracking number.
        var dbAmounts = new List<decimal>();
        var dbTrackings = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        if (unresolvedProducts.Count + unresolvedOrders.Count + unresolvedBare.Count > 0)
        {
            if (_pool is null)
            {
                foreach (var (index, claim) in unresolvedProducts)
                {
                    productVerdicts[index] = new ClaimVerdict("product", claim.Id, "unverifiable", "no database connection");
                }
                foreach (var (index, claim) in unresolvedOrders)
                {
                    orderVerdicts[index] = new ClaimVerdict("order", claim.Id, "unverifiable", "no database connection");
                }
                foreach (var (index, id) in unresolvedBare)
                {
                    bareVerdicts[index] = new ClaimVerdict("bare_id", id, "unverifiable", "no database connection");
                }
            }
            else
            {
                await using var conn = await _pool.OpenAsync();
                await VerifyProductsViaDbAsync(conn, unresolvedProducts, productVerdicts, dbAmounts);
                await VerifyOrdersViaDbAsync(conn, unresolvedOrders, orderVerdicts, dbAmounts, dbTrackings);
                await VerifyBareIdsViaDbAsync(conn, unresolvedBare, bareVerdicts);
            }
        }

        verdicts.AddRange(productVerdicts.OrderBy(p => p.Key).Select(p => p.Value));
        verdicts.AddRange(orderVerdicts.OrderBy(p => p.Key).Select(p => p.Value));
        verdicts.AddRange(bareVerdicts.OrderBy(p => p.Key).Select(p => p.Value));
        verdicts.AddRange(VerifyAmounts(claims, ledger, dbAmounts));
        verdicts.AddRange(VerifyTrackings(claims, ledger, dbTrackings));

        return new GroundingReport(verdicts);
    }

    // ── ledger tier ──

    private static ClaimVerdict? VerifyProductAgainstLedger(ProductClaim claim, GroundingLedger ledger)
    {
        if (!ledger.Products.TryGetValue(claim.Id, out var fact))
        {
            return null;
        }

        if (claim.Price is { } quoted && fact.Price is { } real && Math.Abs(quoted - real) >= PriceTolerance)
        {
            return new ClaimVerdict(
                "product", claim.Id, "price_mismatch",
                $"Card says {Money(quoted)}; tool result said {Money(real)}.", "ledger", real);
        }

        return new ClaimVerdict("product", claim.Id, "verified", fact.Name, "ledger");
    }

    private static ClaimVerdict? VerifyOrderAgainstLedger(OrderClaim claim, GroundingLedger ledger)
    {
        if (!ledger.Orders.TryGetValue(claim.Id, out var fact))
        {
            return null;
        }

        if (claim.Total is { } quoted && fact.Total is { } real && Math.Abs(quoted - real) >= PriceTolerance)
        {
            return new ClaimVerdict(
                "order", claim.Id, "price_mismatch",
                $"Card says {Money(quoted)}; tool result said {Money(real)}.", "ledger", real);
        }

        return new ClaimVerdict("order", claim.Id, "verified", fact.Status, "ledger");
    }

    // ── database tier ──

    private static async Task VerifyProductsViaDbAsync(
        Npgsql.NpgsqlConnection conn,
        List<(int Index, ProductClaim Claim)> unresolved,
        Dictionary<int, ClaimVerdict> verdicts,
        List<decimal> dbAmounts)
    {
        if (unresolved.Count == 0)
        {
            return;
        }

        var ids = ParseGuids(unresolved.Select(u => u.Claim.Id));
        var rows = ids.Count == 0
            ? []
            : (await conn.QueryAsync("SELECT id, name, price FROM products WHERE id = ANY(@ids)", new { ids = ids.ToArray() }))
                .ToDictionary(r => ((Guid)r.id).ToString(), r => (Name: (string)r.name, Price: (decimal)r.price),
                    StringComparer.OrdinalIgnoreCase);

        foreach (var (index, claim) in unresolved)
        {
            if (!rows.TryGetValue(claim.Id, out var row))
            {
                verdicts[index] = new ClaimVerdict(
                    "product", claim.Id, "not_found",
                    Guid.TryParse(claim.Id, out _) ? "No product with this id exists." : "Id is not a valid UUID.", "db");
                continue;
            }

            dbAmounts.Add(row.Price);

            // A card that names a real product at the wrong price is more misleading than one that names
            // nothing, so price is checked too.
            verdicts[index] = claim.Price is { } quoted && Math.Abs(quoted - row.Price) >= PriceTolerance
                ? new ClaimVerdict(
                    "product", claim.Id, "price_mismatch",
                    $"Card says {Money(quoted)}; database says {Money(row.Price)}.", "db", row.Price)
                : new ClaimVerdict("product", claim.Id, "verified", row.Name, "db");
        }
    }

    private static async Task VerifyOrdersViaDbAsync(
        Npgsql.NpgsqlConnection conn,
        List<(int Index, OrderClaim Claim)> unresolved,
        Dictionary<int, ClaimVerdict> verdicts,
        List<decimal> dbAmounts,
        HashSet<string> dbTrackings)
    {
        if (unresolved.Count == 0)
        {
            return;
        }

        var ids = ParseGuids(unresolved.Select(u => u.Claim.Id));
        var rows = ids.Count == 0
            ? []
            : (await conn.QueryAsync(
                    "SELECT id, status, total, tracking_number FROM orders WHERE id = ANY(@ids)", new { ids = ids.ToArray() }))
                .ToDictionary(
                    r => ((Guid)r.id).ToString(),
                    r => (Status: (string)r.status, Total: (decimal)r.total, Tracking: (string?)r.tracking_number),
                    StringComparer.OrdinalIgnoreCase);

        foreach (var (index, claim) in unresolved)
        {
            if (!rows.TryGetValue(claim.Id, out var row))
            {
                verdicts[index] = new ClaimVerdict(
                    "order", claim.Id, "not_found",
                    Guid.TryParse(claim.Id, out _) ? "No order with this id exists." : "Id is not a valid UUID.", "db");
                continue;
            }

            dbAmounts.Add(row.Total);
            if (!string.IsNullOrWhiteSpace(row.Tracking))
            {
                dbTrackings.Add(row.Tracking);
            }

            verdicts[index] = claim.Total is { } quoted && Math.Abs(quoted - row.Total) >= PriceTolerance
                ? new ClaimVerdict(
                    "order", claim.Id, "price_mismatch",
                    $"Card says {Money(quoted)}; database says {Money(row.Total)}.", "db", row.Total)
                : new ClaimVerdict("order", claim.Id, "verified", row.Status, "db");
        }
    }

    private static async Task VerifyBareIdsViaDbAsync(
        Npgsql.NpgsqlConnection conn,
        List<(int Index, string Id)> unresolved,
        Dictionary<int, ClaimVerdict> verdicts)
    {
        if (unresolved.Count == 0)
        {
            return;
        }

        var ids = ParseGuids(unresolved.Select(u => u.Id));
        var known = ids.Count == 0
            ? []
            : (await conn.QueryAsync<Guid>(
                @"SELECT id FROM products WHERE id = ANY(@ids)
                  UNION SELECT id FROM orders WHERE id = ANY(@ids)",
                new { ids = ids.ToArray() }
            )).Select(g => g.ToString()).ToHashSet(StringComparer.OrdinalIgnoreCase);

        foreach (var (index, id) in unresolved)
        {
            verdicts[index] = known.Contains(id)
                ? new ClaimVerdict("bare_id", id, "verified", null, "db")
                : new ClaimVerdict(
                    "bare_id", id, "not_found",
                    Guid.TryParse(id, out _) ? "This id matches no product or order." : "Id is not a valid UUID.", "db");
        }
    }

    // ── amounts and tracking numbers ──

    private static List<ClaimVerdict> VerifyAmounts(
        ExtractedClaims claims, GroundingLedger ledger, IReadOnlyList<decimal> dbAmounts)
    {
        var known = new HashSet<decimal>(ledger.KnownAmounts);
        known.UnionWith(ledger.Products.Values.Where(p => p.Price is not null).Select(p => p.Price!.Value));
        known.UnionWith(ledger.Orders.Values.Where(o => o.Total is not null).Select(o => o.Total!.Value));
        known.UnionWith(ledger.Promos.Values.Where(p => p.DiscountAmount is not null).Select(p => p.DiscountAmount!.Value));

        var userAmounts = ledger.UserAmounts.ToList();
        var operands = known.Concat(dbAmounts).Concat(userAmounts).Distinct().Order().Take(MaxDerivedOperands).ToList();

        var verdicts = new List<ClaimVerdict>();
        foreach (var amount in claims.Amounts)
        {
            var id = Money(amount);
            if (known.Any(k => Math.Abs(amount - k) < PriceTolerance))
            {
                verdicts.Add(new ClaimVerdict("amount", id, "verified", null, "ledger"));
            }
            else if (dbAmounts.Any(k => Math.Abs(amount - k) < PriceTolerance))
            {
                verdicts.Add(new ClaimVerdict("amount", id, "verified", null, "db"));
            }
            else if (userAmounts.Any(k => Math.Abs(amount - k) < PriceTolerance))
            {
                verdicts.Add(new ClaimVerdict(
                    "amount", id, "user_supplied", "amount appears in the user's message or tool arguments", "user"));
            }
            else if (DerivedFrom(amount, operands) is { } used)
            {
                verdicts.Add(new ClaimVerdict(
                    "amount", id, "derived",
                    $"exact sum or difference of {Money(used.A)} and {Money(used.B)}", "derived"));
            }
            else
            {
                verdicts.Add(new ClaimVerdict(
                    "amount", id, "unverifiable", "no matching price/total in this turn's tool results"));
            }
        }

        return verdicts;
    }

    /// <summary>Two distinct grounded amounts whose sum or difference equals <paramref name="value"/> exactly to the cent.</summary>
    private static (decimal A, decimal B)? DerivedFrom(decimal value, IReadOnlyList<decimal> operands)
    {
        var target = Cents(value);
        for (var i = 0; i < operands.Count; i++)
        {
            for (var j = i + 1; j < operands.Count; j++)
            {
                var (a, b) = (Cents(operands[i]), Cents(operands[j]));
                if (a + b == target || Math.Abs(a - b) == target)
                {
                    return (operands[i], operands[j]);
                }
            }
        }

        return null;
    }

    private static List<ClaimVerdict> VerifyTrackings(
        ExtractedClaims claims, GroundingLedger ledger, HashSet<string> dbTrackings)
    {
        var known = ledger.Orders.Values
            .Select(o => o.Tracking)
            .Where(t => !string.IsNullOrWhiteSpace(t))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        var verdicts = new List<ClaimVerdict>();
        foreach (var tracking in claims.TrackingNumbers)
        {
            if (known.Contains(tracking))
            {
                verdicts.Add(new ClaimVerdict("tracking", tracking, "verified", null, "ledger"));
            }
            else if (dbTrackings.Contains(tracking))
            {
                verdicts.Add(new ClaimVerdict("tracking", tracking, "verified", null, "db"));
            }
            else
            {
                verdicts.Add(new ClaimVerdict(
                    "tracking", tracking, "unverifiable", "no matching tracking number in this turn's tool results"));
            }
        }

        return verdicts;
    }

    private static long Cents(decimal value) => (long)decimal.Round(value * 100m, 0, MidpointRounding.AwayFromZero);

    private static string Money(decimal value) => "$" + value.ToString("F2", CultureInfo.InvariantCulture);

    private static List<Guid> ParseGuids(IEnumerable<string> raw) =>
        raw.Select(s => Guid.TryParse(s, out var g) ? g : (Guid?)null)
            .Where(g => g is not null)
            .Select(g => g!.Value)
            .Distinct()
            .ToList();
}
