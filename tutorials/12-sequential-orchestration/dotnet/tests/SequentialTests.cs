// MAF v1 — Chapter 12 tests (Sequential Orchestration)
//
// AgentWorkflowBuilder.BuildSequential is a convenience wrapper: it inserts
// the input/output adapters and forwards the shared conversation for you.
// That convenience is exactly what makes it worth testing — the wiring is
// invisible in the source, so the only way to know Writer -> Reviewer ->
// Finalizer actually holds, and that each agent really sees its
// predecessors, is to observe what reaches the model.
//
// Everything here runs against a scripted IChatClient: no key, no network,
// milliseconds. The seam is Program.RunAsync(IChatClient, topic).
//
// Why these tests exist at all: this chapter shipped with .NET code that
// built, ran, exited 0 — and never called the model. It handed a bare string
// to a workflow whose input type is List<ChatMessage>, never sent the
// TurnToken the wrapped agents wait on, and matched on an AgentResponseEvent
// this builder does not emit. Three independent silent failures, none of
// which a `dotnet build` gate can see.

using FluentAssertions;
using MafV1.Shared.Testing;
using Xunit;

namespace MafV1.Ch12.Sequential.Tests;

public sealed class SequentialTests
{
    private static ScriptedChatClient Scripted() => new("DRAFT", "REVIEW", "FINAL");

    [Fact]
    public void BuildWorkflow_Composes_Without_An_Api_Key()
    {
        // The point of the IChatClient overload: the parameterless
        // BuildWorkflow() would throw here, because BuildChatClient()
        // demands OPENAI_API_KEY.
        Program.BuildWorkflow(Scripted()).Should().NotBeNull();
    }

    [Fact]
    public async Task All_Three_Agents_Run_In_Declared_Order()
    {
        IReadOnlyList<Turn> turns = await Program.RunAsync(Scripted(), "sleep");

        turns.Should().HaveCount(3, "the pipeline is Writer -> Reviewer -> Finalizer");
        turns.Select(t => t.Text).Should().ContainInOrder("DRAFT", "REVIEW", "FINAL");
        turns.Select(t => t.ExecutorId).Should().Equal("writer", "reviewer", "finalizer");
    }

    [Fact]
    public async Task Each_Agent_Sees_The_Conversation_So_Far()
    {
        // The central claim of sequential orchestration, and the one thing a
        // reader cannot check by reading Program.cs — the forwarding happens
        // inside BuildSequential.
        ScriptedChatClient fake = Scripted();
        await Program.RunAsync(fake, "sleep");

        fake.Calls.Should().HaveCount(3);

        fake.Calls[0].Text.Should().Contain("sleep").And.NotContain("DRAFT");
        fake.Calls[1].Text.Should().Contain("sleep").And.Contain("DRAFT");
        fake.Calls[2].Text.Should().Contain("DRAFT").And.Contain("REVIEW");
    }

    [Fact]
    public async Task Each_Agent_Receives_Its_Own_Instructions()
    {
        ScriptedChatClient fake = Scripted();
        await Program.RunAsync(fake, "sleep");

        fake.Calls[0].Instructions.Should().Be(Program.WriterInstructions);
        fake.Calls[1].Instructions.Should().Be(Program.ReviewerInstructions);
        fake.Calls[2].Instructions.Should().Be(Program.FinalizerInstructions);
    }

    [Fact]
    public async Task Agents_Run_One_At_A_Time()
    {
        // Sequential's defining property, and the contrast the reader is
        // meant to draw with chapter 13. Same assertion, opposite verdict.
        var fake = new ScriptedChatClient("DRAFT", "REVIEW", "FINAL")
        {
            Delay = TimeSpan.FromMilliseconds(40),
        };

        await Program.RunAsync(fake, "sleep");

        fake.HadOverlappingCalls().Should().BeFalse();
    }

    [Fact]
    public async Task A_Failing_Agent_Does_Not_Yield_A_Complete_Pipeline()
    {
        // Sequential has no retry and no skip-on-error. What matters is that
        // a mid-pipeline failure cannot masquerade as a finished run.
        var fake = new ScriptedChatClient("DRAFT") { ThrowAfter = 1 };

        IReadOnlyList<Turn> turns;
        try
        {
            turns = await Program.RunAsync(fake, "sleep");
        }
        catch (Exception)
        {
            return; // Either shape is fine; both mean "you get no answer".
        }

        turns.Should().NotContain(t => t.ExecutorId == "finalizer");
    }

    [Fact]
    public void Instructions_Are_Distinct()
    {
        // Cheap, and it catches the copy-paste that still produces three
        // plausible turns and therefore fails nothing else.
        new[] { Program.WriterInstructions, Program.ReviewerInstructions, Program.FinalizerInstructions }
            .Distinct().Should().HaveCount(3);
    }
}
