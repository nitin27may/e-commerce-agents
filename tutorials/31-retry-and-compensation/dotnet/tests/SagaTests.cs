// MAF v1 — Chapter 31 tests (Retry and Compensation)
//
// No LLM, no framework — this chapter is plain orchestration logic, which
// makes it the one chapter where the tests can assert the *state of the world*
// after a failure rather than just the return value. That is the point of the
// saga pattern, so that is what most of these check: after an unwind, the
// three toy services must look exactly as they did before the order started.
//
// A result object saying "compensated" while stock stays decremented is the
// exact bug the pattern exists to prevent, and it is invisible from the return
// value alone.

using FluentAssertions;
using Xunit;

namespace MafV1.Ch31.Saga.Tests;

public sealed class SagaTests
{
    private static Task<SagaResult> Run(
        Backends backends,
        string orderId,
        bool failPayment = false,
        bool failShipment = false,
        int quantity = 2,
        string productId = "widget",
        int maxAttempts = 3) =>
        SagaRunner.RunAsync(
            orderId,
            Program.BuildPlaceOrderSaga(backends, orderId, productId, quantity, 49.99m, failPayment, failShipment),
            maxAttempts: maxAttempts);

    // ─────────────── Happy path ───────────────

    [Fact]
    public async Task All_Three_Steps_Succeed_And_Nothing_Is_Compensated()
    {
        var backends = new Backends();

        SagaResult result = await Run(backends, "order-1");

        result.Succeeded.Should().BeTrue();
        result.CompletedSteps.Should().Equal("reserve_stock", "charge_payment", "create_shipment");
        result.FailedStep.Should().BeNull();
        result.Compensated.Should().BeEmpty();
    }

    [Fact]
    public async Task A_Successful_Order_Leaves_All_Three_Services_Updated()
    {
        var backends = new Backends();

        await Run(backends, "order-1");

        backends.Stock["widget"].Should().Be(8);
        backends.Reservations["widget"].Should().Be(2);
        backends.Payments["order-1"].Should().Be(49.99m);
        backends.Shipments["order-1"].Should().Be("created");
    }

    // ─────────────── Retry ───────────────

    [Fact]
    public async Task A_Transient_Failure_On_A_Retryable_Step_Is_Retried_Until_It_Succeeds()
    {
        var backends = new Backends(reserveStockFlakyCalls: 2);

        SagaResult result = await Run(backends, "order-2");

        result.Succeeded.Should().BeTrue();
        backends.ReserveAttempts.Should().Be(3, "two failures then a success");
    }

    [Fact]
    public async Task Retries_Are_Bounded_By_MaxAttempts()
    {
        // The service never recovers. The saga must give up and compensate
        // rather than retry forever — an unbounded retry against a service
        // that is genuinely down is how one outage becomes two.
        var backends = new Backends(reserveStockFlakyCalls: 99);

        SagaResult result = await Run(backends, "order-2", maxAttempts: 3);

        result.Succeeded.Should().BeFalse();
        result.FailedStep.Should().Be("reserve_stock");
        backends.ReserveAttempts.Should().Be(3);
    }

    [Fact]
    public async Task A_Retried_Step_That_Eventually_Succeeds_Is_Not_Applied_Twice()
    {
        // Retry plus a non-idempotent action is how customers get charged
        // twice. Here: two failed reservations must not decrement stock.
        var backends = new Backends(reserveStockFlakyCalls: 2);

        await Run(backends, "order-2", quantity: 3);

        backends.Stock["widget"].Should().Be(7, "one successful reservation of 3, not three of them");
        backends.Reservations["widget"].Should().Be(3);
    }

    // ─────────────── Genuine failure and unwind ───────────────

    [Fact]
    public async Task A_Declined_Payment_Compensates_Immediately_Without_Retrying()
    {
        var backends = new Backends();

        SagaResult result = await Run(backends, "order-3", failPayment: true);

        result.Succeeded.Should().BeFalse();
        result.FailedStep.Should().Be("charge_payment");
        result.CompletedSteps.Should().Equal("reserve_stock");
        result.Compensated.Should().Equal("reserve_stock");
        backends.ReserveAttempts.Should().Be(1, "a decline is not transient — no retry");
    }

    [Fact]
    public async Task After_A_Declined_Payment_The_Stock_Is_Exactly_Back_Where_It_Started()
    {
        // The assertion that actually proves compensation happened. A result
        // object reporting "compensated" while stock stays decremented is the
        // precise bug this pattern exists to prevent.
        var before = new Backends().Stock["widget"];
        var backends = new Backends();

        await Run(backends, "order-3", failPayment: true);

        backends.Stock["widget"].Should().Be(before);
        backends.Reservations.GetValueOrDefault("widget").Should().Be(0);
        backends.Payments.Should().NotContainKey("order-3", "a failed charge must leave no payment record");
    }

    [Fact]
    public async Task A_Shipment_Failure_Unwinds_Both_Earlier_Steps_In_Reverse_Order()
    {
        // Reverse order is not cosmetic — a later step can depend on an
        // earlier one, so undoing forwards can fail halfway and leave worse
        // state than doing nothing.
        var backends = new Backends();

        SagaResult result = await Run(backends, "order-4", failShipment: true);

        result.CompletedSteps.Should().Equal("reserve_stock", "charge_payment");
        result.Compensated.Should().Equal("charge_payment", "reserve_stock");

        backends.Payments.Should().NotContainKey("order-4");
        backends.Stock["widget"].Should().Be(10);
    }

    [Fact]
    public async Task An_Out_Of_Stock_Failure_On_The_First_Step_Has_Nothing_To_Compensate()
    {
        // The edge case that gets written wrong: failing at step one means the
        // unwind loop runs zero times, and an implementation that assumes at
        // least one completed step throws here instead of returning cleanly.
        var backends = new Backends();

        SagaResult result = await Run(backends, "order-5", productId: "gadget", quantity: 1);

        result.Succeeded.Should().BeFalse();
        result.FailedStep.Should().Be("reserve_stock");
        result.CompletedSteps.Should().BeEmpty();
        result.Compensated.Should().BeEmpty();
    }

    [Fact]
    public async Task An_Out_Of_Stock_Failure_Is_Not_Retried()
    {
        // Retrying will not conjure inventory. Getting this wrong turns an
        // instant, correct "no" into three round trips and the same "no".
        var backends = new Backends();

        await Run(backends, "order-5", productId: "gadget", quantity: 1);

        backends.ReserveAttempts.Should().Be(1);
    }

    [Fact]
    public async Task A_Cancelled_Shipment_Is_Marked_Rather_Than_Deleted()
    {
        // Deliberate asymmetry with refund_payment, which does remove its
        // record. The carrier already knows about the shipment; erasing it
        // would lose the audit trail a saga exists to provide.
        var backends = new Backends();
        var steps = new List<SagaStep>(Program.BuildPlaceOrderSaga(backends, "order-6", "widget", 1, 10m))
        {
            new("always_fails", () => throw new InvalidOperationException("boom"), () => { }),
        };

        await SagaRunner.RunAsync("order-6", steps);

        backends.Shipments["order-6"].Should().Be("cancelled");
    }

    // ─────────────── Engine-level behaviour ───────────────

    [Fact]
    public async Task Steps_Run_In_Declared_Order()
    {
        var order = new List<string>();
        var steps = new[]
        {
            new SagaStep("one", () => order.Add("one"), () => { }),
            new SagaStep("two", () => order.Add("two"), () => { }),
            new SagaStep("three", () => order.Add("three"), () => { }),
        };

        await SagaRunner.RunAsync("order-7", steps);

        order.Should().Equal("one", "two", "three");
    }

    [Fact]
    public async Task A_Step_After_The_Failure_Never_Runs()
    {
        bool laterRan = false;
        var steps = new[]
        {
            new SagaStep("ok", () => { }, () => { }),
            new SagaStep("boom", () => throw new InvalidOperationException("boom"), () => { }),
            new SagaStep("later", () => laterRan = true, () => { }),
        };

        await SagaRunner.RunAsync("order-8", steps);

        laterRan.Should().BeFalse();
    }

    [Fact]
    public async Task A_Transient_Failure_On_A_Non_Retryable_Step_Compensates_Instead_Of_Retrying()
    {
        // Retryable is opt-in per step. A transient error on a step that did
        // not opt in must still be treated as terminal — otherwise the flag
        // means nothing.
        int attempts = 0;
        var steps = new[]
        {
            new SagaStep("flaky-but-not-retryable", () =>
            {
                attempts++;
                throw new TransientException("blip");
            }, () => { }),
        };

        SagaResult result = await SagaRunner.RunAsync("order-9", steps, maxAttempts: 5);

        attempts.Should().Be(1);
        result.Succeeded.Should().BeFalse();
    }

    [Fact]
    public async Task An_Empty_Saga_Succeeds_Trivially()
    {
        SagaResult result = await SagaRunner.RunAsync("order-10", Array.Empty<SagaStep>());

        result.Succeeded.Should().BeTrue();
        result.CompletedSteps.Should().BeEmpty();
    }
}
