// MAF v1 — Chapter 20 tests (Workflow Visualization)
//
// This chapter shipped as a .NET status stub that printed API usage instead of
// running any. Two things were wrong with that:
//
//   * WorkflowVisualizer ships in Microsoft.Agents.AI.Workflows 1.1.0, so
//     there was no SDK gap to be blocked on — unlike chapter 16, which is a
//     genuine one.
//   * The usage the stub printed did not compile. It showed
//     `workflow.ToMermaidString()`, but these are static methods on
//     WorkflowVisualizer, not extension methods. Printed sample code is never
//     compiled, so nothing caught it.
//
// The second point is the one these tests are shaped around: the value of a
// visualization chapter is that the diagram matches the graph, so the
// assertions compare rendered output against the actual topology rather than
// checking that a non-empty string came back.
//
// No LLM, no key — rendering is a pure function of the graph.

using FluentAssertions;
using Microsoft.Agents.AI.Workflows;
using Xunit;

namespace MafV1.Ch20.Visualization.Tests;

public sealed class VisualizationTests
{
    private static readonly Workflow Pipeline = Program.BuildWorkflow();

    // ─────────────── Mermaid ───────────────

    [Fact]
    public void Mermaid_Output_Is_A_Flowchart()
    {
        Program.RenderMermaid(Pipeline).Should().StartWith("flowchart");
    }

    [Fact]
    public void Mermaid_Contains_Every_Executor_In_The_Graph()
    {
        string mermaid = Program.RenderMermaid(Pipeline);

        mermaid.Should().Contain("uppercase").And.Contain("validate").And.Contain("log");
    }

    [Fact]
    public void Mermaid_Contains_Every_Edge_In_The_Graph()
    {
        // The assertion that makes the chapter worth anything: a diagram
        // listing the right nodes but the wrong arrows is worse than no
        // diagram, because it is confidently wrong.
        string mermaid = Program.RenderMermaid(Pipeline);

        mermaid.Should().Contain("uppercase --> validate");
        mermaid.Should().Contain("validate --> log");
    }

    [Fact]
    public void Mermaid_Marks_The_Start_Executor()
    {
        // Direction is not recoverable from an undirected reading of the
        // edges, so the entry point has to be labelled.
        Program.RenderMermaid(Pipeline).Should().Contain("uppercase (Start)");
    }

    [Fact]
    public void Mermaid_Does_Not_Invent_Edges()
    {
        // uppercase -> log is two hops. If it ever appears as one, the
        // renderer is flattening the graph.
        Program.RenderMermaid(Pipeline).Should().NotContain("uppercase --> log");
    }

    // ─────────────── Graphviz DOT ───────────────

    [Fact]
    public void Dot_Output_Is_A_Digraph()
    {
        string dot = Program.RenderDot(Pipeline);

        dot.Should().StartWith("digraph");
        dot.TrimEnd().Should().EndWith("}");
    }

    [Fact]
    public void Dot_Contains_Every_Edge_In_The_Graph()
    {
        string dot = Program.RenderDot(Pipeline);

        dot.Should().Contain("\"uppercase\" -> \"validate\"");
        dot.Should().Contain("\"validate\" -> \"log\"");
    }

    [Fact]
    public void Both_Renderers_Describe_The_Same_Graph()
    {
        // Two formats, one topology. If they ever disagree the bug is in the
        // renderer, and it would otherwise only show up as a diagram that
        // looked subtly wrong in whichever format you happened to embed.
        string mermaid = Program.RenderMermaid(Pipeline);
        string dot = Program.RenderDot(Pipeline);

        foreach (string node in new[] { "uppercase", "validate", "log" })
        {
            mermaid.Should().Contain(node);
            dot.Should().Contain(node);
        }
    }

    // ─────────────── Determinism ───────────────

    [Fact]
    public void Rendering_Is_Deterministic()
    {
        // The stated use case is committing diagrams and diffing them in PRs.
        // That only works if identical graphs render byte-identically —
        // otherwise every build produces a spurious diff.
        Program.RenderMermaid(Program.BuildWorkflow()).Should()
            .Be(Program.RenderMermaid(Program.BuildWorkflow()));

        Program.RenderDot(Program.BuildWorkflow()).Should()
            .Be(Program.RenderDot(Program.BuildWorkflow()));
    }

    // ─────────────── The pipeline itself still works ───────────────

    [Fact]
    public async Task The_Visualized_Pipeline_Actually_Runs()
    {
        // A diagram of a workflow that does not run is a diagram of nothing.
        // This is also what distinguishes the chapter from the stub it
        // replaced, which had no workflow at all.
        var outputs = new List<string>();

        await using StreamingRun run = await InProcessExecution
            .RunStreamingAsync(Program.BuildWorkflow(), "hello world");

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent output && output.Data is string s)
            {
                outputs.Add(s);
            }
        }

        outputs.Should().ContainSingle().Which.Should().Be("LOGGED: HELLO WORLD");
    }

    [Fact]
    public async Task Blank_Input_Short_Circuits_At_Validate()
    {
        // Mirrors the Python chapter's behaviour, and proves the `validate`
        // node in the diagram is a real branch rather than a pass-through.
        var outputs = new List<string>();

        await using StreamingRun run = await InProcessExecution
            .RunStreamingAsync(Program.BuildWorkflow(), "   ");

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent output && output.Data is string s)
            {
                outputs.Add(s);
            }
        }

        outputs.Should().ContainSingle().Which.Should().Be("[skipped]");
    }
}
