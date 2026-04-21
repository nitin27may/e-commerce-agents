using ECommerceAgents.Shared.Workflows;
using FluentAssertions;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

public sealed class ReturnAndReplaceWorkflowTests
{
    // ─────────────────────── happy path ──────────────────────

    [Fact]
    public async Task Execute_LowValue_RunsAllStepsAndAutoApproves()
    {
        var tools = new StubTools
        {
            Eligibility = new ReturnEligibility(true),
            Initiate = new InitiateReturnResult("ret-123", 50m),
            Replacements = new[] { Product("Widget A") },
            Loyalty = new LoyaltyInfo("silver", 10m),
        };
        var wf = new ReturnAndReplaceWorkflow(tools, hitlThreshold: 500m);

        var state = new WorkflowState("c@example.com", "order-1") { OrderTotal = 100m };
        var result = await wf.ExecuteAsync(state);

        result.HitlRequested.Should().BeFalse();
        result.HitlApproved.Should().BeTrue();
        result.ReturnEligible.Should().BeTrue();
        result.ReturnId.Should().Be("ret-123");
        result.RefundAmount.Should().Be(50m);
        result.ReplacementProducts.Should().HaveCount(1);
        result.AppliedDiscount.Should().NotBeNull();
        result.AppliedDiscount!.Tier.Should().Be("silver");
        result.CompletedSteps.Should().BeEquivalentTo(
            "check_eligibility",
            "initiate_return",
            "search_replacements",
            "hitl_gate",
            "apply_discount",
            "finalize"
        );
    }

    // ─────────────────────── HITL pause/resume ───────────────

    [Fact]
    public async Task Execute_HighValue_PausesAtHitlGate()
    {
        var tools = new StubTools
        {
            Eligibility = new ReturnEligibility(true),
            Initiate = new InitiateReturnResult("ret-hv", 800m),
            Replacements = new[] { Product("Widget B"), Product("Widget C") },
            Loyalty = new LoyaltyInfo("gold", 20m),
        };
        var wf = new ReturnAndReplaceWorkflow(tools, hitlThreshold: 500m);

        var state = new WorkflowState("vip@example.com", "order-2") { OrderTotal = 800m };
        var paused = await wf.ExecuteAsync(state);

        paused.HitlRequested.Should().BeTrue();
        paused.HitlApproved.Should().BeNull();
        paused.CompletedSteps.Should().NotContain("apply_discount");
        paused.CompletedSteps.Should().NotContain("finalize");
        tools.GetLoyaltyCalls.Should().Be(0); // discount not applied yet

        var request = wf.BuildApprovalRequest(paused);
        request.OrderId.Should().Be("order-2");
        request.OrderTotal.Should().Be(800m);
        request.RefundAmount.Should().Be(800m);
        request.ReplacementCount.Should().Be(2);
    }

    [Fact]
    public async Task Resume_Approves_CompletesChain()
    {
        var tools = new StubTools
        {
            Eligibility = new ReturnEligibility(true),
            Initiate = new InitiateReturnResult("ret-hv", 900m),
            Replacements = Array.Empty<JsonElement>(),
            Loyalty = new LoyaltyInfo("gold", 20m),
        };
        var wf = new ReturnAndReplaceWorkflow(tools, hitlThreshold: 500m);

        var state = new WorkflowState("vip@example.com", "order-3") { OrderTotal = 900m };
        var paused = await wf.ExecuteAsync(state);
        paused.HitlRequested.Should().BeTrue();

        var final = await wf.ResumeAsync(paused, approved: true);
        final.HitlApproved.Should().BeTrue();
        final.AppliedDiscount!.DiscountPct.Should().Be(20m);
        final.CompletedSteps.Should().EndWith("finalize");
        final.CompletedSteps.Should().Contain("apply_discount");
    }

    [Fact]
    public async Task Resume_Rejects_AppendsErrorAndSkipsDiscount()
    {
        var tools = new StubTools
        {
            Eligibility = new ReturnEligibility(true),
            Initiate = new InitiateReturnResult("ret-hv", 1500m),
            Replacements = Array.Empty<JsonElement>(),
            Loyalty = new LoyaltyInfo("gold", 20m),
        };
        var wf = new ReturnAndReplaceWorkflow(tools, hitlThreshold: 500m);

        var state = new WorkflowState("vip@example.com", "order-4") { OrderTotal = 1500m };
        var paused = await wf.ExecuteAsync(state);
        var final = await wf.ResumeAsync(paused, approved: false);

        final.HitlApproved.Should().BeFalse();
        final.Errors.Should().ContainSingle()
            .Which.Should().Contain("hitl_gate").And.Contain("rejected");
        final.CompletedSteps.Should().NotContain("apply_discount");
        final.CompletedSteps.Should().NotContain("finalize");
        tools.GetLoyaltyCalls.Should().Be(0);
    }

    [Fact]
    public async Task Resume_WithoutPause_Throws()
    {
        var wf = new ReturnAndReplaceWorkflow(new StubTools());
        var state = new WorkflowState("x", "order-5");
        await Assert.ThrowsAsync<InvalidOperationException>(() => wf.ResumeAsync(state, approved: true));
    }

    // ─────────────────────── eligibility gate ────────────────

    [Fact]
    public async Task Execute_NotEligible_HaltsEarly()
    {
        var tools = new StubTools
        {
            Eligibility = new ReturnEligibility(false, "Order already returned"),
        };
        var wf = new ReturnAndReplaceWorkflow(tools);
        var state = new WorkflowState("c", "order-6") { OrderTotal = 100m };
        var result = await wf.ExecuteAsync(state);

        result.ReturnEligible.Should().BeFalse();
        result.Errors.Should().ContainSingle().Which.Should().Be("Order already returned");
        result.CompletedSteps.Should().BeEquivalentTo(new[] { "check_eligibility" });
        tools.InitiateCalls.Should().Be(0);
    }

    [Fact]
    public async Task Execute_InitiateReturnError_HaltsChain()
    {
        var tools = new StubTools
        {
            Eligibility = new ReturnEligibility(true),
            Initiate = new InitiateReturnResult(null, 0m, Error: "already_requested"),
        };
        var wf = new ReturnAndReplaceWorkflow(tools);
        var state = new WorkflowState("c", "order-7") { OrderTotal = 100m };
        var result = await wf.ExecuteAsync(state);

        result.Errors.Should().ContainSingle().Which.Should().Contain("already_requested");
        result.CompletedSteps.Should().NotContain("search_replacements");
    }

    [Fact]
    public async Task Execute_SearchFailure_StillProceeds()
    {
        var tools = new StubTools
        {
            Eligibility = new ReturnEligibility(true),
            Initiate = new InitiateReturnResult("ret", 40m),
            SearchThrows = new TimeoutException("db slow"),
            Loyalty = new LoyaltyInfo("bronze", 0m),
        };
        var wf = new ReturnAndReplaceWorkflow(tools);
        var state = new WorkflowState("c", "order-8") { OrderTotal = 40m };
        var result = await wf.ExecuteAsync(state);

        result.Errors.Should().ContainSingle().Which.Should().Contain("search_replacements");
        result.HitlApproved.Should().BeTrue(); // below threshold
        result.AppliedDiscount.Should().BeNull(); // 0% tier
        result.CompletedSteps.Should().Contain("finalize");
    }

    // ─────────────────────── helpers ─────────────────────────

    private static JsonElement Product(string name)
    {
        using var doc = JsonDocument.Parse($"{{\"name\":\"{name}\"}}");
        return doc.RootElement.Clone();
    }

    private sealed class StubTools : IReturnReplaceTools
    {
        public ReturnEligibility Eligibility { get; set; } = new(true);
        public InitiateReturnResult Initiate { get; set; } = new(null, 0m);
        public IEnumerable<JsonElement> Replacements { get; set; } = Array.Empty<JsonElement>();
        public Exception? SearchThrows { get; set; }
        public LoyaltyInfo? Loyalty { get; set; }

        public int InitiateCalls;
        public int GetLoyaltyCalls;

        public Task<ReturnEligibility> CheckReturnEligibilityAsync(string orderId, CancellationToken ct = default)
            => Task.FromResult(Eligibility);

        public Task<InitiateReturnResult> InitiateReturnAsync(
            string orderId,
            string reason,
            string refundMethod,
            CancellationToken ct = default
        )
        {
            Interlocked.Increment(ref InitiateCalls);
            return Task.FromResult(Initiate);
        }

        public Task<IReadOnlyList<JsonElement>> SearchReplacementsAsync(
            decimal maxPrice,
            decimal minRating,
            int limit,
            CancellationToken ct = default
        )
        {
            if (SearchThrows is not null) throw SearchThrows;
            return Task.FromResult<IReadOnlyList<JsonElement>>(Replacements.ToList());
        }

        public Task<LoyaltyInfo?> GetLoyaltyTierAsync(CancellationToken ct = default)
        {
            Interlocked.Increment(ref GetLoyaltyCalls);
            return Task.FromResult(Loyalty);
        }
    }
}
