// MAF v1 — Chapter 14 tests (Handoff Orchestration)
//
// Handoff is the orchestration whose behaviour is least visible from its
// source. The mesh is declared with WithHandoffs edges, but what actually
// reaches the model is a set of synthesised tools, and the routing decision
// is a tool call the framework intercepts. None of that appears in
// Program.cs, so all of it is asserted here.
//
// The finding that motivated the sharpest test below: MAF 1.1.0 names the
// synthesised tools POSITIONALLY — handoff_to_1, handoff_to_2 — so an agent's
// name never reaches the model at all. Only the `description:` argument
// distinguishes one target from another. A chapter that treats description as
// documentation rather than routing data is teaching a misroute.

using FluentAssertions;
using MafV1.Shared.Testing;
using Microsoft.Extensions.AI;
using Xunit;

namespace MafV1.Ch14.Handoff.Tests;

public sealed class HandoffTests
{
    /// <summary>
    /// Triage hands off to whichever tool's description matches, then the
    /// specialist answers. Selecting by description rather than by tool name
    /// is not test cleverness — it is the only information the model gets.
    /// </summary>
    private static ScriptedChatClient Routing(string wantedDescriptionFragment, string answer) =>
        new(call =>
        {
            if (call.Instructions.Contains("Triage"))
            {
                string tool = call.ToolNamed(wantedDescriptionFragment);
                return ScriptedChatClient.ToolCall(tool);
            }

            return ScriptedChatClient.Text(answer);
        });

    [Fact]
    public async Task A_Math_Question_Reaches_The_Math_Tutor()
    {
        ScriptedChatClient fake = Routing("math", "37 * 42 is 1554.");

        HandoffResult result = await Program.RunAsync(fake, "What is 37 * 42?");

        result.Routing.Should().HaveCount(2);
        result.Routing[0].Should().StartWith("triage_agent");
        result.Routing[1].Should().StartWith("math_tutor");
        result.Final.Should().Be("37 * 42 is 1554.");
    }

    [Fact]
    public async Task A_History_Question_Reaches_The_History_Tutor()
    {
        // Same mesh, same code path, different tool chosen. If the builder
        // ever wires both handoffs to the same target, the math test alone
        // would still pass — this is the one that catches it.
        ScriptedChatClient fake = Routing("historical", "The war ended in 1945.");

        HandoffResult result = await Program.RunAsync(fake, "When did World War 2 end?");

        result.Routing[1].Should().StartWith("history_tutor");
        result.Final.Should().Be("The war ended in 1945.");
    }

    [Fact]
    public async Task Triage_Is_Offered_Exactly_Two_Handoff_Tools()
    {
        ScriptedChatClient fake = Routing("math", "1554");

        await Program.RunAsync(fake, "What is 37 * 42?");

        ScriptedCall triageCall = fake.Calls.First(c => c.Instructions.Contains("Triage"));
        triageCall.Tools.Should().HaveCount(2, "triage -> { math_tutor, history_tutor }");
    }

    [Fact]
    public async Task Handoff_Tools_Are_Named_Positionally_Not_By_Agent()
    {
        // Pins the surprise. If a future MAF release starts emitting
        // handoff_to_math_tutor, this test fails and the chapter's prose —
        // which currently explains the positional naming at length — needs
        // rewriting. That is exactly the signal we want.
        ScriptedChatClient fake = Routing("math", "1554");

        await Program.RunAsync(fake, "What is 37 * 42?");

        ScriptedCall triageCall = fake.Calls.First(c => c.Instructions.Contains("Triage"));
        triageCall.Tools.Should().BeEquivalentTo(new[] { "handoff_to_1", "handoff_to_2" });
        triageCall.Tools.Should().NotContain(t => t.Contains("math_tutor"));
    }

    [Fact]
    public async Task The_Agent_Description_Is_What_Identifies_A_Handoff_Target()
    {
        // The consequence of the naming above: description is routing data.
        ScriptedChatClient fake = Routing("math", "1554");

        await Program.RunAsync(fake, "What is 37 * 42?");

        ScriptedCall triageCall = fake.Calls.First(c => c.Instructions.Contains("Triage"));
        triageCall.ToolDescriptions.Should().Contain(d => d.Contains("math and arithmetic"));
        triageCall.ToolDescriptions.Should().Contain(d => d.Contains("historical questions"));
    }

    [Fact]
    public async Task A_Specialist_Can_Hand_Back_To_Triage()
    {
        // The return edge — WithHandoffs(specialists, triage) — is a separate
        // builder call and easy to forget. Without it the specialist is given
        // no tools and simply answers, which looks like success.
        ScriptedChatClient fake = Routing("math", "1554");

        await Program.RunAsync(fake, "What is 37 * 42?");

        ScriptedCall specialistCall = fake.Calls.First(c => c.Instructions.Contains("Math expert"));
        specialistCall.Tools.Should().ContainSingle("math_tutor -> { triage }");
    }

    [Fact]
    public async Task The_Specialist_Sees_The_Original_Question()
    {
        // A handoff forwards the conversation; it does not restart it. If the
        // specialist only saw the tool result it could not answer at all.
        ScriptedChatClient fake = Routing("math", "1554");

        await Program.RunAsync(fake, "What is 37 * 42?");

        fake.Calls.First(c => c.Instructions.Contains("Math expert"))
            .Text.Should().Contain("What is 37 * 42?");
    }

    [Fact]
    public async Task The_Answering_Agent_Is_Attributed_In_The_Conversation()
    {
        ScriptedChatClient fake = Routing("math", "1554");

        HandoffResult result = await Program.RunAsync(fake, "What is 37 * 42?");

        result.Conversation
            .Last(m => m.Role == ChatRole.Assistant && !string.IsNullOrWhiteSpace(m.Text))
            .AuthorName.Should().Be("math_tutor");
    }

    [Fact]
    public void The_Three_Agents_Have_Distinct_Instructions()
    {
        new[] { Program.TriageInstructions, Program.MathInstructions, Program.HistoryInstructions }
            .Distinct().Should().HaveCount(3);
    }
}
