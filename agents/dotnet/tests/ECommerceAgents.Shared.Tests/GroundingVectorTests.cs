using System.Text.Json;
using ECommerceAgents.Shared.Grounding;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The grounding vector file Python runs too (<c>tests/test_grounding_vectors.py</c>):
/// <c>agents/python/config/grounding/testdata/ledger.v1.json</c>. It pins what the ledger records from a tool
/// result, what a user's text and a tool call's arguments supply, and the verdict a prose amount or id gets, so
/// the two stacks cannot drift on any of them.
/// </summary>
public sealed class GroundingVectorTests
{
    private static readonly JsonElement Vectors = Load();

    private static JsonElement Load()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "python", "config")))
        {
            dir = dir.Parent;
        }

        dir.Should().NotBeNull();
        var path = Path.Combine(dir!.FullName, "agents", "python", "config", "grounding", "testdata", "ledger.v1.json");
        return JsonDocument.Parse(File.ReadAllText(path)).RootElement.Clone();
    }

    private static TheoryData<string> Names(string section)
    {
        var data = new TheoryData<string>();
        foreach (var c in Vectors.GetProperty(section).EnumerateArray())
        {
            data.Add(c.GetProperty("name").GetString()!);
        }

        return data;
    }

    public static TheoryData<string> LedgerCases() => Names("ledger_cases");
    public static TheoryData<string> UserTextCases() => Names("user_text_cases");
    public static TheoryData<string> ToolArgumentCases() => Names("tool_argument_cases");
    public static TheoryData<string> VerdictCases() => Names("verdict_cases");

    private static JsonElement Case(string section, string name) =>
        Vectors.GetProperty(section).EnumerateArray().Single(c => c.GetProperty("name").GetString() == name);

    private static decimal[] Decimals(JsonElement list) => list.EnumerateArray().Select(e => e.GetDecimal()).ToArray();

    private static bool Has(IEnumerable<decimal> amounts, decimal value) => amounts.Any(a => Math.Abs(a - value) < 0.000001m);

    [Theory]
    [MemberData(nameof(LedgerCases))]
    public void Ledger_RecordsWhatTheVectorSays(string name)
    {
        var c = Case("ledger_cases", name);
        var ledger = new GroundingLedger();
        foreach (var result in c.GetProperty("tool_results").EnumerateArray())
        {
            ledger.Record(result);
        }

        var expect = c.GetProperty("expect");
        if (expect.TryGetProperty("products", out var products))
        {
            foreach (var p in products.EnumerateObject())
            {
                ledger.Products[p.Name].Name.Should().Be(p.Value.GetProperty("name").GetString());
                ledger.Products[p.Name].Price.Should().Be(p.Value.GetProperty("price").GetDecimal());
            }
        }

        if (expect.TryGetProperty("product_ids", out var productIds))
        {
            ledger.Products.Keys.Should().Contain(productIds.EnumerateArray().Select(e => e.GetString()!));
        }

        if (expect.TryGetProperty("orders", out var orders))
        {
            foreach (var o in orders.EnumerateObject())
            {
                var recorded = ledger.Orders[o.Name];
                recorded.Status.Should().Be(o.Value.GetProperty("status").GetString());
                recorded.Tracking.Should().Be(o.Value.GetProperty("tracking").GetString());
                recorded.Total.Should().Be(o.Value.GetProperty("total").GetDecimal());
            }
        }

        if (expect.TryGetProperty("promos", out var promos))
        {
            foreach (var p in promos.EnumerateObject())
            {
                ledger.Promos[p.Name].DiscountAmount.Should().Be(p.Value.GetDecimal());
            }
        }

        if (expect.TryGetProperty("no_products", out _)) ledger.Products.Should().BeEmpty();
        if (expect.TryGetProperty("no_orders", out _)) ledger.Orders.Should().BeEmpty();
        if (expect.TryGetProperty("no_promos", out _)) ledger.Promos.Should().BeEmpty();
        if (expect.TryGetProperty("no_known_amounts", out _)) ledger.KnownAmounts.Should().BeEmpty();

        if (expect.TryGetProperty("known_amounts", out var known))
        {
            foreach (var amount in Decimals(known))
            {
                Has(ledger.KnownAmounts, amount).Should().BeTrue($"{amount} should be a known amount");
            }
        }

        if (expect.TryGetProperty("absent_amounts", out var absent))
        {
            foreach (var amount in Decimals(absent))
            {
                Has(ledger.KnownAmounts, amount).Should().BeFalse($"{amount} must not be a known amount");
            }
        }
    }

    [Theory]
    [MemberData(nameof(UserTextCases))]
    public void UserText_SuppliesWhatTheVectorSays(string name)
    {
        var c = Case("user_text_cases", name);
        var (amounts, ids) = UserSupplied.Values(c.GetProperty("text").GetString());

        amounts.OrderBy(a => a).Should().Equal(Decimals(c.GetProperty("amounts")).OrderBy(a => a));
        ids.Should().BeEquivalentTo(c.GetProperty("ids").EnumerateArray().Select(e => e.GetString()!));
    }

    [Theory]
    [MemberData(nameof(ToolArgumentCases))]
    public void ToolArguments_SupplyWhatTheVectorSays(string name)
    {
        var c = Case("tool_argument_cases", name);
        var (amounts, ids) = UserSupplied.Values(c.GetProperty("arguments"));

        amounts.OrderBy(a => a).Should().Equal(Decimals(c.GetProperty("amounts")).OrderBy(a => a));
        ids.Should().BeEquivalentTo(c.GetProperty("ids").EnumerateArray().Select(e => e.GetString()!));
    }

    [Theory]
    [MemberData(nameof(VerdictCases))]
    public async Task Verdicts_MatchTheVector(string name)
    {
        var c = Case("verdict_cases", name);
        var ledger = new GroundingLedger();
        if (c.TryGetProperty("known_amounts", out var known))
        {
            ledger.KnownAmounts.UnionWith(Decimals(known));
        }

        if (c.TryGetProperty("user_text", out var text))
        {
            ledger.NoteUserText(text.GetString());
        }

        if (c.TryGetProperty("tool_arguments", out var arguments))
        {
            ledger.NoteToolArguments(arguments);
        }

        var amounts = c.TryGetProperty("claim_amounts", out var claimAmounts) ? Decimals(claimAmounts) : [];
        var bareIds = c.TryGetProperty("bare_ids", out var ids)
            ? ids.EnumerateArray().Select(e => e.GetString()!).ToArray()
            : [];
        var claims = new ExtractedClaims([], [], bareIds, amounts, []);

        var report = await new GroundingVerifier(null).VerifyAsync(claims, ledger);

        report.Verdicts.Select(v => v.Status).Should()
            .Equal(c.GetProperty("statuses").EnumerateArray().Select(e => e.GetString()!));
    }
}
