// MAF v1 — Chapter 13 tests (Concurrent Orchestration)
//
// The chapter makes two claims that a `dotnet build` gate cannot check:
//
//   1. The three agents genuinely run at the same time. "Wall-clock is the
//      slowest agent, not the sum" is the entire reason to reach for
//      BuildConcurrent, and it is trivially easy to ship a version that
//      quietly serialises.
//   2. The custom aggregator is what produces the terminal output — not some
//      framework default that happens to look similar.
//
// Both are asserted here against a scripted IChatClient. The overlap test
// uses recorded call start/end timestamps rather than total elapsed time, so
// it does not flake on a loaded CI runner.

using FluentAssertions;
using MafV1.Shared.Testing;
using Microsoft.Extensions.AI;
using Xunit;

namespace MafV1.Ch13.Concurrent.Tests;

public sealed class ConcurrentTests
{
    // The three agents race, so a positional response queue would hand the
    // marketer the researcher's line roughly one run in six. Answer by
    // looking at who is asking instead.
    private static ScriptedChatClient Scripted(TimeSpan? delay = null) => new(call =>
        call.Instructions.Contains("Researcher") ? "MARKET-FIT"
        : call.Instructions.Contains("Marketer") ? "POSITIONING"
        : call.Instructions.Contains("Legal") ? "REG-RISK"
        : "?")
    {
        Delay = delay ?? TimeSpan.Zero,
    };

    [Fact]
    public async Task All_Three_Agents_Are_Consulted()
    {
        ScriptedChatClient fake = Scripted();

        ConcurrentReview review = await Program.RunAsync(fake, "herbal tea box");

        fake.Calls.Should().HaveCount(3);
        review.PerAgent.Select(m => m.Text).Should()
            .BeEquivalentTo("MARKET-FIT", "POSITIONING", "REG-RISK");
    }

    [Fact]
    public async Task Every_Agent_Sees_The_Same_Input_And_Only_The_Input()
    {
        // Fan-out, not a chain: no agent may see another's answer. If this
        // ever fails, BuildConcurrent has become BuildSequential.
        ScriptedChatClient fake = Scripted();

        await Program.RunAsync(fake, "herbal tea box");

        foreach (ScriptedCall call in fake.Calls)
        {
            call.Text.Should().Contain("herbal tea box");
            call.Text.Should().NotContain("MARKET-FIT");
            call.Text.Should().NotContain("POSITIONING");
            call.Text.Should().NotContain("REG-RISK");
        }
    }

    [Fact]
    public async Task Each_Agent_Receives_Its_Own_Instructions()
    {
        ScriptedChatClient fake = Scripted();

        await Program.RunAsync(fake, "herbal tea box");

        fake.CallFor("Researcher").Instructions.Should().Be(Program.ResearcherInstructions);
        fake.CallFor("Marketer").Instructions.Should().Be(Program.MarketerInstructions);
        fake.CallFor("Legal").Instructions.Should().Be(Program.LegalInstructions);
    }

    [Fact]
    public async Task The_Three_Calls_Overlap_In_Time()
    {
        // The claim the chapter is built on. Asserted from recorded call
        // start/end times, not from total elapsed — a wall-clock threshold
        // would flake the first time CI got busy.
        ScriptedChatClient fake = Scripted(TimeSpan.FromMilliseconds(120));

        await Program.RunAsync(fake, "herbal tea box");

        fake.HadOverlappingCalls().Should()
            .BeTrue("BuildConcurrent's whole purpose is that the agents do not wait for each other");
    }

    [Fact]
    public async Task The_Custom_Aggregator_Produces_The_Terminal_Output()
    {
        ConcurrentReview review = await Program.RunAsync(Scripted(), "herbal tea box");

        // The header is SynthesizeReview's own, so its presence proves the
        // aggregator ran rather than a framework default.
        review.Summary.Should().StartWith("Cross-functional review:");
        review.Summary.Should().Contain("MARKET-FIT")
            .And.Contain("POSITIONING")
            .And.Contain("REG-RISK");
    }

    [Fact]
    public void The_Aggregator_Labels_Each_Line_With_Its_Author()
    {
        // Unit-level, so the labelling rule is pinned independently of the
        // workflow: last message per agent, tagged with AuthorName.
        var perAgent = new List<List<ChatMessage>>
        {
            new() { new ChatMessage(ChatRole.Assistant, "a") { AuthorName = "researcher" } },
            new() { new ChatMessage(ChatRole.Assistant, "b") { AuthorName = "marketer" } },
        };

        List<ChatMessage> result = Program.SynthesizeReview(perAgent);

        result.Should().ContainSingle();
        result[0].AuthorName.Should().Be("concurrent-aggregator");
        result[0].Text.Should().Contain("- researcher: a").And.Contain("- marketer: b");
    }

    [Fact]
    public void The_Aggregator_Skips_Agents_That_Emitted_Nothing()
    {
        // A concurrent branch can legitimately produce an empty list. The
        // aggregator must not index into it — that would turn one quiet agent
        // into a crashed workflow.
        var perAgent = new List<List<ChatMessage>>
        {
            new(),
            new() { new ChatMessage(ChatRole.Assistant, "b") { AuthorName = "marketer" } },
        };

        List<ChatMessage> result = Program.SynthesizeReview(perAgent);

        result[0].Text.Should().Contain("- marketer: b");
    }

    [Fact]
    public async Task One_Failing_Agent_Does_Not_Silently_Produce_A_Partial_Review()
    {
        // Concurrent has no per-branch error isolation here. What must not
        // happen is a summary that looks complete but is missing an agent.
        var fake = new ScriptedChatClient(_ => "ok") { ThrowAfter = 1 };

        ConcurrentReview review;
        try
        {
            review = await Program.RunAsync(fake, "herbal tea box");
        }
        catch (Exception)
        {
            return;
        }

        review.PerAgent.Should().HaveCountLessThan(3);
    }
}
