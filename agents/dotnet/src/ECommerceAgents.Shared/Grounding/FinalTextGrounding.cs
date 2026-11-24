using System.Text.Json.Nodes;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.Grounding;

/// <summary>
/// Verifies an answer's final text per <c>GROUNDING_MODE</c> — the .NET twin of Python's
/// <c>verify_final_text</c> and <c>_apply_corrections</c> (<c>shared/grounding/middleware.py</c>).
/// </summary>
/// <remarks>
/// <para>
/// One dispatch for every place an answer is composed: the chat routes (the tool path and every orchestration
/// mode, which is what Python's <c>@grounded_run</c> does per mode), a resumed workflow, and a specialist's own
/// run. <c>off</c> does nothing. <c>observe</c> verifies and logs but reports nothing. <c>annotate</c> reports.
/// <c>enforce</c> reports and corrects the text: a card naming a product or order that does not exist is removed,
/// and a card with a wrong price or total gets the real one.
/// </para>
/// <para>
/// An answer with no checkable claim sends no report, so the client renders no badge rather than a misleading
/// "0 facts verified". A failure here never takes down the answer.
/// </para>
/// <para>
/// <c>enforce</c> cannot un-stream a fabricated card that already reached the browser inside a live chunk; only
/// the persisted and final text is corrected. An operator who needs the visible stream never to show an unverified
/// card should route that traffic through the non-streaming path. A workflow mode's answer is composed in one
/// piece, so there the visible text is the corrected one.
/// </para>
/// </remarks>
public static class FinalTextGrounding
{
    public static async Task<(GroundingReport? Report, string Text)> VerifyAsync(
        string? text,
        string? mode,
        GroundingLedger? ledger,
        DatabasePool? pool,
        ILogger? logger,
        CancellationToken ct = default)
    {
        var shown = text ?? "";
        mode = (mode ?? "annotate").Trim().ToLowerInvariant();
        if (mode == "off" || string.IsNullOrEmpty(text))
        {
            return (null, shown);
        }

        var claims = ClaimExtractor.Extract(text);
        if (claims.Total == 0)
        {
            return (null, shown);
        }

        GroundingReport report;
        try
        {
            report = await new GroundingVerifier(pool).VerifyAsync(claims, ledger, ct);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            // Grounding is a report about an answer, not part of producing it: a failure here must not take
            // down a response the user is already reading.
            logger?.LogError(ex, "grounding.final_text_failed mode={Mode}", mode);
            return (null, shown);
        }

        logger?.LogInformation(
            "grounding.verified total={Total} verified={Verified} unverified={Unverified} mode={Mode}",
            report.Total, report.Verified, report.Unverified, mode);

        // observe measures without showing: verified above, withheld here.
        if (mode == "observe")
        {
            return (null, shown);
        }

        return (report, mode == "enforce" ? ApplyCorrections(text, report) : shown);
    }

    /// <summary>
    /// Strips cards that name nothing real and corrects the price or total of a real one. A card the verifier did
    /// not reach, or could not check (the database was unreachable), is left alone: a real card is never stripped
    /// because the check could not run.
    /// </summary>
    public static string ApplyCorrections(string text, GroundingReport report)
    {
        var products = Latest(report, "product");
        var orders = Latest(report, "order");

        return ClaimExtractor.RewriteCards(
            text,
            entry => Decide(entry, products, "price"),
            entry => Decide(entry, orders, "total"));
    }

    private static Dictionary<string, ClaimVerdict> Latest(GroundingReport report, string type)
    {
        var map = new Dictionary<string, ClaimVerdict>(StringComparer.Ordinal);
        foreach (var verdict in report.Verdicts.Where(v => v.Type == type))
        {
            map[verdict.Id] = verdict;
        }

        return map;
    }

    private static JsonObject? Decide(JsonObject entry, Dictionary<string, ClaimVerdict> verdicts, string priceKey)
    {
        var id = entry["id"]?.ToString();
        if (id is null || !verdicts.TryGetValue(id, out var verdict))
        {
            return entry;
        }

        if (verdict.Status == "not_found")
        {
            return null;
        }

        if (verdict.Status == "price_mismatch" && verdict.CorrectedValue is { } corrected)
        {
            var copy = (JsonObject)entry.DeepClone();
            copy[priceKey] = JsonValue.Create(corrected);
            return copy;
        }

        return entry;
    }
}
