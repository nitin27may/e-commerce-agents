// MAF v1 — Chapter 16 tests (Magentic Orchestration)
//
// History: this chapter's .NET side is a status stub, written when Magentic
// was Python-only (Microsoft.Agents.AI.Workflows 1.1.0). These tests were a
// tripwire for the day that stopped being true, and it did: Workflows 1.28.0
// exports MagenticWorkflowBuilder and its event/ledger types (found when the
// tutorials were unified onto the production MAF version, #84).
//
// The tripwire is therefore INVERTED rather than deleted. It now pins the fact
// that the types exist, so a rename or removal still makes someone look. The
// stub itself is not yet replaced by a real port — that is a follow-up — and
// its output says so honestly.

using System.Reflection;
using FluentAssertions;
using Microsoft.Agents.AI.Workflows;
using Xunit;

namespace MafV1.Ch16.Magentic.Tests;

public sealed class MagenticTests
{
    private static readonly Assembly Workflows = typeof(Workflow).Assembly;

    /// <summary>Every exported type name in Microsoft.Agents.AI.Workflows.</summary>
    private static IEnumerable<string> ExportedTypeNames =>
        Workflows.GetExportedTypes().Select(t => t.Name);

    [Fact]
    public void The_Workflows_Assembly_Now_Ships_Magentic_Types()
    {
        // The tripwire fired at 1.28.0. Pin what shipped so a rename or a
        // removal in a later bump is noticed. When the real port lands, this
        // test stays as the "the SDK still has it" control.
        ExportedTypeNames.Should().Contain("MagenticWorkflowBuilder");
        ExportedTypeNames.Should().Contain(
            name => name.Contains("Magentic", StringComparison.OrdinalIgnoreCase) && name.EndsWith("Event"),
            "Magentic emits orchestration events (plan created, replanned, progress ledger)");
    }

    [Fact]
    public void The_Python_Names_Are_Still_Not_The_DotNet_Names()
    {
        // The Python chapter is built on MagenticBuilder / StandardMagenticManager.
        // The .NET API is named differently (MagenticWorkflowBuilder), so a port
        // cannot be a transliteration of ../../python/main.py.
        Workflows.GetType("Microsoft.Agents.AI.Workflows.MagenticBuilder").Should().BeNull();
        Workflows.GetType("Microsoft.Agents.AI.Workflows.StandardMagenticManager").Should().BeNull();
    }

    [Fact]
    public void The_Sibling_Orchestration_Builders_Are_Present()
    {
        // The control for the two tests above. Without this, an assembly that
        // failed to load — or a rename of the whole namespace — would look
        // exactly like "Magentic is still missing" and the tripwire would
        // never fire.
        MethodInfo[] builders = typeof(AgentWorkflowBuilder)
            .GetMethods(BindingFlags.Public | BindingFlags.Static);

        builders.Select(m => m.Name).Should()
            .Contain("BuildSequential")
            .And.Contain("BuildConcurrent")
            .And.Contain("CreateHandoffBuilderWith")
            .And.Contain("CreateGroupChatBuilderWith");
    }

    [Fact]
    public void The_Stub_Points_Readers_At_The_Python_Implementation()
    {
        // The stub's only job is to leave a runnable .NET project that tells
        // the truth: the SDK now has Magentic, this chapter has not ported it.
        using var captured = new StringWriter();
        TextWriter original = Console.Out;
        try
        {
            Console.SetOut(captured);
            Program.Main();
        }
        finally
        {
            Console.SetOut(original);
        }

        string output = captured.ToString();
        output.Should().Contain("not yet ported");
        output.Should().Contain("tutorials/16-magentic-orchestration/python/main.py");
    }
}
