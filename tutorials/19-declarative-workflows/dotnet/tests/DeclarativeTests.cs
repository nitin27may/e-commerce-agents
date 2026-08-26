// MAF v1 — Chapter 19 tests (Declarative Workflows)
//
// The whole premise of a declarative loader is that a config file can be
// wrong. Every error path here is a message a reader will see at some point,
// and a loader whose errors are unhelpful is worse than no loader — the
// failure surfaces at runtime, in a file the compiler never checked.
//
// So the bulk of this file is validation: unknown ops, duplicate ids, dangling
// edges, a start that names nothing, missing files, malformed YAML. Each one
// asserts that the message names the thing that is actually wrong, because
// "workflow spec invalid" would technically pass every one of them.
//
// No LLM. The ops are pure string functions.

using System.Text;
using FluentAssertions;
using Microsoft.Agents.AI.Workflows;
using Xunit;

namespace MafV1.Ch19.Declarative.Tests;

public sealed class DeclarativeTests : IDisposable
{
    private readonly List<string> _tempFiles = new();

    public void Dispose()
    {
        foreach (string f in _tempFiles.Where(File.Exists))
        {
            File.Delete(f);
        }
    }

    private string SpecFile(string yaml)
    {
        string path = Path.Combine(Path.GetTempPath(), $"ch19-{Guid.NewGuid():N}.yaml");
        File.WriteAllText(path, yaml, Encoding.UTF8);
        _tempFiles.Add(path);
        return path;
    }

    private const string ValidSpec = """
        name: text-pipeline
        start: uppercase
        executors:
          - id: uppercase
            op: upper
          - id: validate
            op: non_empty
          - id: log
            op: prefix
            prefix: "LOGGED: "
        edges:
          - from: uppercase
            to: validate
          - from: validate
            to: log
        """;

    // ─────────────── The happy path ───────────────

    [Fact]
    public async Task The_Shipped_Spec_Transforms_Input_End_To_End()
    {
        Workflow workflow = DeclarativeWorkflowLoader.Load(SpecFile(ValidSpec));

        IReadOnlyList<string> outputs = await Program.RunAsync(workflow, "hello world");

        outputs.Should().ContainSingle().Which.Should().Be("LOGGED: HELLO WORLD");
    }

    [Fact]
    public async Task Blank_Input_Short_Circuits_At_The_Validator()
    {
        // non_empty yields a terminal output instead of forwarding, so the
        // logger never runs. This is the behaviour that justifies the loader
        // calling WithOutputFrom on every executor rather than just the last.
        Workflow workflow = DeclarativeWorkflowLoader.Load(SpecFile(ValidSpec));

        IReadOnlyList<string> outputs = await Program.RunAsync(workflow, "   ");

        outputs.Should().ContainSingle().Which.Should().Be("[skipped: empty input]");
        outputs.Should().NotContain(o => o.StartsWith("LOGGED:"));
    }

    [Fact]
    public async Task Reordering_The_Spec_Reorders_The_Pipeline_With_No_Code_Change()
    {
        // The claim that makes "declarative" worth the machinery. Same binary,
        // same ops, different YAML — different answer.
        string reversedFirst = """
            name: reversed
            start: rev
            executors:
              - id: rev
                op: reverse
              - id: log
                op: prefix
                prefix: "OUT: "
            edges:
              - from: rev
                to: log
            """;

        Workflow workflow = DeclarativeWorkflowLoader.Load(SpecFile(reversedFirst));

        IReadOnlyList<string> outputs = await Program.RunAsync(workflow, "abc");

        outputs.Should().ContainSingle().Which.Should().Be("OUT: cba");
    }

    [Theory]
    [InlineData("upper", "Hello", "HELLO")]
    [InlineData("lower", "Hello", "hello")]
    [InlineData("strip", "  hi  ", "hi")]
    [InlineData("reverse", "abc", "cba")]
    [InlineData("passthrough", "abc", "abc")]
    public void Every_Builtin_Transform_Op_Does_What_Its_Name_Says(string op, string input, string expected)
    {
        // Straight from the registry, so a broken op is attributed to the op
        // rather than to whatever pipeline happened to use it.
        OpFunction fn = OpRegistry.Build(new ExecutorSpec { Id = "x", Op = op });

        fn(input).Forward.Should().Be(expected);
        fn(input).Terminal.Should().BeNull();
    }

    [Fact]
    public void The_Prefix_Op_Is_Terminal_And_The_Transform_Ops_Are_Not()
    {
        // Forward vs Terminal is the contract every op is written against, and
        // getting it backwards produces a pipeline that silently stops early.
        OpFunction prefix = OpRegistry.Build(new ExecutorSpec { Id = "p", Op = "prefix", Prefix = "P: " });
        prefix("x").Should().Be((null, "P: x"));

        OpFunction upper = OpRegistry.Build(new ExecutorSpec { Id = "u", Op = "upper" });
        upper("x").Should().Be(("X", null));
    }

    [Fact]
    public void The_Non_Empty_Op_Honours_A_Custom_Empty_Output()
    {
        OpFunction fn = OpRegistry.Build(
            new ExecutorSpec { Id = "v", Op = "non_empty", EmptyOutput = "nothing to do" });

        fn("").Terminal.Should().Be("nothing to do");
        fn("x").Forward.Should().Be("x");
    }

    [Fact]
    public void Op_Names_Are_Case_Insensitive()
    {
        // The registry is built with OrdinalIgnoreCase. Worth pinning: YAML
        // authors will write `Upper` eventually, and if that ever starts
        // throwing it should be a deliberate decision.
        OpRegistry.Build(new ExecutorSpec { Id = "x", Op = "UPPER" })("a").Forward.Should().Be("A");
    }

    // ─────────────── Validation ───────────────

    [Fact]
    public void An_Unknown_Op_Names_The_Op_The_Executor_And_The_Alternatives()
    {
        // The single most likely authoring mistake — a typo in `op:`. The
        // message has to be enough to fix it without opening the source.
        var act = () => DeclarativeWorkflowLoader.Load(SpecFile("""
            name: bad
            start: a
            executors:
              - id: a
                op: uppercase
            """));

        act.Should().Throw<WorkflowSpecException>()
            .Which.Message.Should()
            .Contain("uppercase").And
            .Contain("'a'").And
            .Contain("upper", "the message must list what IS registered");
    }

    [Fact]
    public void A_Duplicate_Executor_Id_Is_Rejected()
    {
        // Silently keeping the last one would produce a workflow whose edges
        // point at an executor the author did not write.
        var act = () => DeclarativeWorkflowLoader.Load(SpecFile("""
            name: dup
            start: a
            executors:
              - id: a
                op: upper
              - id: a
                op: lower
            """));

        act.Should().Throw<WorkflowSpecException>().WithMessage("*duplicate executor id 'a'*");
    }

    [Fact]
    public void A_Start_That_Names_No_Executor_Is_Rejected()
    {
        var act = () => DeclarativeWorkflowLoader.Load(SpecFile("""
            name: bad-start
            start: nowhere
            executors:
              - id: a
                op: upper
            """));

        act.Should().Throw<WorkflowSpecException>()
            .Which.Message.Should().Contain("nowhere").And.Contain("a");
    }

    [Fact]
    public void A_Dangling_Edge_Target_Is_Rejected()
    {
        var act = () => DeclarativeWorkflowLoader.Load(SpecFile("""
            name: dangling
            start: a
            executors:
              - id: a
                op: upper
            edges:
              - from: a
                to: ghost
            """));

        act.Should().Throw<WorkflowSpecException>().WithMessage("*edge target 'ghost'*");
    }

    [Fact]
    public void A_Dangling_Edge_Source_Is_Rejected()
    {
        var act = () => DeclarativeWorkflowLoader.Load(SpecFile("""
            name: dangling
            start: a
            executors:
              - id: a
                op: upper
            edges:
              - from: ghost
                to: a
            """));

        act.Should().Throw<WorkflowSpecException>().WithMessage("*edge source 'ghost'*");
    }

    [Fact]
    public void An_Executor_Missing_Its_Op_Is_Rejected()
    {
        var act = () => DeclarativeWorkflowLoader.Load(SpecFile("""
            name: incomplete
            start: a
            executors:
              - id: a
            """));

        act.Should().Throw<WorkflowSpecException>().WithMessage("*'id' and 'op'*");
    }

    [Fact]
    public void A_Spec_With_No_Executors_Is_Rejected()
    {
        var act = () => DeclarativeWorkflowLoader.Load(SpecFile("""
            name: empty
            start: a
            executors: []
            """));

        act.Should().Throw<WorkflowSpecException>().WithMessage("*no executors declared*");
    }

    [Fact]
    public void A_Missing_Start_Is_Rejected()
    {
        var act = () => DeclarativeWorkflowLoader.Load(SpecFile("""
            name: no-start
            executors:
              - id: a
                op: upper
            """));

        act.Should().Throw<WorkflowSpecException>().WithMessage("*'start' is required*");
    }

    [Fact]
    public void A_Missing_File_Is_Rejected_With_The_Path()
    {
        string path = Path.Combine(Path.GetTempPath(), $"ch19-absent-{Guid.NewGuid():N}.yaml");

        var act = () => DeclarativeWorkflowLoader.Load(path);

        act.Should().Throw<WorkflowSpecException>().WithMessage($"*{path}*");
    }

    [Fact]
    public void Malformed_Yaml_Becomes_A_WorkflowSpecException_Not_A_Yaml_Exception()
    {
        // The loader's own exception type is part of its contract. Leaking
        // YamlDotNet's exception would force every caller to reference
        // YamlDotNet just to catch it.
        var act = () => DeclarativeWorkflowLoader.Load(SpecFile("name: [unclosed\nstart: a"));

        act.Should().Throw<WorkflowSpecException>().WithMessage("*malformed YAML*");
    }

    // ─────────────── Extension point ───────────────

    [Fact]
    public async Task A_Registered_Custom_Op_Is_Usable_From_Yaml()
    {
        // The documented extension point: register a name, reference it in
        // YAML, no loader change.
        string opName = $"shout_{Guid.NewGuid():N}";
        OpRegistry.Register(opName, _ => s => (null, s + "!!!"));

        OpRegistry.RegisteredOps.Should().Contain(opName);

        Workflow workflow = DeclarativeWorkflowLoader.Load(SpecFile($"""
            name: custom
            start: a
            executors:
              - id: a
                op: {opName}
            """));

        IReadOnlyList<string> outputs = await Program.RunAsync(workflow, "hey");

        outputs.Should().ContainSingle().Which.Should().Be("hey!!!");
    }
}
