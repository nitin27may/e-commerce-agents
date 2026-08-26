// MAF v1 — Chapter 32 tests (Cost Control and Budgets)
//
// This is the chapter whose .NET side can be tested where the Python side
// cannot. Python's ReplayChatClient composes FunctionInvocationLayer directly
// and skips ChatMiddlewareLayer, so CostBudgetChatMiddleware.process() never
// runs under LLM_PROVIDER=replay and the enforcement path is live-LLM-only.
// A DelegatingChatClient has no such gap: it wraps whatever it is handed. So
// everything below is gated on every PR, for free.
//
// The behaviours worth pinning are the two that look like bugs:
//
//   * Enforcement is one turn behind. The turn that crosses the ceiling still
//     runs; the next one is refused. A test asserting a hard cap would be
//     asserting something the design does not promise.
//   * A refusal is a response, not an exception — so a caller cannot forget to
//     handle it, and does not have to.

using FluentAssertions;
using MafV1.Shared.Testing;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Xunit;

namespace MafV1.Ch32.CostControl.Tests;

public sealed class CostControlTests
{
    /// <summary>1000 in / 1000 out per turn = $0.002 + $0.008 = $0.01 exactly.</summary>
    private static ScriptedChatClient Priced(string answer = "That costs $129.99.") =>
        new(_ => answer) { Usage = _ => (1000, 1000) };

    private static IChatClient Wrap(IChatClient inner, Budget budget) =>
        inner.AsBuilder().Use(next => new CostBudgetChatClient(next, budget)).Build();

    // ─────────────── Pricing ───────────────

    [Theory]
    [InlineData(0, 0, 0.0)]
    [InlineData(1000, 0, 0.002)]
    [InlineData(0, 1000, 0.008)]
    [InlineData(1000, 1000, 0.010)]
    [InlineData(500, 250, 0.003)]
    public void Cost_Is_Priced_Per_Thousand_Tokens_At_Different_Input_And_Output_Rates(
        int tokensIn, int tokensOut, double expected)
    {
        // Output costs 4x input. Pricing both at one rate is a common
        // simplification that understates a chatty agent's bill significantly.
        Pricing.EstimateUsd(tokensIn, tokensOut).Should().Be((decimal)expected);
    }

    // ─────────────── Accumulation ───────────────

    [Fact]
    public async Task Every_Turn_Adds_To_The_Running_Total()
    {
        var budget = new Budget(1.00m, BudgetMode.Observe);
        IChatClient client = Wrap(Priced(), budget);

        for (int i = 0; i < 3; i++)
        {
            await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "hi") });
        }

        budget.TurnsRecorded.Should().Be(3);
        budget.TotalUsd.Should().Be(0.03m);
    }

    [Fact]
    public async Task A_Turn_With_No_Usage_Is_Counted_Separately_Rather_Than_Priced_At_Zero()
    {
        // A provider that omits usage silently disables the budget. Treating
        // that as "free" is how a run goes unbounded without anything looking
        // wrong — so it gets its own counter.
        var budget = new Budget(1.00m, BudgetMode.Observe);
        IChatClient client = Wrap(new ScriptedChatClient(_ => "hi"), budget);

        await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "hi") });

        budget.TotalUsd.Should().Be(0m);
        budget.TurnsRecorded.Should().Be(0);
        budget.TurnsUnpriced.Should().Be(1);
    }

    // ─────────────── Observe mode ───────────────

    [Fact]
    public async Task Observe_Mode_Never_Blocks_Even_Far_Past_The_Ceiling()
    {
        // Production's default. It has to be safe to switch on everywhere,
        // which means it must never change behaviour.
        var budget = new Budget(0.001m, BudgetMode.Observe);
        IChatClient client = Wrap(Priced(), budget);

        for (int i = 0; i < 5; i++)
        {
            ChatResponse response = await client.GetResponseAsync(
                new[] { new ChatMessage(ChatRole.User, "hi") });
            response.Text.Should().NotContain("cost budget");
        }

        budget.IsOverBudget.Should().BeTrue();
        budget.TurnsBlocked.Should().Be(0);
        budget.TurnsRecorded.Should().Be(5);
    }

    [Fact]
    public async Task Off_Mode_Does_Not_Even_Account()
    {
        var budget = new Budget(0.001m, BudgetMode.Off);
        IChatClient client = Wrap(Priced(), budget);

        await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "hi") });

        budget.TotalUsd.Should().Be(0m);
        budget.TurnsRecorded.Should().Be(0);
    }

    // ─────────────── Enforce mode ───────────────

    [Fact]
    public async Task The_Turn_That_Crosses_The_Ceiling_Still_Completes()
    {
        // Enforcement is necessarily one turn behind: cost is only knowable
        // after a turn finishes. Asserting a hard cap here would be asserting
        // something the design does not promise, and cannot.
        var budget = new Budget(0.005m, BudgetMode.Enforce);
        var inner = Priced();
        IChatClient client = Wrap(inner, budget);

        ChatResponse response = await client.GetResponseAsync(
            new[] { new ChatMessage(ChatRole.User, "hi") });

        response.Text.Should().NotContain("cost budget");
        budget.TotalUsd.Should().Be(0.010m).And.BeGreaterThan(budget.LimitUsd);
        inner.Calls.Should().HaveCount(1);
    }

    [Fact]
    public async Task The_Next_Turn_After_The_Ceiling_Is_Refused()
    {
        var budget = new Budget(0.005m, BudgetMode.Enforce);
        var inner = Priced();
        IChatClient client = Wrap(inner, budget);

        await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "one") });
        ChatResponse second = await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "two") });

        second.Text.Should().Be(CostBudgetChatClient.RefusalMessage);
        budget.TurnsBlocked.Should().Be(1);
    }

    [Fact]
    public async Task A_Refused_Turn_Never_Reaches_The_Provider()
    {
        // The point of the whole exercise: not calling the provider is what
        // actually saves the money. A refusal that still made the call would
        // be a very expensive log line.
        var budget = new Budget(0.005m, BudgetMode.Enforce);
        var inner = Priced();
        IChatClient client = Wrap(inner, budget);

        await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "one") });
        await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "two") });
        await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "three") });

        inner.Calls.Should().HaveCount(1, "only the first turn was under budget");
        budget.TurnsBlocked.Should().Be(2);
    }

    [Fact]
    public async Task A_Refusal_Is_A_Response_Not_An_Exception()
    {
        // So a caller cannot forget to handle it. The finish reason is what
        // lets a caller distinguish a refusal from an ordinary short answer.
        var budget = new Budget(0m, BudgetMode.Enforce);
        IChatClient client = Wrap(Priced(), budget);

        await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "one") });
        ChatResponse refused = await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "two") });

        refused.FinishReason.Should().Be(ChatFinishReason.Length);
        refused.Text.Should().Contain("raise the budget");
    }

    [Fact]
    public async Task A_Refused_Turn_Does_Not_Add_To_The_Running_Total()
    {
        // It made no call, so it cost nothing. A budget that keeps climbing
        // while refusing would make the final report meaningless.
        var budget = new Budget(0.005m, BudgetMode.Enforce);
        IChatClient client = Wrap(Priced(), budget);

        await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "one") });
        decimal afterFirst = budget.TotalUsd;
        await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "two") });

        budget.TotalUsd.Should().Be(afterFirst);
        budget.TurnsRecorded.Should().Be(1);
    }

    [Fact]
    public async Task Exactly_Hitting_The_Ceiling_Does_Not_Trip_It()
    {
        // Strictly greater-than, not greater-or-equal. Worth pinning either
        // way, because an off-by-one here refuses a run that was within budget.
        var budget = new Budget(0.010m, BudgetMode.Enforce);
        var inner = Priced();
        IChatClient client = Wrap(inner, budget);

        await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "one") });
        budget.TotalUsd.Should().Be(0.010m);

        ChatResponse second = await client.GetResponseAsync(new[] { new ChatMessage(ChatRole.User, "two") });

        second.Text.Should().NotContain("cost budget");
        inner.Calls.Should().HaveCount(2);
    }

    // ─────────────── Streaming ───────────────

    [Fact]
    public async Task Streaming_Turns_Accumulate_Usage_Too()
    {
        // Usage arrives as a UsageContent item on the stream, not on a response
        // object. Missing it is how a streaming agent gets a budget that never
        // accumulates and therefore never trips.
        var budget = new Budget(1.00m, BudgetMode.Observe);
        IChatClient client = Wrap(Priced(), budget);

        await foreach (ChatResponseUpdate _ in client.GetStreamingResponseAsync(
            new[] { new ChatMessage(ChatRole.User, "hi") }))
        {
        }

        budget.TotalUsd.Should().Be(0.010m);
        budget.TurnsRecorded.Should().Be(1);
    }

    [Fact]
    public async Task Streaming_Is_Refused_Once_Over_Budget()
    {
        var budget = new Budget(0.005m, BudgetMode.Enforce);
        var inner = Priced();
        IChatClient client = Wrap(inner, budget);

        await foreach (ChatResponseUpdate _ in client.GetStreamingResponseAsync(
            new[] { new ChatMessage(ChatRole.User, "one") }))
        {
        }

        var text = string.Empty;
        await foreach (ChatResponseUpdate update in client.GetStreamingResponseAsync(
            new[] { new ChatMessage(ChatRole.User, "two") }))
        {
            text += update.Text;
        }

        text.Should().Be(CostBudgetChatClient.RefusalMessage);
        inner.Calls.Should().HaveCount(1);
    }

    // ─────────────── The agent, end to end ───────────────

    [Fact]
    public async Task A_Tool_Calling_Loop_Costs_One_Budgeted_Turn_Per_Model_Round_Trip()
    {
        // The reason the budget client sits outside function invocation. A
        // two-turn tool call should cost twice — if it only counted once, an
        // agent that loops through ten tool calls would look as cheap as one.
        var budget = new Budget(1.00m, BudgetMode.Observe);
        var inner = new ScriptedChatClient(call =>
                call.Text.Contains("$129.99")
                    ? ScriptedChatClient.Text("It costs $129.99.")
                    : ScriptedChatClient.ToolCall(
                        nameof(Program.GetProductPrice),
                        new Dictionary<string, object?> { ["productId"] = "P-100" }))
            { Usage = _ => (1000, 1000) };

        AIAgent agent = Program.BuildAgent(inner, budget);

        string answer = await Program.AskAsync(agent, "What's the price of product P-100?");

        answer.Should().Contain("129.99");
        budget.TurnsRecorded.Should().Be(2, "one turn to call the tool, one to read the result");
        budget.TotalUsd.Should().Be(0.020m);
    }

    [Fact]
    public void The_Canned_Catalogue_Answers_Known_Ids_And_Says_So_For_Unknown_Ones()
    {
        Program.GetProductPrice("P-100").Should().Be("$129.99");
        Program.GetProductPrice("p-300").Should().Be("$899.00");
        Program.GetProductPrice("P-999").Should().Contain("No price found");
    }

    [Fact]
    public void The_Demo_Budget_Is_Small_Enough_To_Trip_Within_A_Few_Turns()
    {
        // Guards the thing that makes the demo a demo. If someone bumps this
        // to a realistic production figure, main() runs to completion without
        // ever showing a refusal and the chapter demonstrates nothing.
        Program.DemoBudgetUsdPerRun.Should().BeLessThan(0.01m);
    }
}
