// MAF v1 — Chapter 16 tests (Magentic Orchestration)
//
// This chapter's .NET side is a status stub, and the stub is correct:
// Magentic really is Python-only in Microsoft Agent Framework v1. So there is
// no behaviour to test. What there IS is a claim with a shelf life.
//
// A stub that says "not supported yet" is only honest while it stays true.
// Left alone it decays silently — the package gets bumped, Magentic lands, and
// the chapter goes on telling readers to go use Python. Nobody notices,
// because nothing fails.
//
// These tests are the tripwire. They assert the SDK gap still exists by
// looking in the assembly, so the day Microsoft ships MagenticBuilder this
// chapter goes red and someone has to come rewrite it. That is the whole
// point: a failure here is good news.

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
    public void The_Workflows_Assembly_Still_Has_No_Magentic_Types()
    {
        // If this fails, Magentic has shipped for .NET. Delete the stub, port
        // the Python sample in ../../python/main.py, and update the status
        // table in tutorials/README.md and its [^stub] footnote.
        ExportedTypeNames.Should().NotContain(
            name => name.Contains("Magentic", StringComparison.OrdinalIgnoreCase),
            "the .NET stub for this chapter is only correct while Magentic is Python-only");
    }

    [Fact]
    public void The_Named_Types_The_Python_Chapter_Uses_Are_Absent()
    {
        // Named explicitly, because "contains Magentic" would miss a rename.
        // These are the two symbols the Python chapter is built on.
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
        // the truth. Assert it actually says where to go instead.
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
        output.Should().Contain("not yet supported in C#");
        output.Should().Contain("tutorials/16-magentic-orchestration/python/main.py");
    }
}
