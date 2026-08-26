// MAF v1 — Chapter 26 tests (Evals)
//
// Testing an eval harness is a slightly odd exercise — it is scoring code, so
// the tests are scoring the scorer. What makes it worth doing is that a broken
// scorer does not look broken: it reports numbers, the numbers look
// reasonable, and the suite goes green while measuring nothing.
//
// So the assertions here are mostly about the ways a scorer lies:
//
//   * A case with no expected facts scores 1.0 — defensible, and a trap.
//   * The deterministic tier passes an answer that is rude, off-topic or
//     enormous, as long as the number appears somewhere in it.
//   * The stub judge agrees with the deterministic tier by construction, which
//     is exactly what a real judge must not do.
//
// A suite that hides those is worse than no suite, because it is trusted.

using FluentAssertions;
using MafV1.Shared.Testing;
using Microsoft.Agents.AI;
using Xunit;

namespace MafV1.Ch26.Evals.Tests;

public sealed class EvalsTests
{
    /// <summary>Calls the tool, then answers with the canned text for the case.</summary>
    private static ScriptedChatClient Answering(Func<string, string> answerFor) => new(call =>
        call.Text.Contains("result:")
            ? ScriptedChatClient.Text(answerFor(call.Text))
            : ScriptedChatClient.ToolCall("search_catalog",
                new Dictionary<string, object?> { ["productName"] = "Wireless Mouse" }));

    // ─────────────── The catalogue tool ───────────────

    [Fact]
    public void SearchCatalog_Reports_Price_Stock_And_Availability()
    {
        Program.SearchCatalog("Wireless Mouse").Should()
            .Contain("$24.99").And.Contain("42 units").And.Contain("in stock");
    }

    [Fact]
    public void SearchCatalog_Distinguishes_Out_Of_Stock_From_Missing()
    {
        // Two different answers to two different questions. Collapsing them
        // makes "we stock it, just not today" indistinguishable from "we have
        // never sold that".
        Program.SearchCatalog("USB-C Hub").Should().Contain("out of stock").And.Contain("0 units");
        Program.SearchCatalog("flux capacitor").Should().Contain("No catalog entry");
    }

    // ─────────────── Deterministic tier ───────────────

    [Fact]
    public void An_Answer_Containing_Every_Expected_Fact_Scores_One()
    {
        DeterministicResult result = Program.ScoreDeterministic(
            "The Portable Charger is $19.99 and we have 120 in stock.", new[] { "19.99", "120" });

        result.Score.Should().Be(1.0);
        result.Missing.Should().BeEmpty();
    }

    [Fact]
    public void A_Partial_Answer_Scores_Proportionally_And_Names_What_Is_Missing()
    {
        // The missing list is what makes a failing case actionable. A bare 0.5
        // tells you something is wrong and nothing about what.
        DeterministicResult result = Program.ScoreDeterministic(
            "The Portable Charger is $19.99.", new[] { "19.99", "120" });

        result.Score.Should().Be(0.5);
        result.Found.Should().Equal("19.99");
        result.Missing.Should().Equal("120");
    }

    [Fact]
    public void Matching_Is_Case_Insensitive()
    {
        Program.ScoreDeterministic("It is OUT OF STOCK.", new[] { "out of stock" })
            .Score.Should().Be(1.0);
    }

    [Fact]
    public void A_Case_With_No_Expected_Facts_Scores_One()
    {
        // The only defensible answer — there was nothing to get wrong — and
        // also the trap: a suite of empty cases reports a perfect pass rate.
        Program.ScoreDeterministic("anything at all", Array.Empty<string>())
            .Score.Should().Be(1.0);
    }

    [Fact]
    public void The_Deterministic_Tier_Passes_An_Answer_That_Is_Correct_And_Useless()
    {
        // The stated limitation, asserted rather than described. This is the
        // gap the judge tier exists to cover, and pretending it is not there
        // is how a green eval suite gets trusted further than it should be.
        DeterministicResult result = Program.ScoreDeterministic(
            "I'm not going to help you, but if I were, the number would be 24.99. Also, buy a rival's product.",
            new[] { "24.99" });

        result.Score.Should().Be(1.0);
    }

    [Fact]
    public void A_Substring_Match_Can_Pass_On_A_Coincidence()
    {
        // "15" appears inside "150". Substring matching is cheap and exact and
        // still not the same as correct — worth knowing before trusting a
        // number to two decimal places.
        Program.ScoreDeterministic("We have 150 in stock.", new[] { "15" })
            .Score.Should().Be(1.0);
    }

    // ─────────────── Judge tier ───────────────

    [Fact]
    public void A_Full_Coverage_Answer_Gets_No_Failure_Mode()
    {
        JudgeVerdict verdict = Program.JudgeResponseStub("q", "$19.99 and 120 units", new[] { "19.99", "120" });

        verdict.Score.Should().Be(1.0);
        verdict.FailureMode.Should().BeNull();
        verdict.Reasoning.Should().NotBeNullOrWhiteSpace();
    }

    [Fact]
    public void A_Zero_Coverage_Answer_Is_Labelled_Missing_Field()
    {
        // The failure mode is the field that makes a suite of results
        // groupable. "12 cases failed" is noise; "9 of them missing_field" is
        // a place to start.
        Program.JudgeResponseStub("q", "no idea", new[] { "19.99" })
            .FailureMode.Should().Be("missing_field");
    }

    [Fact]
    public void A_Partial_Answer_Is_Labelled_Partial_Coverage()
    {
        JudgeVerdict verdict = Program.JudgeResponseStub("q", "$19.99", new[] { "19.99", "120" });

        verdict.Score.Should().Be(0.5);
        verdict.FailureMode.Should().Be("partial_coverage");
        verdict.Reasoning.Should().Contain("1/2");
    }

    [Fact]
    public void The_Stub_Judge_Always_Agrees_With_The_Deterministic_Tier()
    {
        // True by construction, and pinned so nobody mistakes agreement for
        // corroboration. Two scorers that always agree are one scorer costing
        // twice as much; the value of a second tier is disagreement, which is
        // exactly what this stub cannot produce.
        foreach (string answer in new[] { "$19.99 and 120", "$19.99", "nothing useful" })
        {
            Program.JudgeResponseStub("q", answer, new[] { "19.99", "120" }).Score
                .Should().Be(Program.ScoreDeterministic(answer, new[] { "19.99", "120" }).Score);
        }
    }

    // ─────────────── The suite ───────────────

    [Fact]
    public async Task Every_Case_Is_Run_And_Scored()
    {
        var fake = Answering(_ => "$24.99, 42 units, 15, out of stock, 149.99, 19.99, 120");

        IReadOnlyList<EvalResult> results = await Program.RunEvalSuiteAsync(Program.BuildAgent(fake));

        results.Should().HaveCount(Program.EvalCases.Count);
        results.Select(r => r.CaseId).Should().Equal(Program.EvalCases.Select(c => c.CaseId));
    }

    [Fact]
    public async Task A_Perfect_Agent_Passes_Every_Case()
    {
        var fake = Answering(_ => "$24.99, 42 units, 15, out of stock, 149.99, 19.99, 120");

        IReadOnlyList<EvalResult> results = await Program.RunEvalSuiteAsync(Program.BuildAgent(fake));

        results.Should().OnlyContain(r => r.Deterministic.Score == 1.0);
    }

    [Fact]
    public async Task An_Agent_That_Answers_Nothing_Useful_Fails_Every_Case()
    {
        // The control. Without it, a suite that passes everything might be
        // measuring nothing at all — and would look identical to a good one.
        var fake = Answering(_ => "I'm not sure, sorry.");

        IReadOnlyList<EvalResult> results = await Program.RunEvalSuiteAsync(Program.BuildAgent(fake));

        results.Should().OnlyContain(r => r.Deterministic.Score == 0.0);
        results.Should().OnlyContain(r => r.Judge.FailureMode == "missing_field");
    }

    [Fact]
    public async Task Each_Case_Is_Scored_Against_Its_Own_Expected_Facts()
    {
        // Cross-contamination check: an answer that satisfies case A must not
        // satisfy case B. If the suite reused one expectation list, a single
        // lucky answer would turn the whole run green.
        var fake = Answering(_ => "The Wireless Mouse costs $24.99.");

        IReadOnlyList<EvalResult> results = await Program.RunEvalSuiteAsync(Program.BuildAgent(fake));

        results.Single(r => r.CaseId == "mouse-price").Deterministic.Score.Should().Be(1.0);
        results.Single(r => r.CaseId == "keyboard-stock").Deterministic.Score.Should().Be(0.0);
    }

    [Fact]
    public async Task The_Scorecard_Reports_The_Pass_Count_And_Names_Failures()
    {
        var fake = Answering(_ => "The Wireless Mouse costs $24.99.");
        IReadOnlyList<EvalResult> results = await Program.RunEvalSuiteAsync(Program.BuildAgent(fake));

        var lines = new List<string>();
        Program.PrintScorecard(results, lines.Add);

        string output = string.Join("\n", lines);
        output.Should().Contain("1/5 cases fully grounded");
        output.Should().Contain("keyboard-stock").And.Contain("missing: [15]");
    }

    [Fact]
    public void Every_Case_Has_At_Least_One_Checkable_Fact()
    {
        // Guards the suite against itself. An expectation-free case is a case
        // that always passes, and a suite that accumulates a few of those
        // drifts upward without anyone changing the agent.
        Program.EvalCases.Should().OnlyContain(c => c.ExpectedFacts.Count > 0);
    }

    [Fact]
    public void Case_Ids_Are_Unique()
    {
        // They are the key results are reported and tracked under. Duplicates
        // make a regression report ambiguous at exactly the wrong moment.
        Program.EvalCases.Select(c => c.CaseId).Should().OnlyHaveUniqueItems();
    }
}
