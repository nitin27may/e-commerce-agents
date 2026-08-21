using Dapper;
using ECommerceAgents.Shared.Data;

namespace ECommerceAgents.Shared.Grounding;

/// <summary>One claim's verdict. Field names match the client's <c>GroundingClaim</c>.</summary>
public sealed record ClaimVerdict(
    string Type,
    string Id,
    string Status,
    string? Detail = null,
    string? Source = null
);

public sealed record GroundingReport(IReadOnlyList<ClaimVerdict> Verdicts)
{
    public int Total => Verdicts.Count;
    public int Verified => Verdicts.Count(v => v.Status == "verified");
    public int Unverified => Total - Verified;

    /// <summary>Snake-case shape the client's GroundingReport parses.</summary>
    public object ToWire() => new
    {
        total = Total,
        verified = Verified,
        unverified = Unverified,
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
/// Checks extracted claims against Postgres — the .NET twin of Python's
/// <c>shared/grounding/verifier.py</c>.
/// </summary>
/// <remarks>
/// Python verifies in three tiers: a per-request ledger of facts recorded from
/// tool results, then a batched database lookup, then a consistency check.
/// This implements the database and consistency tiers. The ledger tier is not
/// a cheaper route to the same answer — it is the only thing that can check a
/// figure quoted in prose against what a tool actually returned — but in this
/// stack those tool results are produced inside the *specialist* processes and
/// would have to be carried back over A2A to be visible here. So prose amounts
/// and tracking numbers are checked against the database rows this answer's
/// own cards cite, which covers the case that actually occurs (a card, then
/// its price restated in the sentence above it) and reports anything else as
/// <c>unverifiable</c> rather than dropping it. Every verdict carries its
/// <c>source</c>, so a reader can see nothing here came from a ledger.
///
/// Ids are looked up in one batched query per type rather than per claim: a
/// products card carries up to a dozen, and a query each would make grounding
/// the slowest thing in the response.
/// </remarks>
public sealed class GroundingVerifier(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    /// <summary>Matches Python's <c>_PRICE_TOLERANCE</c>: a cent of float slop.</summary>
    private const decimal PriceTolerance = 0.01m;

    public async Task<GroundingReport> VerifyAsync(ExtractedClaims claims, CancellationToken ct = default)
    {
        if (claims.Total == 0)
        {
            return new GroundingReport([]);
        }

        var verdicts = new List<ClaimVerdict>();
        await using var conn = await _pool.OpenAsync();

        // ── products ──
        var productIds = ParseGuids(claims.Products.Select(p => p.Id));
        var productRows = productIds.Count == 0
            ? []
            : (await conn.QueryAsync("SELECT id, name, price FROM products WHERE id = ANY(@ids)", new { ids = productIds.ToArray() }))
                .ToDictionary(r => ((Guid)r.id).ToString(), r => (Name: (string)r.name, Price: (decimal)r.price), StringComparer.OrdinalIgnoreCase);

        foreach (var claim in claims.Products)
        {
            if (!productRows.TryGetValue(claim.Id, out var row))
            {
                verdicts.Add(new ClaimVerdict("product", claim.Id, "not_found",
                    "No product with this id exists.", "db"));
                continue;
            }

            // A card that names a real product at the wrong price is more
            // misleading than one that names nothing, so price is checked too.
            verdicts.Add(claim.Price is { } quoted && quoted != row.Price
                ? new ClaimVerdict("product", claim.Id, "price_mismatch",
                    $"Card says ${quoted:F2}; database says ${row.Price:F2}.", "db")
                : new ClaimVerdict("product", claim.Id, "verified", row.Name, "db"));
        }

        // ── orders ──
        var orderIds = ParseGuids(claims.Orders.Select(o => o.Id));
        var orderRows = orderIds.Count == 0
            ? []
            : (await conn.QueryAsync("SELECT id, status, total, tracking_number FROM orders WHERE id = ANY(@ids)", new { ids = orderIds.ToArray() }))
                .ToDictionary(
                    r => ((Guid)r.id).ToString(),
                    r => (Status: (string)r.status, Total: (decimal)r.total, Tracking: (string?)r.tracking_number),
                    StringComparer.OrdinalIgnoreCase
                );

        foreach (var claim in claims.Orders)
        {
            if (!orderRows.TryGetValue(claim.Id, out var row))
            {
                verdicts.Add(new ClaimVerdict("order", claim.Id, "not_found",
                    "No order with this id exists.", "db"));
                continue;
            }

            verdicts.Add(claim.Total is { } quoted && quoted != row.Total
                ? new ClaimVerdict("order", claim.Id, "price_mismatch",
                    $"Card says ${quoted:F2}; database says ${row.Total:F2}.", "db")
                : new ClaimVerdict("order", claim.Id, "verified", row.Status, "db"));
        }

        // ── bare ids mentioned in prose ──
        var bareIds = ParseGuids(claims.BareIds);
        if (bareIds.Count > 0)
        {
            var known = (await conn.QueryAsync<Guid>(
                @"SELECT id FROM products WHERE id = ANY(@ids)
                  UNION SELECT id FROM orders WHERE id = ANY(@ids)",
                new { ids = bareIds.ToArray() }
            )).Select(g => g.ToString()).ToHashSet(StringComparer.OrdinalIgnoreCase);

            foreach (var id in claims.BareIds)
            {
                verdicts.Add(known.Contains(id)
                    ? new ClaimVerdict("bare_id", id, "verified", null, "db")
                    : new ClaimVerdict("bare_id", id, "not_found",
                        "This id matches no product or order.", "db"));
            }
        }

        // ── amounts and tracking numbers ──
        // Checked against the database rows already fetched for this answer's
        // own cards, which covers the case that actually occurs: the model
        // shows a product card and then restates its price in the sentence
        // above it. Python resolves these against a per-request ledger of
        // every fact its tools returned; that ledger is populated inside the
        // *specialist* process, so an orchestrator-side port would need the
        // facts carried back over A2A. Until then this is the honest subset —
        // a figure that matches no cited entity is reported unverifiable
        // rather than dropped, so the count never overstates.
        var knownAmounts = productRows.Values.Select(r => r.Price)
            .Concat(orderRows.Values.Select(r => r.Total))
            .ToList();

        foreach (var amount in claims.Amounts)
        {
            var matched = knownAmounts.Any(known => Math.Abs(amount - known) < PriceTolerance);
            verdicts.Add(matched
                ? new ClaimVerdict("amount", $"${amount:F2}", "verified", null, "db")
                : new ClaimVerdict("amount", $"${amount:F2}", "unverifiable",
                    "No cited product or order in this answer carries this figure.", null));
        }

        var knownTracking = orderRows.Values
            .Select(r => r.Tracking)
            .Where(t => !string.IsNullOrWhiteSpace(t))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        foreach (var tracking in claims.TrackingNumbers)
        {
            verdicts.Add(knownTracking.Contains(tracking)
                ? new ClaimVerdict("tracking", tracking, "verified", null, "db")
                : new ClaimVerdict("tracking", tracking, "unverifiable",
                    "No cited order in this answer carries this tracking number.", null));
        }

        return new GroundingReport(verdicts);
    }

    private static List<Guid> ParseGuids(IEnumerable<string> raw) =>
        raw.Select(s => Guid.TryParse(s, out var g) ? g : (Guid?)null)
            .Where(g => g is not null)
            .Select(g => g!.Value)
            .Distinct()
            .ToList();
}
