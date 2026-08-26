// MAF v1 — Chapter 29 tests (Planner-Executor)
//
// The pattern's whole selling point is that the plan exists before anything
// runs, so it can be read, approved, or costed. Two things have to hold for
// that to be true, and neither is visible from the API:
//
//   * The planner must not execute. It has no tools, and if that ever changes
//     you get a reactive agent that also emits a plan it stopped following.
//   * Every step must share one session, or a "pick the best candidate" step
//     has nothing to pick from — and will confidently invent something,
//     without erroring.
//
// The scripted client tells planner from executor by instructions, and answers
// the planner with a JSON plan.

using FluentAssertions;
using MafV1.Shared.Testing;
using Microsoft.Agents.AI;
using Xunit;

namespace MafV1.Ch29.PlannerExecutor.Tests;

public sealed class PlannerExecutorTests
{
    private const string ThreeStepPlan = """
        {
          "goal": "Find a photography gift under $200.",
          "steps": [
            {"step": 1, "action": "Search the catalog for photography products.", "query": "photography camera"},
            {"step": 2, "action": "Narrow the results to items under $200.", "query": null},
            {"step": 3, "action": "Recommend the best candidate.", "query": null}
          ]
        }
        """;

    private static bool IsPlanner(ScriptedCall call) => call.Instructions == Program.PlannerInstructions;
    private static bool IsExecutor(ScriptedCall call) => call.Instructions == Program.ExecutorInstructions;

    private static ScriptedChatClient Scripted(string plan = ThreeStepPlan)
    {
        int stepAnswers = 0;
        return new ScriptedChatClient(call =>
            IsPlanner(call) ? plan : $"STEP-RESULT-{++stepAnswers}");
    }

    // ─────────────── The catalogue tool ───────────────

    [Fact]
    public void SearchProducts_Matches_On_Name_Category_And_Description()
    {
        Program.SearchProducts("tripod").Should().Contain("Travel Camera Tripod");
        Program.SearchProducts("photography").Should().Contain("50mm Prime Lens");
        Program.SearchProducts("softbox").Should().Contain("Professional Studio Light Kit");
    }

    [Fact]
    public void SearchProducts_Honours_An_Inclusive_Price_Ceiling()
    {
        // Inclusive, not exclusive. An item priced at exactly the ceiling is
        // within budget, and dropping it is the kind of off-by-one a user
        // notices immediately.
        string results = Program.SearchProducts("photography", maxPrice: 189);

        results.Should().Contain("Compact Mirrorless Camera");
        results.Should().NotContain("Professional Studio Light Kit");
    }

    [Fact]
    public void SearchProducts_Says_So_When_Nothing_Matches_And_Repeats_The_Cap()
    {
        // The cap has to appear in the message. "No products found for
        // 'camera'" reads as "we don't sell cameras" when the real answer is
        // "not at that price".
        string results = Program.SearchProducts("camera", maxPrice: 5);

        results.Should().Contain("No products found").And.Contain("under $5");
    }

    [Fact]
    public void SearchProducts_Does_Not_Return_Unrelated_Categories()
    {
        Program.SearchProducts("photography").Should().NotContain("Espresso Machine");
    }

    // ─────────────── Planning ───────────────

    [Fact]
    public async Task The_Planner_Returns_A_Structured_Plan()
    {
        var fake = Scripted();

        Plan plan = await Program.MakePlanAsync(Program.BuildPlannerAgent(fake), Program.DefaultRequest);

        plan.Goal.Should().Contain("photography");
        plan.Steps.Should().HaveCount(3);
        plan.Steps.Select(s => s.Step).Should().Equal(1, 2, 3);
    }

    [Fact]
    public async Task The_Planner_Is_Given_No_Tools()
    {
        // The design, not an omission. A planner holding the search tool will
        // search — and the plan becomes a description of what it already did.
        var fake = Scripted();

        await Program.MakePlanAsync(Program.BuildPlannerAgent(fake), Program.DefaultRequest);

        fake.Calls.Single().Tools.Should().BeEmpty();
    }

    [Fact]
    public async Task A_Search_Step_Carries_A_Query_And_A_Reasoning_Step_Does_Not()
    {
        // Null Query is the signal the executor branches on. If the planner
        // filled it in for every step, every step would search — including the
        // ones whose job is to reason over what earlier steps found.
        var fake = Scripted();

        Plan plan = await Program.MakePlanAsync(Program.BuildPlannerAgent(fake), Program.DefaultRequest);

        plan.Steps[0].Query.Should().NotBeNullOrWhiteSpace();
        plan.Steps[1].Query.Should().BeNull();
        plan.Steps[2].Query.Should().BeNull();
    }

    [Fact]
    public async Task An_Unparseable_Plan_Throws_With_The_Raw_Text()
    {
        // Executing a half-parsed plan is worse than not executing one. The
        // raw text matters: without it the failure is unactionable.
        var fake = new ScriptedChatClient(_ => "Sure! First I'd look for cameras, then...");

        var act = async () => await Program.MakePlanAsync(Program.BuildPlannerAgent(fake), "anything");

        (await act.Should().ThrowAsync<InvalidOperationException>())
            .Which.Message.Should().Contain("Sure! First I'd look for cameras");
    }

    // ─────────────── Execution ───────────────

    [Fact]
    public async Task Every_Planned_Step_Is_Executed_In_Order()
    {
        var fake = Scripted();

        (Plan plan, IReadOnlyList<string> results) =
            await Program.RunPlanAsync(fake, Program.DefaultRequest);

        results.Should().HaveCount(plan.Steps.Count);
        results.Should().Equal("STEP-RESULT-1", "STEP-RESULT-2", "STEP-RESULT-3");
    }

    [Fact]
    public async Task The_Plan_Is_Made_Before_Any_Step_Runs()
    {
        // The property that makes the plan approvable. If planning and
        // execution interleaved, there would be no moment at which the whole
        // plan existed and nothing had happened yet.
        var fake = Scripted();

        await Program.RunPlanAsync(fake, Program.DefaultRequest);

        IsPlanner(fake.Calls[0]).Should().BeTrue();
        fake.Calls.Skip(1).Should().OnlyContain(c => IsExecutor(c));
    }

    [Fact]
    public async Task All_Steps_Share_One_Session_So_Later_Steps_See_Earlier_Results()
    {
        // The failure mode without this is not an error — step 3 sees an empty
        // conversation, has nothing to pick from, and invents a plausible
        // recommendation. Asserting the conversation grows is the only way to
        // catch it.
        var fake = Scripted();

        await Program.RunPlanAsync(fake, Program.DefaultRequest);

        List<ScriptedCall> steps = fake.Calls.Where(IsExecutor).ToList();

        steps[1].Text.Should().Contain("STEP-RESULT-1");
        steps[2].Text.Should().Contain("STEP-RESULT-1").And.Contain("STEP-RESULT-2");
    }

    [Fact]
    public async Task A_Search_Step_Is_Told_Which_Query_To_Use()
    {
        var fake = Scripted();

        await Program.RunPlanAsync(fake, Program.DefaultRequest);

        fake.Calls.First(IsExecutor).Text.Should().Contain("photography camera");
    }

    [Fact]
    public async Task A_Reasoning_Step_Is_Not_Handed_A_Query()
    {
        var fake = Scripted();

        await Program.RunPlanAsync(fake, Program.DefaultRequest);

        List<ScriptedCall> steps = fake.Calls.Where(IsExecutor).ToList();

        // The LAST message, not the whole transcript. Because all steps share
        // one session, step 1's prompt is still visible in step 2's context —
        // asserting over the flattened text would always find it.
        steps[1].Messages[^1].Should().NotContain("search_products with query");
        steps[1].Messages[^1].Should().Contain("Narrow the results");
    }

    [Fact]
    public async Task The_Executor_Always_Has_The_Catalogue_Tool_Available()
    {
        // Available on every step, even the reasoning ones — the executor
        // decides, the plan only suggests. Removing it per-step would make the
        // plan binding in a way the pattern does not intend.
        var fake = Scripted();

        await Program.RunPlanAsync(fake, Program.DefaultRequest);

        fake.Calls.Where(IsExecutor).Should()
            .OnlyContain(c => c.Tools.Contains("search_products"));
    }

    [Fact]
    public async Task An_Empty_Plan_Executes_Nothing_Rather_Than_Throwing()
    {
        var fake = Scripted("""{"goal": "nothing to do", "steps": []}""");

        (Plan plan, IReadOnlyList<string> results) = await Program.RunPlanAsync(fake, "hello");

        plan.Steps.Should().BeEmpty();
        results.Should().BeEmpty();
    }

    [Fact]
    public void The_Two_Agents_Have_Different_Instructions()
    {
        Program.PlannerInstructions.Should().NotBe(Program.ExecutorInstructions);
    }
}
