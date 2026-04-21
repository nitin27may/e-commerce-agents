using ECommerceAgents.Shared.Workflows;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

public sealed class DeclarativeWorkflowTests
{
    // ─────────────────────── parsing ─────────────────────────

    [Fact]
    public void Parse_ValidSpec_BuildsWorkflow()
    {
        const string yaml = """
            name: pipeline
            description: demo
            start: strip
            executors:
              - id: strip
                op: strip
              - id: upper
                op: upper
              - id: prefix
                op: prefix
                prefix: "FINAL: "
            edges:
              - from: strip
                to: upper
              - from: upper
                to: prefix
            """;
        var wf = DeclarativeWorkflow.Parse(yaml);
        wf.Name.Should().Be("pipeline");
        wf.StartId.Should().Be("strip");
        wf.Executors.Should().HaveCount(3);
        wf.Edges.Should().HaveCount(2);
    }

    [Fact]
    public void Parse_MissingRequiredKey_Throws()
    {
        const string yaml = "name: oops\nstart: a\nexecutors: []";
        var act = () => DeclarativeWorkflow.Parse(yaml);
        act.Should().Throw<WorkflowSpecException>()
            .WithMessage("*missing required key 'edges'*");
    }

    [Fact]
    public void Parse_UnknownOp_Throws()
    {
        const string yaml = """
            name: x
            start: a
            executors:
              - id: a
                op: explode
            edges: []
            """;
        var act = () => DeclarativeWorkflow.Parse(yaml);
        act.Should().Throw<WorkflowSpecException>()
            .WithMessage("*unknown op 'explode'*");
    }

    [Fact]
    public void Parse_UnknownStart_Throws()
    {
        const string yaml = """
            name: x
            start: ghost
            executors:
              - id: a
                op: passthrough
            edges: []
            """;
        var act = () => DeclarativeWorkflow.Parse(yaml);
        act.Should().Throw<WorkflowSpecException>()
            .WithMessage("*start='ghost'*");
    }

    [Fact]
    public void Parse_EdgeTargetNotDeclared_Throws()
    {
        const string yaml = """
            name: x
            start: a
            executors:
              - id: a
                op: passthrough
            edges:
              - from: a
                to: missing
            """;
        var act = () => DeclarativeWorkflow.Parse(yaml);
        act.Should().Throw<WorkflowSpecException>()
            .WithMessage("*edge target 'missing'*");
    }

    // ─────────────────────── execution ───────────────────────

    [Fact]
    public void Run_TransformsThroughPipeline()
    {
        const string yaml = """
            name: pipeline
            start: strip
            executors:
              - id: strip
                op: strip
              - id: upper
                op: upper
              - id: prefix
                op: prefix
                prefix: "FINAL: "
            edges:
              - from: strip
                to: upper
              - from: upper
                to: prefix
            """;
        var wf = DeclarativeWorkflow.Parse(yaml);
        wf.Run("  hello  ").Should().Be("FINAL: HELLO");
    }

    [Fact]
    public void Run_NonEmpty_ShortCircuitsOnBlankInput()
    {
        const string yaml = """
            name: pipe
            start: gate
            executors:
              - id: gate
                op: non_empty
                empty_output: "[empty]"
              - id: upper
                op: upper
            edges:
              - from: gate
                to: upper
            """;
        var wf = DeclarativeWorkflow.Parse(yaml);
        wf.Run("   ").Should().Be("[empty]");
        wf.Run("hello").Should().Be("HELLO");
    }

    // ─────────────────────── mermaid ─────────────────────────

    [Fact]
    public void Mermaid_RendersFlowchartWithNodesAndEdges()
    {
        const string yaml = """
            name: demo
            start: a
            executors:
              - id: a
                op: upper
              - id: b
                op: reverse
            edges:
              - from: a
                to: b
            """;
        var wf = DeclarativeWorkflow.Parse(yaml);
        var text = MermaidExporter.ToMermaid(wf);
        text.Should().Contain("flowchart TD");
        text.Should().Contain("a[\"a<br/>op: upper\"]");
        text.Should().Contain("b[\"b<br/>op: reverse\"]");
        text.Should().Contain("a --> b");
        text.Should().Contain("a:::start");
    }
}
