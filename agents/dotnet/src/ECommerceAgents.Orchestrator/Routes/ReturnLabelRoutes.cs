using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using System.Globalization;
using System.Text;
using System.Text.Json;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>GET /api/returns/{label_token}/label</c>. Generates a simple
/// shipping-label PDF without any PDF library (raw PDF 1.4 bytes).
/// Matches Python's _build_return_label_pdf layout 1:1 so the two
/// backends produce visually identical labels.
/// </summary>
public static class ReturnLabelRoutes
{
    public static IEndpointRouteBuilder MapReturnLabelRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/returns/{labelToken}/label", GetLabel);
        return routes;
    }

    private static async Task<IResult> GetLabel(string labelToken, DatabasePool pool)
    {
        if (string.IsNullOrWhiteSpace(labelToken))
        {
            return Results.NotFound(new { detail = "Return label not found" });
        }

        await using var conn = await pool.OpenAsync();
        var ret = await conn.QueryFirstOrDefaultAsync(
            @"SELECT r.id, r.order_id, r.reason, r.status, r.return_label_url,
                     r.refund_method, r.refund_amount, r.created_at,
                     o.shipping_address, o.shipping_carrier,
                     u.name AS user_name, u.email AS user_email
              FROM returns r
              JOIN orders o ON r.order_id = o.id
              JOIN users u ON r.user_id = u.id
              WHERE r.return_label_url LIKE @pattern",
            new { pattern = $"%{labelToken}%" }
        );
        if (ret is null)
        {
            return Results.NotFound(new { detail = "Return label not found" });
        }

        var address = ParseAddress(ret.shipping_address);
        string userName = ret.user_name is null ? "Customer" : (string)ret.user_name;
        string userEmail = ret.user_email is null ? "" : (string)ret.user_email;
        string orderIdShort = ((Guid)ret.order_id).ToString("N")[..8];
        string returnIdShort = ((Guid)ret.id).ToString("N")[..8];
        string carrier = ret.shipping_carrier is null ? "Standard Shipping" : (string)ret.shipping_carrier;
        string reason = ret.reason is null ? "Return" : (string)ret.reason;
        string created = ret.created_at is null
            ? ""
            : ((DateTime)ret.created_at).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        string barcode = $"RTN-{labelToken.ToUpperInvariant()}";

        var pdfBytes = BuildPdf(
            barcode,
            userName,
            userEmail,
            orderIdShort,
            returnIdShort,
            carrier,
            reason,
            created,
            addr: address
        );

        return Results.File(
            pdfBytes,
            contentType: "application/pdf",
            fileDownloadName: $"return-label-{labelToken}.pdf"
        );
    }

    private static (string Street, string City, string State, string Zip) ParseAddress(object? raw)
    {
        if (raw is null) return ("", "", "", "");
        var text = raw is string s ? s : raw.ToString();
        if (string.IsNullOrWhiteSpace(text)) return ("", "", "", "");
        try
        {
            using var doc = JsonDocument.Parse(text!);
            string Get(string key) =>
                doc.RootElement.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String
                    ? v.GetString() ?? ""
                    : "";
            return (Get("street"), Get("city"), Get("state"), Get("zip"));
        }
        catch
        {
            return ("", "", "", "");
        }
    }

    private static byte[] BuildPdf(
        string barcode,
        string userName,
        string userEmail,
        string orderIdShort,
        string returnIdShort,
        string carrier,
        string reason,
        string created,
        (string Street, string City, string State, string Zip) addr
    )
    {
        string Esc(string s) => s.Replace("\\", "\\\\").Replace("(", "\\(").Replace(")", "\\)");

        string shortReason = reason.Length > 60 ? reason[..60] : reason;

        var stream = string.Join("\n", new[]
        {
            // Header bar (teal)
            "0.05 0.58 0.55 rg",
            "0 742 612 50 re f",
            "1 1 1 rg",
            "BT /F2 20 Tf 30 760 Td (RETURN SHIPPING LABEL) Tj ET",
            // Barcode area
            "0.95 0.95 0.95 rg",
            "30 680 552 50 re f",
            "0 0 0 rg",
            $"BT /F2 16 Tf 180 700 Td ({Esc(barcode)}) Tj ET",
            "BT /F1 9 Tf 30 685 Td (Scan or enter this code at drop-off) Tj ET",
            // Carrier
            "0 0 0 rg",
            $"BT /F2 12 Tf 30 655 Td (Carrier: {Esc(carrier)}) Tj ET",
            $"BT /F1 10 Tf 400 655 Td (Date: {Esc(created)}) Tj ET",
            // Divider
            "0.8 0.8 0.8 RG", "0.5 w", "30 640 m 582 640 l S",
            // FROM
            "0 0 0 rg",
            "BT /F2 11 Tf 30 620 Td (FROM:) Tj ET",
            $"BT /F1 11 Tf 30 605 Td ({Esc(userName)}) Tj ET",
            $"BT /F1 10 Tf 30 591 Td ({Esc(addr.Street)}) Tj ET",
            $"BT /F1 10 Tf 30 577 Td ({Esc(addr.City)}, {Esc(addr.State)} {Esc(addr.Zip)}) Tj ET",
            $"BT /F1 9 Tf 30 562 Td ({Esc(userEmail)}) Tj ET",
            // TO
            "BT /F2 11 Tf 320 620 Td (TO:) Tj ET",
            "BT /F1 11 Tf 320 605 Td (E-Commerce Agents Returns Center) Tj ET",
            "BT /F1 10 Tf 320 591 Td (1200 Returns Blvd, Suite 400) Tj ET",
            "BT /F1 10 Tf 320 577 Td (Memphis, TN 38118) Tj ET",
            // Divider
            "30 545 m 582 545 l S",
            // Return details
            "0.97 0.97 0.97 rg",
            "30 460 552 75 re f",
            "0 0 0 rg",
            "BT /F2 10 Tf 40 520 Td (Order ID:) Tj ET",
            $"BT /F1 10 Tf 140 520 Td (#{Esc(orderIdShort)}...) Tj ET",
            "BT /F2 10 Tf 40 504 Td (Return ID:) Tj ET",
            $"BT /F1 10 Tf 140 504 Td (#{Esc(returnIdShort)}...) Tj ET",
            "BT /F2 10 Tf 40 488 Td (Reason:) Tj ET",
            $"BT /F1 10 Tf 140 488 Td ({Esc(shortReason)}) Tj ET",
            "BT /F2 10 Tf 40 472 Td (Status:) Tj ET",
            "BT /F1 10 Tf 140 472 Td (Return Requested) Tj ET",
            // Instructions box
            "0.05 0.58 0.55 rg",
            "30 380 552 60 re f",
            "1 1 1 rg",
            "BT /F2 12 Tf 40 420 Td (INSTRUCTIONS) Tj ET",
            "BT /F1 10 Tf 40 404 Td (1. Print this label and cut along the border.) Tj ET",
            "BT /F1 10 Tf 40 390 Td (2. Pack all items securely in the original packaging.) Tj ET",
            "0 0 0 rg",
            "BT /F1 10 Tf 40 360 Td (3. Attach this label to the outside of the package.) Tj ET",
            "BT /F1 10 Tf 40 346 Td (4. Drop off at any carrier location or schedule a pickup.) Tj ET",
            "BT /F1 10 Tf 40 332 Td (5. Your refund will be processed after we receive and inspect the items.) Tj ET",
            // Footer
            "0.6 0.6 0.6 rg",
            "BT /F1 8 Tf 30 50 Td (Generated by E-Commerce Agents | This label is valid for 30 days from the return request date.) Tj ET",
            $"BT /F1 8 Tf 30 38 Td (Label ID: {Esc(barcode)} | For support, contact support@ecommerce-agents.com) Tj ET",
        });
        byte[] streamBytes = Encoding.Latin1.GetBytes(stream);

        var objects = new List<string>
        {
            "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj",
            "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj",
            "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                + "/Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>\nendobj",
            $"4 0 obj\n<< /Length {streamBytes.Length} >>\nstream\n{stream}\nendstream\nendobj",
            "5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj",
            "6 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj",
        };

        var pdfLines = new List<string> { "%PDF-1.4" };
        var offsets = new List<int>();
        foreach (var obj in objects)
        {
            offsets.Add(Encoding.Latin1.GetBytes(string.Join("\n", pdfLines)).Length + 1);
            pdfLines.Add(obj);
        }

        int xrefOffset = Encoding.Latin1.GetBytes(string.Join("\n", pdfLines)).Length + 1;
        pdfLines.Add("xref");
        pdfLines.Add($"0 {objects.Count + 1}");
        pdfLines.Add("0000000000 65535 f ");
        foreach (var off in offsets)
        {
            pdfLines.Add($"{off:D10} 00000 n ");
        }
        pdfLines.Add("trailer");
        pdfLines.Add($"<< /Size {objects.Count + 1} /Root 1 0 R >>");
        pdfLines.Add("startxref");
        pdfLines.Add(xrefOffset.ToString(CultureInfo.InvariantCulture));
        pdfLines.Add("%%EOF");

        return Encoding.Latin1.GetBytes(string.Join("\n", pdfLines));
    }
}
