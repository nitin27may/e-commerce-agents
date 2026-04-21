using ECommerceAgents.Shared.Workflows;
using FluentAssertions;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

public sealed class PrePurchaseWorkflowTests
{
    // ─────────────────────── happy path ──────────────────────

    [Fact]
    public async Task Execute_CompletesAllStepsWhenInStock()
    {
        var tools = new StubTools
        {
            Reviews = Parse(@"{""sentiment"":""positive"",""total_reviews"":12}"),
            Stock = Parse(@"{""in_stock"":true,""total_quantity"":50}"),
            PriceHistory = Parse(@"{""is_good_deal"":true,""average_price"":149}"),
            Shipping = Parse(@"{""options"":[{""price"":9.99,""days"":3}]}"),
        };

        var wf = new PrePurchaseWorkflow(tools);
        var state = await wf.ExecuteAsync(new ResearchState("p-1", "east"));

        state.CompletedSteps.Should().BeEquivalentTo(
            new[] { "reviews", "stock", "price_history", "shipping" },
            opts => opts.WithoutStrictOrdering()
        );
        state.Errors.Should().BeEmpty();
        state.Recommendation.Should().Contain("Reviews: positive (12 reviews)");
        state.Recommendation.Should().Contain("Stock: 50 units");
        state.Recommendation.Should().Contain("Good deal");
        state.Recommendation.Should().Contain("Shipping: from $9.99");
    }

    [Fact]
    public async Task Execute_FansOutInParallel()
    {
        var gate = new TaskCompletionSource();
        var tools = new StubTools
        {
            Reviews = Parse(@"{""sentiment"":""neutral""}"),
            Stock = Parse(@"{""in_stock"":false}"),
            PriceHistory = Parse(@"{""trend"":""falling""}"),
            // Hold each tool until all three are invoked; forces parallelism.
            BeforeEach = async () =>
            {
                await gate.Task;
            },
        };

        var wf = new PrePurchaseWorkflow(tools);
        var task = wf.ExecuteAsync(new ResearchState("p-2"));

        // All three fan-out calls start concurrently.
        await Task.Delay(50);
        tools.InvocationCount.Should().Be(3);
        gate.SetResult();

        var state = await task;
        state.CompletedSteps.Should().Contain(new[] { "reviews", "stock", "price_history" });
        state.CompletedSteps.Should().NotContain("shipping"); // not in stock
    }

    // ─────────────────────── out-of-stock skip ───────────────

    [Fact]
    public async Task Execute_SkipsShippingWhenOutOfStock()
    {
        var tools = new StubTools
        {
            Reviews = Parse(@"{""sentiment"":""mixed""}"),
            Stock = Parse(@"{""in_stock"":false}"),
            PriceHistory = Parse(@"{""trend"":""stable""}"),
        };

        var wf = new PrePurchaseWorkflow(tools);
        var state = await wf.ExecuteAsync(new ResearchState("p-3"));

        state.Shipping.Should().BeNull();
        state.CompletedSteps.Should().NotContain("shipping");
        state.Recommendation.Should().Contain("Currently out of stock");
        state.Recommendation.Should().Contain("Price trend: stable");
    }

    // ─────────────────────── degrade gracefully ─────────────

    [Fact]
    public async Task Execute_RecordsErrorButContinuesOnToolFailure()
    {
        var tools = new StubTools
        {
            Reviews = Parse(@"{""sentiment"":""positive""}"),
            StockThrows = new InvalidOperationException("db timeout"),
            PriceHistory = Parse(@"{""trend"":""rising""}"),
        };

        var wf = new PrePurchaseWorkflow(tools);
        var state = await wf.ExecuteAsync(new ResearchState("p-4"));

        state.Errors.Should().ContainSingle().Which.Should().Contain("stock").And.Contain("db timeout");
        state.CompletedSteps.Should().Contain("reviews");
        state.CompletedSteps.Should().Contain("price_history");
        state.CompletedSteps.Should().NotContain("stock");
        state.Recommendation.Should().NotBeEmpty();
    }

    [Fact]
    public async Task Execute_EmptyRecommendationWhenNoData()
    {
        var tools = new StubTools
        {
            Reviews = Parse("{}"),
            Stock = Parse("{}"),
            PriceHistory = Parse("{}"),
        };

        var wf = new PrePurchaseWorkflow(tools);
        var state = await wf.ExecuteAsync(new ResearchState("p-5"));

        // "Currently out of stock" still emitted from the stock block.
        state.Recommendation.Should().Contain("Currently out of stock");
    }

    // ─────────────────────── helpers ─────────────────────────

    private static JsonElement Parse(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    private sealed class StubTools : IPrePurchaseTools
    {
        public JsonElement? Reviews { get; set; }
        public JsonElement? Stock { get; set; }
        public JsonElement? PriceHistory { get; set; }
        public JsonElement? Shipping { get; set; }
        public Exception? StockThrows { get; set; }
        public Func<Task>? BeforeEach { get; set; }
        public int InvocationCount;

        public async Task<JsonElement> AnalyzeSentimentAsync(string productId, CancellationToken ct = default)
        {
            Interlocked.Increment(ref InvocationCount);
            if (BeforeEach is not null) await BeforeEach();
            return Reviews ?? throw new InvalidOperationException("no reviews stub");
        }

        public async Task<JsonElement> CheckStockAsync(string productId, CancellationToken ct = default)
        {
            Interlocked.Increment(ref InvocationCount);
            if (BeforeEach is not null) await BeforeEach();
            if (StockThrows is not null) throw StockThrows;
            return Stock ?? throw new InvalidOperationException("no stock stub");
        }

        public async Task<JsonElement> GetPriceHistoryAsync(string productId, int days, CancellationToken ct = default)
        {
            Interlocked.Increment(ref InvocationCount);
            if (BeforeEach is not null) await BeforeEach();
            return PriceHistory ?? throw new InvalidOperationException("no price stub");
        }

        public Task<JsonElement> EstimateShippingAsync(string productId, string destinationRegion, CancellationToken ct = default)
            => Task.FromResult(Shipping ?? throw new InvalidOperationException("no shipping stub"));
    }
}
