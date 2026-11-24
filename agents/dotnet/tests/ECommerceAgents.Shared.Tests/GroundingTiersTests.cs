using System.Runtime.CompilerServices;
using System.Text.Json;
using Dapper;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Grounding;
using ECommerceAgents.Shared.Middleware;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The verifier's tiers without a database (twin of Python's <c>test_grounding_verifier.py</c> ledger cases and
/// <c>test_grounding_user_supplied.py</c>): the ledger settles a claim before the database is asked, and nothing
/// the model made up is excused by a value only the caller typed.
/// </summary>
public sealed class GroundingTiersTests
{
    private const string ProductId = "0fd372fa-ecb2-4db0-bb71-8628a784ced9";
    private const string OrderId = "1a2b3c4d-5e6f-7890-abcd-ef0123456789";

    private static async Task<GroundingReport> VerifyAsync(string text, GroundingLedger? ledger) =>
        await new GroundingVerifier(null).VerifyAsync(ClaimExtractor.Extract(text), ledger);

    private static string ProductCard(decimal price) =>
        $$"""
        ```product
        {"id": "{{ProductId}}", "name": "Widget", "price": {{price}}}
        ```
        """;

    [Fact]
    public async Task ACardTheLedgerHolds_IsVerifiedWithoutADatabase()
    {
        var ledger = new GroundingLedger();
        ledger.Record(new { id = ProductId, name = "Widget", price = 19.99m });

        var report = await VerifyAsync(ProductCard(19.99m), ledger);

        report.Verdicts.Single().Status.Should().Be("verified");
        report.Verdicts.Single().Source.Should().Be("ledger");
    }

    [Fact]
    public async Task ACardAtTheWrongPrice_IsAMismatchCarryingTheRealOne()
    {
        var ledger = new GroundingLedger();
        ledger.Record(new { id = ProductId, name = "Widget", price = 19.99m });

        var verdict = (await VerifyAsync(ProductCard(9.00m), ledger)).Verdicts.Single();

        verdict.Status.Should().Be("price_mismatch");
        verdict.CorrectedValue.Should().Be(19.99m);
        verdict.Source.Should().Be("ledger");
    }

    [Fact]
    public async Task APriceWithinACent_IsNotAMismatch()
    {
        var ledger = new GroundingLedger();
        ledger.Record(new { id = ProductId, name = "Widget", price = 19.99m });

        (await VerifyAsync(ProductCard(19.995m), ledger)).Verdicts.Single().Status.Should().Be("verified");
    }

    [Fact]
    public async Task AnOrderTheLedgerHolds_IsVerified_AndItsTrackingNumberToo()
    {
        var ledger = new GroundingLedger();
        ledger.Record(new { order_id = OrderId, status = "shipped", total = 55m, tracking_number = "TRK99XZ" });
        var text = $$"""
            Shipped via TRK99XZ, not TRK11111:

            ```order
            {"id": "{{OrderId}}", "status": "shipped", "total": 55.00}
            ```
            """;

        var report = await VerifyAsync(text, ledger);

        report.Verdicts.Single(v => v.Type == "order").Source.Should().Be("ledger");
        report.Verdicts.Where(v => v.Type == "tracking").ToDictionary(v => v.Id, v => v.Status)
            .Should().Equal(new Dictionary<string, string> { ["TRK99XZ"] = "verified", ["TRK11111"] = "unverifiable" });
    }

    [Fact]
    public async Task ACardNoToolReturned_WithNoDatabase_IsUnverifiableNotNotFound()
    {
        var verdict = (await VerifyAsync(ProductCard(5m), new GroundingLedger())).Verdicts.Single();

        verdict.Status.Should().Be("unverifiable");
        verdict.Detail.Should().Be("no database connection");
    }

    [Fact]
    public async Task AnAmountTheUserTyped_IsUserSupplied_AndCountsAsVerifiedButIsReportedSeparately()
    {
        var ledger = new GroundingLedger();
        ledger.NoteUserText("My cart total is $80 and I want something under $14.99");

        var report = await VerifyAsync("Your $80.00 cart qualifies; picks under $14.99 follow.", ledger);

        report.Verdicts.Select(v => v.Status).Should().OnlyContain(s => s == "user_supplied");
        report.Verified.Should().Be(2);
        report.UserSupplied.Should().Be(2);
        report.Verdicts[0].Source.Should().Be("user");
    }

    [Fact]
    public async Task AModelInventedAmount_StaysUnverifiable_EvenWithUserAmountsPresent()
    {
        var ledger = new GroundingLedger();
        ledger.NoteUserText("My cart total is $80");

        var report = await VerifyAsync("That comes to $95.00.", ledger);

        report.Verdicts.Single().Status.Should().Be("unverifiable");
        report.Verified.Should().Be(0);
    }

    [Fact]
    public async Task AWrongSum_StillFails_WhileTheExactOneIsDerived()
    {
        var ledger = new GroundingLedger { };
        ledger.KnownAmounts.UnionWith([600m, 1000m]);

        var report = await VerifyAsync("Spend $400.00 more, or $450.00, or $400.01.", ledger);

        report.Verdicts.Select(v => v.Status).Should().Equal("derived", "unverifiable", "unverifiable");
        report.Derived.Should().Be(1);
        report.Verdicts[0].Detail.Should().Be("exact sum or difference of $600.00 and $1000.00");
    }

    [Fact]
    public async Task ABareIdTheUserTyped_IsUserSupplied_WithoutTouchingTheDatabase()
    {
        var ledger = new GroundingLedger();
        ledger.NoteUserText($"Tell me about product {ProductId.ToUpperInvariant()}");

        var report = await VerifyAsync($"Product {ProductId} is a widget.", ledger);

        report.Verdicts.Single().Status.Should().Be("user_supplied");
    }

    [Fact]
    public void TheWireReportCarriesTheSeparateCounts_WithTheSamePropertyNamesAsPython()
    {
        var report = new GroundingReport(
        [
            new ClaimVerdict("amount", "$80.00", "user_supplied"),
            new ClaimVerdict("amount", "$25.00", "derived"),
            new ClaimVerdict("amount", "$9.00", "unverifiable"),
        ]);

        var wire = JsonSerializer.SerializeToElement(report.ToWire());

        wire.EnumerateObject().Select(p => p.Name).Should()
            .Equal("total", "verified", "unverified", "user_supplied", "derived", "claims");
        wire.GetProperty("verified").GetInt32().Should().Be(2);
        wire.GetProperty("user_supplied").GetInt32().Should().Be(1);
        wire.GetProperty("derived").GetInt32().Should().Be(1);
        wire.GetProperty("claims")[0].EnumerateObject().Select(p => p.Name).Should()
            .Equal("type", "id", "status", "detail", "source");
    }
}

/// <summary>
/// What <c>enforce</c> does to the text, and the mode dispatch around it (twin of Python's
/// <c>test_mode_grounding.py</c> and the middleware's <c>enforce</c> tests).
/// </summary>
public sealed class FinalTextGroundingTests
{
    private const string ProductId = "0fd372fa-ecb2-4db0-bb71-8628a784ced9";

    private static GroundingLedger LedgerWithWidget()
    {
        var ledger = new GroundingLedger();
        ledger.Record(new { id = ProductId, name = "X", price = 5.0m });
        return ledger;
    }

    private static string Card(string id, decimal price) =>
        $"```product\n{{\"name\": \"X\", \"id\": \"{id}\", \"price\": {price}}}\n```";

    private static Task<(GroundingReport? Report, string Text)> Run(string text, string mode, GroundingLedger? ledger = null) =>
        FinalTextGrounding.VerifyAsync(text, mode, ledger ?? LedgerWithWidget(), null, NullLogger.Instance);

    [Fact]
    public async Task Annotate_ReportsAndLeavesTheTextAlone()
    {
        var text = Card(ProductId, 5.0m);

        var (report, shown) = await Run(text, "annotate");

        shown.Should().Be(text);
        (report!.Total, report.Verified, report.Unverified).Should().Be((1, 1, 0));
    }

    [Theory]
    [InlineData("off")]
    [InlineData("observe")]
    public async Task OffAndObserve_ReportNothing(string mode)
    {
        var (report, shown) = await Run(Card(ProductId, 5.0m), mode);

        report.Should().BeNull();
        shown.Should().Be(Card(ProductId, 5.0m));
    }

    [Fact]
    public async Task AnAnswerWithNoCheckableClaim_SendsNoReport()
    {
        (await Run("Nothing to verify here.", "annotate")).Report.Should().BeNull();
        (await Run("Nothing to verify here.", "enforce")).Report.Should().BeNull();
    }

    [Fact]
    public async Task Enforce_CorrectsAWrongPrice_ToTheVerifiedOne()
    {
        var (report, shown) = await Run(Card(ProductId, 9.0m), "enforce");

        report!.Unverified.Should().Be(1);
        var card = JsonDocument.Parse(shown.Split('\n')[1]).RootElement;
        card.GetProperty("price").GetDecimal().Should().Be(5.0m);
        card.GetProperty("id").GetString().Should().Be(ProductId);
    }

    [Fact]
    public async Task Enforce_LeavesAVerifiedCardByteForByte()
    {
        var text = $"Here you go:\n\n{Card(ProductId, 5.0m)}\n\nAnything else?";

        (await Run(text, "enforce")).Text.Should().Be(text);
    }

    [Fact]
    public async Task Enforce_LeavesACardItCouldNotCheck()
    {
        // No ledger entry and no database: unverifiable, which must never strip a real card.
        var other = "22222222-2222-2222-2222-222222222222";

        var (report, shown) = await Run(Card(other, 5.0m), "enforce");

        report!.Verdicts.Single().Status.Should().Be("unverifiable");
        shown.Should().Be(Card(other, 5.0m));
    }

    [Fact]
    public async Task Enforce_FiltersAProductsBlockEntryByEntry_AndDropsAnEmptyOne()
    {
        var ledger = LedgerWithWidget();
        ledger.Record(new { id = "33333333-3333-3333-3333-333333333333", name = "Y", price = 7m });
        var mixed = $"```products\n[{{\"id\":\"{ProductId}\",\"price\":9.0}},{{\"id\":\"33333333-3333-3333-3333-333333333333\",\"price\":7}}]\n```";

        var (_, shown) = await Run(mixed, "enforce", ledger);

        var array = JsonDocument.Parse(shown.Split('\n')[1]).RootElement;
        array.GetArrayLength().Should().Be(2);
        array[0].GetProperty("price").GetDecimal().Should().Be(5.0m);
        array[1].GetProperty("price").GetDecimal().Should().Be(7m);
    }

    [Fact]
    public void ApplyCorrections_StripsACardWhoseProductDoesNotExist_AndAnEmptyProductsBlockGoesWithIt()
    {
        var report = new GroundingReport([new ClaimVerdict("product", ProductId, "not_found", null, "db")]);

        FinalTextGrounding.ApplyCorrections($"Try this:\n{Card(ProductId, 5m)}\nOr that.", report)
            .Should().Be("Try this:\n\nOr that.");
        FinalTextGrounding.ApplyCorrections($"```products\n[{{\"id\":\"{ProductId}\"}}]\n```", report).Should().BeEmpty();
    }

    [Fact]
    public void ApplyCorrections_LeavesAMalformedFenceAlone()
    {
        var report = new GroundingReport([new ClaimVerdict("product", ProductId, "not_found", null, "db")]);
        var broken = "```product\n{not json\n```";

        FinalTextGrounding.ApplyCorrections(broken, report).Should().Be(broken);
    }

    [Fact]
    public async Task AFailureInVerification_NeverTakesDownTheAnswer()
    {
        var text = Card(ProductId, 5m);
        // A pool pointing at nothing reachable: the database tier throws, the answer is returned as composed.
        var pool = new DatabasePool(new AgentSettings { DatabaseUrl = "Host=127.0.0.1;Port=1;Database=x;Username=x;Password=x;Timeout=1" });

        var (report, shown) = await FinalTextGrounding.VerifyAsync(
            text, "enforce", new GroundingLedger(), pool, NullLogger.Instance);

        report.Should().BeNull();
        shown.Should().Be(text);
    }
}

/// <summary>
/// The ledger stage and the specialist's grounding gate, through the real <see cref="SpecialistPipeline"/>.
/// </summary>
public sealed class GroundingPipelineTests
{
    private const string ProductId = "0fd372fa-ecb2-4db0-bb71-8628a784ced9";

    /// <summary>Calls one tool with fixed arguments, then answers with the configured text.</summary>
    private sealed class ToolThenAnswerClient(string toolName, IDictionary<string, object?> arguments, string answer) : IChatClient
    {
        public async Task<ChatResponse> GetResponseAsync(
            IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default)
        {
            var updates = new List<ChatResponseUpdate>();
            await foreach (var u in GetStreamingResponseAsync(messages, options, cancellationToken))
            {
                updates.Add(u);
            }

            return updates.ToChatResponse();
        }

        public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
            IEnumerable<ChatMessage> messages,
            ChatOptions? options = null,
            [EnumeratorCancellation] CancellationToken cancellationToken = default)
        {
            var hasResult = messages.Any(m => m.Contents.OfType<FunctionResultContent>().Any());
            await Task.CompletedTask;
            yield return hasResult
                ? new ChatResponseUpdate(ChatRole.Assistant, answer)
                : new ChatResponseUpdate(ChatRole.Assistant, [new FunctionCallContent("c1", toolName, arguments)]);
        }

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose()
        {
        }
    }

    private static IServiceProvider Services()
    {
        var services = new ServiceCollection();
        services.AddLogging();
        services.AddSingleton<AgentRunLogger>();
        services.AddSingleton<ToolAuditMiddleware>();
        services.AddSingleton<PiiRedactor>();
        services.AddSingleton(new AgentSettings());
        services.AddSingleton(new DatabasePool(new AgentSettings()));
        services.AddSingleton<HitlGate>();
        return services.BuildServiceProvider();
    }

    private static AIAgent Agent(string answer, string agentName, AgentSettings settings)
    {
        var tool = AgentTool.Create(
            (string sku) => new { id = ProductId, name = "Widget", price = 19.99m, image_url = (string?)null },
            "GetProductDetails");
        var client = new ToolThenAnswerClient("get_product_details", new Dictionary<string, object?> { ["sku"] = "XM5 under $30" }, answer);
        var inner = client.AsAIAgent(new ChatClientAgentOptions
        {
            Name = agentName,
            ChatOptions = new ChatOptions { Instructions = "be helpful", Tools = [tool] },
        });
        return SpecialistPipeline.Apply(inner, settings, Services(), agentName);
    }

    private static string Card(decimal price) =>
        $"```product\n{{\"name\": \"Widget\", \"id\": \"{ProductId}\", \"price\": {price}}}\n```";

    [Fact]
    public async Task EveryToolResult_AndItsArguments_LandInTheRequestLedger()
    {
        using var scope = RequestContext.Scope("u@example.com", "customer", "");
        var agent = Agent("done", "product-discovery", new AgentSettings { GroundingMode = "annotate" });

        await agent.RunAsync("find XM5");

        var ledger = RequestContext.CurrentGroundingLedger!;
        ledger.Products[ProductId].Price.Should().Be(19.99m);
        ledger.UserAmounts.Should().Contain(30m, "a figure the model passed as a tool argument is the caller's");
    }

    [Fact]
    public async Task WithGroundingOff_NothingIsRecorded()
    {
        using var scope = RequestContext.Scope("u@example.com", "customer", "");
        var agent = Agent("done", "product-discovery", new AgentSettings { GroundingMode = "off" });

        await agent.RunAsync("find XM5");

        RequestContext.CurrentGroundingLedger!.Products.Should().BeEmpty();
    }

    [Fact]
    public async Task ASpecialistInEnforceMode_CorrectsItsOwnAnswerAgainstWhatItsToolReturned()
    {
        using var scope = RequestContext.Scope("u@example.com", "customer", "");
        var agent = Agent(Card(9.00m), "product-discovery", new AgentSettings { GroundingMode = "enforce" });

        var response = await agent.RunAsync("find XM5");

        JsonDocument.Parse(response.Text.Split('\n')[1]).RootElement.GetProperty("price").GetDecimal().Should().Be(19.99m);
    }

    [Fact]
    public async Task ASpecialistInAnnotateMode_LeavesItsAnswerAsComposed()
    {
        using var scope = RequestContext.Scope("u@example.com", "customer", "");
        var agent = Agent(Card(9.00m), "product-discovery", new AgentSettings { GroundingMode = "annotate" });

        (await agent.RunAsync("find XM5")).Text.Should().Be(Card(9.00m));
    }

    [Fact]
    public async Task TheOrchestratorIsNotGatedHere_TheChatRouteVerifiesItsAnswer()
    {
        using var scope = RequestContext.Scope("u@example.com", "customer", "");
        var agent = Agent(Card(9.00m), "orchestrator", new AgentSettings { GroundingMode = "enforce" });

        (await agent.RunAsync("find XM5")).Text.Should().Be(Card(9.00m));
    }

    [Fact]
    public async Task AStreamedSpecialistAnswer_IsPassedThroughUntouched()
    {
        using var scope = RequestContext.Scope("u@example.com", "customer", "");
        var agent = Agent(Card(9.00m), "product-discovery", new AgentSettings { GroundingMode = "enforce" });

        var text = new System.Text.StringBuilder();
        await foreach (var update in agent.RunStreamingAsync("find XM5"))
        {
            text.Append(update.Text);
        }

        text.ToString().Should().Be(Card(9.00m), "a stream already on the wire cannot be un-sent");
    }
}
