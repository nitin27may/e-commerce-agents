// MAF v1 — Chapter 28 tests (Reflection and Critique)
//
// This is the first chapter where the repo's own code drives an unbounded
// multi-turn loop, so the tests are shaped around the two ways that goes
// wrong: the loop never stops, or it stops for the wrong reason.
//
// The parsing tests carry most of the weight. "Any criterion not clearly
// marked PASS is a FAIL" is a real design decision with a real cost — a critic
// whose output drifts causes extra revisions — and the alternative is much
// worse: a parser that defaults to PASS turns an unreadable critique into a
// silent approval, and the loop stops enforcing anything while still looking
// like it works.

using FluentAssertions;
using MafV1.Shared.Testing;
using Microsoft.Agents.AI;
using Xunit;

namespace MafV1.Ch28.Reflection.Tests;

public sealed class ReflectionTests
{
    private const string AllPass = "PRICE: PASS\nFEATURE: PASS\nLENGTH: PASS\nFEEDBACK: none";
    private const string PriceFails =
        "PRICE: FAIL\nFEATURE: PASS\nLENGTH: PASS\nFEEDBACK: the price is missing.";

    /// <summary>
    /// Answers as the critic or the draft agent depending on who is asking,
    /// and lets a test script the critic verdict per round.
    /// </summary>
    private static ScriptedChatClient Scripted(params string[] critiques)
    {
        int round = 0;
        int drafts = 0;
        return new ScriptedChatClient(call =>
            call.Instructions == Program.CriticInstructions
                ? critiques[Math.Min(round++, critiques.Length - 1)]
                : $"DRAFT-{++drafts}");
    }

    private static (AIAgent Draft, AIAgent Critic) Agents(ScriptedChatClient fake) =>
        (Program.BuildDraftAgent(fake), Program.BuildCriticAgent(fake));

    // ─────────────── Parsing ───────────────

    [Fact]
    public void A_Clean_All_Pass_Critique_Parses_As_Passed()
    {
        CritiqueResult result = Program.ParseCritique(AllPass);

        result.Passed.Should().BeTrue();
        result.Feedback.Should().Be("none");
    }

    [Fact]
    public void A_Single_Fail_Fails_The_Whole_Critique()
    {
        Program.ParseCritique(PriceFails).Passed.Should().BeFalse();
    }

    [Fact]
    public void An_Omitted_Criterion_Is_Treated_As_Fail()
    {
        // The load-bearing decision. Defaulting to PASS would turn a truncated
        // or malformed critique into a silent approval — the loop would still
        // "work", and would stop enforcing anything.
        CritiqueResult result = Program.ParseCritique("PRICE: PASS\nFEATURE: PASS\nFEEDBACK: looks fine");

        result.LengthOk.Should().BeFalse();
        result.Passed.Should().BeFalse();
    }

    [Fact]
    public void Completely_Unparseable_Text_Fails_Every_Criterion()
    {
        CritiqueResult result = Program.ParseCritique("Looks great to me! Ship it.");

        result.Passed.Should().BeFalse();
        result.PriceOk.Should().BeFalse();
        result.FeatureOk.Should().BeFalse();
        result.LengthOk.Should().BeFalse();
        result.Feedback.Should().BeEmpty();
    }

    [Fact]
    public void Empty_Text_Fails_Rather_Than_Throwing()
    {
        Program.ParseCritique(string.Empty).Passed.Should().BeFalse();
    }

    [Fact]
    public void Parsing_Is_Case_Insensitive_And_Tolerates_Surrounding_Whitespace()
    {
        // Models do not respect formatting instructions perfectly. Being
        // strict about case here would cause revisions that the critic
        // actually thought were unnecessary.
        CritiqueResult result = Program.ParseCritique(
            "  price : pass  \n  Feature: Pass\nLENGTH:PASS\n  feedback:  all good  ");

        result.Passed.Should().BeTrue();
        result.Feedback.Should().Be("all good");
    }

    [Fact]
    public void Preamble_And_Trailing_Chatter_Do_Not_Break_Parsing()
    {
        CritiqueResult result = Program.ParseCritique(
            "Sure, here's my grade:\nPRICE: PASS\nFEATURE: PASS\nLENGTH: PASS\nFEEDBACK: none\nHope that helps!");

        result.Passed.Should().BeTrue();
    }

    // ─────────────── The loop ───────────────

    [Fact]
    public async Task A_Draft_That_Passes_First_Time_Stops_After_One_Iteration()
    {
        ScriptedChatClient fake = Scripted(AllPass);
        (AIAgent draft, AIAgent critic) = Agents(fake);

        IReadOnlyList<Iteration> iterations =
            await Program.RunReflectionLoopAsync(draft, critic, Program.DefaultProduct);

        iterations.Should().ContainSingle();
        iterations[0].Critique.Passed.Should().BeTrue();
    }

    [Fact]
    public async Task A_Passing_First_Draft_Costs_Exactly_Two_Calls()
    {
        // One draft, one critique, no revision. If the loop always revised
        // once "just in case", every run would cost 50% more for nothing.
        ScriptedChatClient fake = Scripted(AllPass);
        (AIAgent draft, AIAgent critic) = Agents(fake);

        await Program.RunReflectionLoopAsync(draft, critic, Program.DefaultProduct);

        fake.Calls.Should().HaveCount(2);
    }

    [Fact]
    public async Task A_Failing_Draft_Is_Revised_And_Re_Critiqued()
    {
        ScriptedChatClient fake = Scripted(PriceFails, AllPass);
        (AIAgent draft, AIAgent critic) = Agents(fake);

        IReadOnlyList<Iteration> iterations =
            await Program.RunReflectionLoopAsync(draft, critic, Program.DefaultProduct);

        iterations.Should().HaveCount(2);
        iterations[0].Critique.Passed.Should().BeFalse();
        iterations[1].Critique.Passed.Should().BeTrue();
        iterations[1].Draft.Should().NotBe(iterations[0].Draft, "the second round grades a revised draft");
    }

    [Fact]
    public async Task The_Loop_Stops_At_MaxIterations_Even_When_The_Critic_Never_Passes()
    {
        // The cap. Nothing in MAF bounds this loop — a critic with an
        // unsatisfiable rubric would otherwise run until the bill stopped it.
        ScriptedChatClient fake = Scripted(PriceFails);
        (AIAgent draft, AIAgent critic) = Agents(fake);

        IReadOnlyList<Iteration> iterations =
            await Program.RunReflectionLoopAsync(draft, critic, Program.DefaultProduct, maxIterations: 3);

        iterations.Should().HaveCount(3);
        iterations[^1].Critique.Passed.Should().BeFalse();
    }

    [Fact]
    public async Task Hitting_The_Cap_Does_Not_Spend_A_Revision_Nobody_Grades()
    {
        // Off-by-one worth pinning: after the final critique the loop must
        // stop, not revise once more and throw the result away. That wasted
        // call is invisible in the output and shows up only on the invoice.
        ScriptedChatClient fake = Scripted(PriceFails);
        (AIAgent draft, AIAgent critic) = Agents(fake);

        await Program.RunReflectionLoopAsync(draft, critic, Program.DefaultProduct, maxIterations: 3);

        // 1 draft + 3 critiques + 2 revisions = 6.
        fake.Calls.Should().HaveCount(6);
    }

    [Fact]
    public async Task A_Cap_Of_One_Grades_The_First_Draft_And_Stops()
    {
        ScriptedChatClient fake = Scripted(PriceFails);
        (AIAgent draft, AIAgent critic) = Agents(fake);

        IReadOnlyList<Iteration> iterations =
            await Program.RunReflectionLoopAsync(draft, critic, Program.DefaultProduct, maxIterations: 1);

        iterations.Should().ContainSingle();
        fake.Calls.Should().HaveCount(2, "one draft, one critique, no revision");
    }

    [Fact]
    public async Task Every_Iteration_Is_Returned_Not_Just_The_Last()
    {
        // A reflection loop that only returns its final draft hides whether it
        // improved anything — which is the one question a reader has.
        ScriptedChatClient fake = Scripted(PriceFails, PriceFails, AllPass);
        (AIAgent draft, AIAgent critic) = Agents(fake);

        IReadOnlyList<Iteration> iterations =
            await Program.RunReflectionLoopAsync(draft, critic, Program.DefaultProduct);

        iterations.Select(i => i.Number).Should().Equal(1, 2, 3);
        iterations.Select(i => i.Draft).Should().OnlyHaveUniqueItems();
    }

    // ─────────────── Prompts ───────────────

    [Fact]
    public async Task The_Critic_Is_Given_The_Draft_And_All_Three_Criteria()
    {
        // A critic that cannot see the price it is grading against can only
        // guess, and will guess PASS.
        ScriptedChatClient fake = Scripted(AllPass);
        (AIAgent draft, AIAgent critic) = Agents(fake);

        await Program.RunReflectionLoopAsync(draft, critic, Program.DefaultProduct);

        ScriptedCall criticCall = fake.Calls.Single(c => c.Instructions == Program.CriticInstructions);
        criticCall.Text.Should()
            .Contain("DRAFT-1").And
            .Contain("$39.99").And
            .Contain("USB-C charging port").And
            .Contain(Program.WordLimit.ToString());
    }

    [Fact]
    public async Task A_Revision_Carries_The_Feedback_And_The_Previous_Draft()
    {
        // Without both, "revise" degenerates into "write it again" and the
        // loop stops converging.
        ScriptedChatClient fake = Scripted(PriceFails, AllPass);
        (AIAgent draft, AIAgent critic) = Agents(fake);

        await Program.RunReflectionLoopAsync(draft, critic, Program.DefaultProduct);

        ScriptedCall revision = fake.Calls
            .Where(c => c.Instructions == Program.DraftInstructions)
            .Skip(1)
            .First();

        revision.Text.Should().Contain("the price is missing.").And.Contain("DRAFT-1");
    }

    [Fact]
    public void The_Draft_Prompt_States_Every_Constraint_The_Critic_Grades()
    {
        // If these two ever drift apart the loop can never pass: the drafter
        // is not told about a rule the critic enforces.
        string prompt = Program.DraftPrompt(Program.DefaultProduct);

        prompt.Should().Contain("$39.99");
        prompt.Should().Contain("adjustable color temperature");
        prompt.Should().Contain(Program.WordLimit.ToString());
    }

    [Fact]
    public void The_Two_Agents_Have_Different_Instructions()
    {
        Program.DraftInstructions.Should().NotBe(Program.CriticInstructions);
    }
}
