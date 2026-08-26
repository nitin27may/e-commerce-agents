// MAF v1 — Chapter 24 tests (RAG and Grounding)
//
// The chapter's argument is that retrieval and grounding are two different
// things, and that having the first does not give you the second. That is an
// awkward claim to demonstrate with a live model, because a good model usually
// copies the price correctly — the interesting case is rare.
//
// A scripted client makes it easy: hand the agent a tool result and then have
// it answer with a rounded price anyway. That is the whole point of the
// chapter, and it is the one scenario you cannot reliably reproduce on demand
// against a real provider.

using FluentAssertions;
using MafV1.Shared.Testing;
using Xunit;

namespace MafV1.Ch24.Rag.Tests;

public sealed class RagTests
{
    /// <summary>Calls the tool once, then answers with <paramref name="answer"/>.</summary>
    private static ScriptedChatClient Answering(string answer) => new(call =>
        call.Text.Contains("result:")
            ? ScriptedChatClient.Text(answer)
            : ScriptedChatClient.ToolCall("search_products",
                new Dictionary<string, object?> { ["query"] = "headphones" }));

    // ─────────────── Retrieval ───────────────

    [Fact]
    public void Search_Matches_On_Name()
    {
        Program.Search("headphones").Should().ContainSingle().Which.Id.Should().Be("P001");
    }

    [Fact]
    public void Search_Matches_On_Category_Too()
    {
        Program.Search("electronics").Select(p => p.Id).Should().BeEquivalentTo(new[] { "P001", "P004" });
    }

    [Fact]
    public void Search_Is_Case_Insensitive()
    {
        Program.Search("HEADPHONES").Should().ContainSingle();
    }

    [Fact]
    public void Search_Matches_Any_Word_Not_All_Of_Them()
    {
        // OR, not AND. Worth pinning because it is a real design choice with a
        // real cost — recall over precision — and because a multi-word query
        // silently returning nothing looks like an empty catalogue.
        Program.Search("headphones hoodie").Select(p => p.Id)
            .Should().BeEquivalentTo(new[] { "P001", "P003" });
    }

    [Fact]
    public void Search_Returns_Nothing_For_A_Miss()
    {
        Program.Search("submarine").Should().BeEmpty();
    }

    [Fact]
    public void The_Tool_Reports_A_Miss_In_Words_Rather_Than_Returning_Empty()
    {
        // An empty string reaching the model reads as "the tool broke". Saying
        // so plainly is what lets the agent answer "we don't stock that".
        Program.SearchProducts("submarine").Should().Contain("No products matched");
    }

    [Fact]
    public void The_Tool_Result_Includes_The_Id_And_The_Exact_Price()
    {
        // The model can only quote what it is given. A tool result that omits
        // the id makes the grounding instruction impossible to follow.
        Program.SearchProducts("headphones").Should().Contain("P001").And.Contain("$129.99");
    }

    // ─────────────── Claim extraction ───────────────

    [Fact]
    public void An_Id_With_A_Price_Right_After_It_Is_Extracted_As_One_Claim()
    {
        List<ProductClaim> claims = Program.ExtractClaims("We have P001 at $129.99, in stock now.");

        claims.Should().ContainSingle();
        claims[0].Should().Be(new ProductClaim("P001", 129.99m));
    }

    [Fact]
    public void An_Id_With_No_Price_Yields_A_Claim_With_No_Price()
    {
        // Not a failure. "We stock P001" is a weaker claim, not a wrong one,
        // and treating a missing price as a mismatch would flag every
        // perfectly good answer.
        Program.ExtractClaims("Yes, we stock P001.").Single().Price.Should().BeNull();
    }

    [Fact]
    public void Several_Ids_Yield_Several_Claims()
    {
        List<ProductClaim> claims = Program.ExtractClaims("P001 is $129.99 and P004 is $39.99.");

        claims.Select(c => c.Id).Should().Equal("P001", "P004");
        claims.Select(c => c.Price).Should().Equal(129.99m, 39.99m);
    }

    [Fact]
    public void A_Distant_Price_Is_Not_Attached_To_An_Earlier_Id()
    {
        // The 40-character window is the heuristic holding this together, and
        // it is the part most likely to be wrong on real prose. Pinning it
        // means a change to the window is a deliberate decision.
        string answer = "P001 is one of several excellent options we currently have available today. It costs $129.99.";

        Program.ExtractClaims(answer).Single().Price.Should().BeNull();
    }

    [Fact]
    public void An_Answer_With_No_Ids_Yields_No_Claims()
    {
        Program.ExtractClaims("We're open until 6pm.").Should().BeEmpty();
    }

    // ─────────────── Verification ───────────────

    [Fact]
    public void A_Correct_Id_And_Price_Verifies()
    {
        Program.VerifyAnswer("P001 costs $129.99.").AllVerified.Should().BeTrue();
    }

    [Fact]
    public void A_Rounded_Price_Is_Caught_As_A_Mismatch()
    {
        // The chapter's headline scenario. Retrieval worked — the model saw
        // $129.99 — and the answer still says $130. Nothing about the retrieval
        // step can catch this.
        GroundingReport report = Program.VerifyAnswer("P001 costs $130.");

        report.AllVerified.Should().BeFalse();
        report.Verdicts.Single().Status.Should().Be(ClaimStatus.PriceMismatch);
        report.Verdicts.Single().Detail.Should().Contain("$129.99").And.Contain("$130");
    }

    [Fact]
    public void A_Hallucinated_Id_Is_Caught_As_Not_Found()
    {
        // P007 has the catalogue's shape but is not in it.
        GroundingReport report = Program.VerifyAnswer("Try P007 at $10.00.");

        report.Verdicts.Single().Status.Should().Be(ClaimStatus.NotFound);
    }

    [Fact]
    public void An_Id_That_Does_Not_Match_The_Catalogue_Id_Shape_Is_Invisible_To_The_Extractor()
    {
        // A real limitation, pinned rather than papered over. The extractor
        // regex is \bP0\d{2}\b, so a hallucinated "P999" is never extracted
        // and therefore never verified — the answer comes back vacuously
        // grounded. That is the worst possible verdict for that input.
        //
        // It is the right trade-off at toy scale (a looser pattern would match
        // order numbers, postcodes and half the alphabet), but it is exactly
        // why production parses structured card payloads instead of prose —
        // see agents/python/shared/grounding/extractor.py.
        GroundingReport report = Program.VerifyAnswer("Try P999 at $10.00.");

        report.TotalCount.Should().Be(0);
        report.AllVerified.Should().BeTrue("which is precisely the problem");
    }

    [Fact]
    public void A_Correct_Id_With_No_Price_Verifies()
    {
        Program.VerifyAnswer("Yes, we stock P001.").AllVerified.Should().BeTrue();
    }

    [Fact]
    public void A_Price_Within_Tolerance_Verifies()
    {
        // A cent of slack, to absorb formatting rather than to forgive
        // rounding. $129.99 vs $130 is 1 cent over the line and must fail —
        // see the mismatch test above.
        Program.VerifyAnswer("P001 costs $129.99.").AllVerified.Should().BeTrue();
    }

    [Fact]
    public void One_Bad_Claim_Among_Several_Good_Ones_Is_Still_Reported()
    {
        // A per-answer boolean would hide this. The counts are what make a
        // grounding report actionable.
        GroundingReport report = Program.VerifyAnswer("P001 is $129.99, P004 is $40.00, P002 is $24.50.");

        report.TotalCount.Should().Be(3);
        report.VerifiedCount.Should().Be(2);
        report.AllVerified.Should().BeFalse();
    }

    [Fact]
    public void An_Answer_That_Claims_Nothing_Is_Vacuously_Grounded()
    {
        // Correct, and worth stating: an agent that never cites anything scores
        // 100%. A grounding rate is a safety measure, not a quality score.
        GroundingReport report = Program.VerifyAnswer("We're open until 6pm.");

        report.TotalCount.Should().Be(0);
        report.AllVerified.Should().BeTrue();
    }

    // ─────────────── The two mechanisms, end to end ───────────────

    [Fact]
    public async Task The_Agent_Retrieves_Before_Answering()
    {
        var fake = Answering("We have P001 at $129.99.");

        await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        fake.Calls[0].Tools.Should().ContainSingle().Which.Should().Be("search_products");
        fake.Calls.Should().HaveCount(2, "one turn to call the tool, one to answer from the result");
    }

    [Fact]
    public async Task The_Model_Sees_The_Real_Price_Before_It_Answers()
    {
        var fake = Answering("We have P001 at $129.99.");

        await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        fake.Calls[1].Text.Should().Contain("$129.99");
    }

    [Fact]
    public async Task Retrieval_Succeeding_Does_Not_Make_The_Answer_Grounded()
    {
        // The whole chapter in one test. The tool returned $129.99, the model
        // read it, and the answer still says $130 — so the run is correct at
        // every step except the one that matters to the customer.
        var fake = Answering("We have P001, currently $130.");

        string answer = await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        fake.Calls[1].Text.Should().Contain("$129.99", "the model was given the correct price");
        Program.VerifyAnswer(answer).AllVerified.Should().BeFalse("and quoted a different one anyway");
    }
}
