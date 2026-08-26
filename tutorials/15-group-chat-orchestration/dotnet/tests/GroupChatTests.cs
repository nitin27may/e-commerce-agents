// MAF v1 — Chapter 15 tests (Group Chat Orchestration)
//
// The chapter's thesis is that "prompt-driven" is not a separate MAF product
// type — it is a GroupChatManager subclass whose SelectNextAgentAsync calls an
// LLM instead of walking an index. Testing it means testing the manager, and
// the manager's interesting behaviour is entirely in its failure paths:
//
//   * the LLM names a participant       -> that participant speaks
//   * the LLM names something unknown   -> fall back, do not crash
//   * the LLM returns unparseable text  -> fall back, do not crash
//   * the LLM throws                    -> fall back, do not crash
//
// Those three fallbacks are the difference between a demo and something you
// would run, and none of them is reachable from a `dotnet build`.
//
// The scripted client answers by looking at who is asking: the selector's
// prompt is recognisable ("You coordinate a Writer/Critic/Editor group chat"),
// everything else is an agent turn.

using FluentAssertions;
using MafV1.Shared.Testing;
using Microsoft.Extensions.AI;
using Xunit;

namespace MafV1.Ch15.GroupChat.Tests;

public sealed class GroupChatTests
{
    private const string SelectorMarker = "You coordinate a Writer/Critic/Editor group chat";

    /// <summary>Agents answer with their own role name; the selector answers with <paramref name="selection"/>.</summary>
    private static ScriptedChatClient Scripted(Func<int, string> selection)
    {
        int selectorCalls = 0;
        return new ScriptedChatClient(call =>
        {
            if (call.Text.Contains(SelectorMarker))
            {
                return selection(selectorCalls++);
            }

            // Match on the opening clause, not a bare role word. The Critic's
            // instructions mention the Writer and the Editor's mention both, so
            // a naive Contains("Writer") hands every agent the writer's line —
            // and the run still completes, which is the worst kind of wrong.
            if (call.Instructions.StartsWith("You are a Writer")) return "WRITER-LINE";
            if (call.Instructions.StartsWith("You are a Critic")) return "CRITIC-NOTE";
            if (call.Instructions.StartsWith("You are an Editor")) return "EDITOR-FINAL";
            return "?";
        });
    }

    private static IEnumerable<string> Speakers(IReadOnlyList<ChatMessage> conversation) =>
        conversation.Where(m => m.Role == ChatRole.Assistant).Select(m => m.AuthorName ?? "?");

    // ─────────────── Round-robin manager ───────────────

    [Fact]
    public async Task RoundRobin_Gives_Each_Participant_A_Turn_In_Order()
    {
        ScriptedChatClient fake = Scripted(_ => "unused");

        IReadOnlyList<ChatMessage> conversation = await Program.RunAsync(fake, "coffee slogan");

        Speakers(conversation).Should().Equal("writer", "critic", "editor");
    }

    [Fact]
    public async Task RoundRobin_Never_Consults_A_Selector_Model()
    {
        // The contrast the chapter draws: round-robin costs zero extra tokens.
        ScriptedChatClient fake = Scripted(_ => "unused");

        await Program.RunAsync(fake, "coffee slogan");

        fake.Calls.Should().NotContain(c => c.Text.Contains(SelectorMarker));
        fake.Calls.Should().HaveCount(3, "three participants, one turn each, no manager call");
    }

    [Fact]
    public async Task MaximumIterationCount_Caps_The_Conversation()
    {
        // The safety net. If this ever stops holding, a bad selector becomes
        // an unbounded spend rather than a short bad answer.
        ScriptedChatClient fake = Scripted(_ => "unused");

        IReadOnlyList<ChatMessage> conversation =
            await Program.RunAsync(fake, "coffee slogan", "round-robin", maximumIterations: 1);

        Speakers(conversation).Should().HaveCount(1);
    }

    [Fact]
    public async Task Each_Speaker_Sees_What_Came_Before()
    {
        ScriptedChatClient fake = Scripted(_ => "unused");

        await Program.RunAsync(fake, "coffee slogan");

        ScriptedCall criticCall = fake.Calls.First(c => c.Instructions.StartsWith("You are a Critic"));
        criticCall.Text.Should().Contain("WRITER-LINE");

        ScriptedCall editorCall = fake.Calls.First(c => c.Instructions.StartsWith("You are an Editor"));
        editorCall.Text.Should().Contain("WRITER-LINE").And.Contain("CRITIC-NOTE");
    }

    // ─────────────── Prompt-driven manager ───────────────

    [Fact]
    public async Task PromptDriven_Honours_The_Model_Selection()
    {
        // Selector names the critic first, so the critic speaks first — which
        // round-robin would never do. That difference is the assertion.
        ScriptedChatClient fake = Scripted(_ => "{\"next\": \"critic\"}");

        IReadOnlyList<ChatMessage> conversation =
            await Program.RunAsync(fake, "coffee slogan", "prompt");

        Speakers(conversation).First().Should().Be("critic");
    }

    [Fact]
    public async Task PromptDriven_Terminates_Once_The_Editor_Has_Spoken()
    {
        // ShouldTerminateAsync, not the iteration cap. Raise the cap well
        // above the expected turn count so a pass cannot be the cap in disguise.
        ScriptedChatClient fake = Scripted(_ => "{\"next\": \"editor\"}");

        IReadOnlyList<ChatMessage> conversation =
            await Program.RunAsync(fake, "coffee slogan", "prompt", maximumIterations: 10);

        Speakers(conversation).Should().Equal("editor");
    }

    [Fact]
    public async Task PromptDriven_Respects_MaximumIterationCount_When_Its_Own_Condition_Never_Fires()
    {
        // The regression test for the bug this chapter shipped with.
        //
        // PromptDrivenManager overrides ShouldTerminateAsync. MaximumIterationCount
        // is enforced in the BASE implementation, so an override that does not
        // chain to it removes the cap entirely. Here the selector always names
        // the critic, so the "editor has spoken" condition never fires — before
        // the fix this ran forever, one provider call per turn.
        //
        // Deliberately asserted with a timeout: the failure mode is a hang, not
        // a wrong value, and a hung test run is a much worse signal than a
        // failed assertion.
        ScriptedChatClient fake = Scripted(_ => "{\"next\": \"critic\"}");

        Task<IReadOnlyList<ChatMessage>> run =
            Program.RunAsync(fake, "coffee slogan", "prompt", maximumIterations: 3);

        Task finished = await Task.WhenAny(run, Task.Delay(TimeSpan.FromSeconds(10)));

        finished.Should().BeSameAs(run, "the iteration cap must bound a selector that never terminates");
        (await run).Where(m => m.Role == ChatRole.Assistant).Should().HaveCountLessThanOrEqualTo(3);
    }

    [Fact]
    public async Task PromptDriven_Falls_Back_When_The_Model_Names_An_Unknown_Agent()
    {
        ScriptedChatClient fake = Scripted(_ => "{\"next\": \"nobody-by-that-name\"}");

        IReadOnlyList<ChatMessage> conversation =
            await Program.RunAsync(fake, "coffee slogan", "prompt");

        // The fallback is round-robin by iteration, so somebody real speaks.
        Speakers(conversation).Should().NotBeEmpty();
        Speakers(conversation).Should().OnlyContain(s => s == "writer" || s == "critic" || s == "editor");
    }

    [Fact]
    public async Task PromptDriven_Falls_Back_When_The_Model_Returns_Unparseable_Text()
    {
        // The single most likely real-world failure: the model chats instead
        // of emitting JSON. It must not take the workflow down.
        ScriptedChatClient fake = Scripted(_ => "Sure! I think the critic should go next.");

        IReadOnlyList<ChatMessage> conversation =
            await Program.RunAsync(fake, "coffee slogan", "prompt");

        Speakers(conversation).Should().NotBeEmpty();
    }

    [Fact]
    public async Task PromptDriven_Falls_Back_When_The_Selector_Call_Throws()
    {
        // Provider outage mid-run. The catch in SelectNextAgentAsync is the
        // only thing between that and a dead workflow.
        var fake = new ScriptedChatClient(call =>
        {
            if (call.Text.Contains(SelectorMarker))
            {
                throw new HttpRequestException("selector provider is down");
            }

            return ScriptedChatClient.Text("SOMETHING");
        });

        IReadOnlyList<ChatMessage> conversation =
            await Program.RunAsync(fake, "coffee slogan", "prompt");

        Speakers(conversation).Should().NotBeEmpty("the manager falls back to round-robin");
    }

    [Fact]
    public async Task The_Selector_Prompt_Lists_Every_Participant()
    {
        // If the roster is ever built from the wrong list the model can only
        // pick wrong, and the fallback quietly hides it.
        ScriptedChatClient fake = Scripted(_ => "{\"next\": \"writer\"}");

        await Program.RunAsync(fake, "coffee slogan", "prompt");

        ScriptedCall selectorCall = fake.Calls.First(c => c.Text.Contains(SelectorMarker));
        selectorCall.Text.Should().Contain("writer").And.Contain("critic").And.Contain("editor");
    }

    [Fact]
    public void The_Three_Roles_Have_Distinct_Instructions()
    {
        new[] { Program.WriterInstructions, Program.CriticInstructions, Program.EditorInstructions }
            .Distinct().Should().HaveCount(3);
    }
}
