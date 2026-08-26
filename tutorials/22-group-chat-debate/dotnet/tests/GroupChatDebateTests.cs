// MAF v1 — Chapter 22 tests (Group-Chat Debate)
//
// The pattern's defining claim is sequencing: each panelist sees what was said
// before them, so later takes are responses rather than parallel monologues.
// That claim is invisible from the output of a well-behaved panel — two
// panelists who happen not to reference each other look identical whether the
// transcript was shared or not.
//
// So most of these tests hand a panelist a responder that REPORTS what it
// could see, and assert on that. It is the only way to distinguish a
// round-table from a fan-out that happens to run in order.
//
// No LLM: the panelists are plain callables, which is also what makes every
// assertion here exact.

using FluentAssertions;
using Xunit;

namespace MafV1.Ch22.GroupChatDebate.Tests;

public sealed class GroupChatDebateTests
{
    /// <summary>A panelist that always says the same thing.</summary>
    private static Responder Says(string text) => (_, _) => ValueTask.FromResult(text);

    /// <summary>A panelist that reports who spoke before it.</summary>
    private static Responder ReportsPriorSpeakers() =>
        (_, transcript) => ValueTask.FromResult(
            transcript.Count == 0
                ? "I spoke first"
                : $"I heard: {string.Join(", ", transcript.Select(t => t.Speaker))}");

    // ─────────────── Sequencing ───────────────

    [Fact]
    public async Task Every_Panelist_Speaks_Exactly_Once_In_Order()
    {
        var workflow = new GroupChatWorkflow(new[]
        {
            ("first", Says("a")),
            ("second", Says("b")),
            ("third", Says("c")),
        });

        GroupChatState state = await workflow.ExecuteAsync("q");

        state.Transcript.Select(t => t.Speaker).Should().Equal("first", "second", "third");
        state.Transcript.Select(t => t.Text).Should().Equal("a", "b", "c");
    }

    [Fact]
    public async Task A_Later_Panelist_Sees_Every_Earlier_Turn()
    {
        // The assertion the pattern lives on. Without a shared transcript the
        // third panelist would report "I spoke first" — and nothing else in the
        // run would look wrong.
        var workflow = new GroupChatWorkflow(new[]
        {
            ("first", ReportsPriorSpeakers()),
            ("second", ReportsPriorSpeakers()),
            ("third", ReportsPriorSpeakers()),
        });

        GroupChatState state = await workflow.ExecuteAsync("q");

        state.Transcript[0].Text.Should().Be("I spoke first");
        state.Transcript[1].Text.Should().Be("I heard: first");
        state.Transcript[2].Text.Should().Be("I heard: first, second");
    }

    [Fact]
    public async Task A_Panelist_Cannot_See_Turns_That_Have_Not_Happened_Yet()
    {
        // The other half of the ordering guarantee, and it is not implied by
        // the test above: a transcript passed by reference could expose later
        // turns if the panel ran concurrently.
        var seen = new List<int>();
        Responder counting = (_, transcript) =>
        {
            seen.Add(transcript.Count);
            return ValueTask.FromResult("ok");
        };

        await new GroupChatWorkflow(new[]
        {
            ("a", counting), ("b", counting), ("c", counting),
        }).ExecuteAsync("q");

        seen.Should().Equal(0, 1, 2);
    }

    [Fact]
    public async Task The_Question_Reaches_Every_Panelist()
    {
        var questions = new List<string>();
        Responder capture = (question, _) =>
        {
            questions.Add(question);
            return ValueTask.FromResult("ok");
        };

        await new GroupChatWorkflow(new[] { ("a", capture), ("b", capture) }).ExecuteAsync("is it worth it?");

        questions.Should().Equal("is it worth it?", "is it worth it?");
    }

    // ─────────────── The moderator ───────────────

    [Fact]
    public async Task The_Moderator_Runs_Last_And_Is_Recorded()
    {
        // CompletedSteps is the audit trail. A moderator that ran before the
        // last panelist would still produce a plausible verdict — from an
        // incomplete transcript.
        GroupChatState state = await new GroupChatWorkflow(new[]
        {
            ("value", Says("cheap")),
            ("quality", Says("solid")),
        }).ExecuteAsync("q");

        state.CompletedSteps.Should().Equal("value", "quality", "moderator");
    }

    [Fact]
    public async Task The_Default_Synthesis_Names_Every_Speaker()
    {
        GroupChatState state = await new GroupChatWorkflow(new[]
        {
            ("value", Says("cheap")),
            ("quality", Says("solid")),
        }).ExecuteAsync("Is it worth it?");

        state.Verdict.Should()
            .Contain("2 perspective(s)").And
            .Contain("value, quality").And
            .Contain("Is it worth it?");
    }

    [Fact]
    public async Task A_Custom_Synthesizer_Replaces_The_Default()
    {
        GroupChatState state = await new GroupChatWorkflow(
            new[] { ("value", Says("cheap")) },
            _ => "MY VERDICT").ExecuteAsync("q");

        state.Verdict.Should().Be("MY VERDICT");
    }

    [Fact]
    public async Task The_Synthesizer_Sees_The_Complete_Transcript()
    {
        // If the moderator were handed a copy taken before the last turn, this
        // would read 1 — and the verdict would be confidently wrong rather
        // than obviously broken.
        int seen = -1;

        await new GroupChatWorkflow(
            new[] { ("a", Says("x")), ("b", Says("y")) },
            state =>
            {
                seen = state.Transcript.Count;
                return "done";
            }).ExecuteAsync("q");

        seen.Should().Be(2);
    }

    // ─────────────── Failure handling ───────────────

    [Fact]
    public async Task A_Panelist_That_Throws_Becomes_A_Visible_Turn_Rather_Than_Killing_The_Panel()
    {
        // A round-table that dies because one specialist timed out is worse
        // than one that reports a missing voice — the moderator can reconcile
        // around a gap it can see.
        Responder boom = (_, _) => throw new InvalidOperationException("provider down");

        GroupChatState state = await new GroupChatWorkflow(new[]
        {
            ("value", Says("cheap")),
            ("quality", boom),
        }).ExecuteAsync("q");

        state.Transcript.Should().HaveCount(2);
        state.Transcript[1].Text.Should().Contain("quality could not respond").And.Contain("provider down");
        state.Verdict.Should().NotBeEmpty();
    }

    [Fact]
    public async Task A_Panelist_After_A_Failure_Still_Runs_And_Sees_The_Failed_Turn()
    {
        Responder boom = (_, _) => throw new InvalidOperationException("nope");

        GroupChatState state = await new GroupChatWorkflow(new[]
        {
            ("a", boom),
            ("b", ReportsPriorSpeakers()),
        }).ExecuteAsync("q");

        state.Transcript[1].Text.Should().Be("I heard: a");
        state.CompletedSteps.Should().Equal("a", "b", "moderator");
    }

    [Fact]
    public void An_Empty_Panel_Is_Rejected()
    {
        // A moderator summarizing silence would emit a confident verdict about
        // nothing at all.
        var act = () => new GroupChatWorkflow(Array.Empty<(string, Responder)>()).Build();

        act.Should().Throw<ArgumentException>().WithMessage("*at least one panelist*");
    }

    [Fact]
    public async Task A_Single_Panelist_Panel_Still_Reaches_The_Moderator()
    {
        GroupChatState state = await new GroupChatWorkflow(new[] { ("solo", Says("x")) }).ExecuteAsync("q");

        state.Transcript.Should().ContainSingle();
        state.CompletedSteps.Should().Equal("solo", "moderator");
        state.Verdict.Should().NotBeEmpty();
    }

    // ─────────────── The chapter's own demo ───────────────

    [Fact]
    public async Task The_Demo_Panel_Produces_A_Two_Turn_Debate_And_A_Verdict()
    {
        GroupChatState state = await Program.BuildWorkflow()
            .ExecuteAsync("Is the Sony WH-1000XM5 worth it?");

        state.Transcript.Select(t => t.Speaker).Should().Equal("value", "quality");
        state.Verdict.Should().Contain("recommended");
    }

    [Fact]
    public async Task The_Quality_Panelist_Demonstrably_Reads_The_Transcript()
    {
        // The demo's own proof that this is a debate. If the quality voice ever
        // stopped counting prior turns, the chapter would still run and would
        // stop demonstrating its own subject.
        GroupChatState state = await Program.BuildWorkflow().ExecuteAsync("q");

        state.Transcript[1].Text.Should().Contain("1 prior point(s)");
    }

    [Fact]
    public async Task Two_Runs_Do_Not_Share_A_Transcript()
    {
        // The workflow is built per run; state that leaked between runs would
        // make the second panel see the first one's turns.
        GroupChatWorkflow workflow = Program.BuildWorkflow();

        GroupChatState first = await workflow.ExecuteAsync("q1");
        GroupChatState second = await workflow.ExecuteAsync("q2");

        first.Transcript.Should().HaveCount(2);
        second.Transcript.Should().HaveCount(2);
        second.Question.Should().Be("q2");
    }
}
