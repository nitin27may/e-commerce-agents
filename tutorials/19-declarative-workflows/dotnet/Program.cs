// MAF v1 — Chapter 19: Declarative Workflows (.NET)
//
// Load a workflow from a YAML file at runtime instead of hand-wiring it in
// C#. Mirror of tutorials/19-declarative-workflows/python/main.py.
//
// The loader:
//   1. Deserialises workflow.yaml into a WorkflowSpec record (YamlDotNet).
//   2. Looks each executor entry's `op` up in the op registry.
//   3. Instantiates a DeclarativeExecutor per spec entry (one real Executor
//      subclass with source-generator-driven handler registration).
//   4. Feeds the executors to WorkflowBuilder.AddEdge(...) per spec.
//   5. Returns the Workflow — the same type you'd get from Ch09's hand-wired
//      WorkflowBuilder chain, now driven by a config file.
//
// For the officially supported full schema (agent invocation, HITL, control
// flow, PowerFx expressions, etc.) see Microsoft.Agents.AI.Workflows.Declarative
// and DeclarativeWorkflowBuilder.Build<TInput>(path, options).
//
// Run:
//   dotnet run                 # defaults to "hello world"
//   dotnet run -- ""           # empty  -> validate short-circuits
//   dotnet run -- "   "        # blank  -> validate short-circuits
//   dotnet run -- "maf rocks"  # happy path

using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Agents.AI.Workflows;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace MafV1.Ch19.Declarative;

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        string text = args.Length > 0 ? args[0] : "hello world";
        string specPath = Path.Combine(AppContext.BaseDirectory, "workflow.yaml");
        if (!File.Exists(specPath))
        {
            // dotnet run copies content files; fall back to the source tree
            // when running from a checked-out repo without publishing.
            specPath = Path.Combine(
                Path.GetDirectoryName(typeof(Program).Assembly.Location)!,
                "..", "..", "..", "workflow.yaml");
        }

        Workflow workflow = DeclarativeWorkflowLoader.Load(specPath);

        Console.WriteLine($"spec:   {Path.GetFileName(specPath)}");
        Console.WriteLine($"input:  '{text}'");

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, text);
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case WorkflowOutputEvent output when output.Data is string s:
                    Console.WriteLine($"output: '{s}'");
                    break;
                case ExecutorFailedEvent failed:
                    Console.Error.WriteLine($"  [fail] executor '{failed.ExecutorId}' failed: {failed.Data}");
                    return 1;
                case WorkflowErrorEvent werr:
                    Console.Error.WriteLine($"  [err]  {werr.Exception?.Message}");
                    return 1;
            }
        }

        return 0;
    }
}

// ─────────────── YAML spec records ───────────────

/// <summary>
/// The top-level declarative spec. Matches the Python schema one-for-one.
/// </summary>
public sealed class WorkflowSpec
{
    public string Name { get; set; } = "declarative";
    public string? Description { get; set; }
    public string Start { get; set; } = "";
    public List<ExecutorSpec> Executors { get; set; } = new();
    public List<EdgeSpec> Edges { get; set; } = new();
}

/// <summary>
/// A single executor entry. `Id` identifies it in edges; `Op` looks up
/// behaviour in the op registry; everything else is free-form config.
/// </summary>
public sealed class ExecutorSpec
{
    public string Id { get; set; } = "";
    public string Op { get; set; } = "";

    /// <summary>All keys other than <c>id</c>/<c>op</c> end up here.</summary>
    [YamlMember(Alias = "prefix")]
    public string? Prefix { get; set; }

    [YamlMember(Alias = "empty_output")]
    public string? EmptyOutput { get; set; }
}

public sealed class EdgeSpec
{
    public string From { get; set; } = "";
    public string To { get; set; } = "";
}

// ─────────────── Op registry ───────────────

/// <summary>
/// An "op" is a pure function from input string to
/// <c>(forward, terminal)</c>:
/// <list type="bullet">
///   <item><c>forward</c> non-null → send downstream via <c>SendMessageAsync</c>.</item>
///   <item><c>terminal</c> non-null → yield a final workflow output.</item>
/// </list>
/// Exactly one of the two should be non-null per call.
/// </summary>
public delegate (string? Forward, string? Terminal) OpFunction(string input);

public static class OpRegistry
{
    private static readonly Dictionary<string, Func<ExecutorSpec, OpFunction>> _ops =
        new(StringComparer.OrdinalIgnoreCase)
        {
            ["passthrough"] = _ => s => (s, null),
            ["upper"] = _ => s => (s.ToUpperInvariant(), null),
            ["lower"] = _ => s => (s.ToLowerInvariant(), null),
            ["strip"] = _ => s => (s.Trim(), null),
            ["reverse"] = _ => s => (new string(s.Reverse().ToArray()), null),
            ["non_empty"] = spec =>
            {
                string empty = spec.EmptyOutput ?? "[skipped: empty input]";
                return s => string.IsNullOrWhiteSpace(s) ? (null, empty) : (s, null);
            },
            ["prefix"] = spec =>
            {
                string prefix = spec.Prefix ?? "";
                return s => (null, prefix + s);
            },
        };

    /// <summary>
    /// Register a new op name so YAML specs can reference it. The single
    /// extension point for domain-specific behaviour (e.g. wrapping an
    /// existing tool call behind an op name).
    /// </summary>
    public static void Register(string name, Func<ExecutorSpec, OpFunction> factory)
        => _ops[name] = factory;

    public static IReadOnlyCollection<string> RegisteredOps => _ops.Keys.ToList();

    public static OpFunction Build(ExecutorSpec spec)
    {
        if (!_ops.TryGetValue(spec.Op, out var factory))
        {
            throw new WorkflowSpecException(
                $"unknown op '{spec.Op}' on executor '{spec.Id}'. " +
                $"Registered: {string.Join(", ", _ops.Keys)}");
        }
        return factory(spec);
    }
}

// ─────────────── Executor ───────────────

/// <summary>
/// The single MAF <see cref="Executor"/> subclass every YAML entry
/// instantiates. The source generator reads <c>[MessageHandler]</c> and
/// <c>[SendsMessage]</c>/<c>[YieldsOutput]</c> to wire handler dispatch and
/// declare the outbound type surface — identical shape to the Ch09 concrete
/// executors, but the actual behaviour comes from an <see cref="OpFunction"/>
/// set in the constructor.
/// </summary>
[SendsMessage(typeof(string))]
[YieldsOutput(typeof(string))]
internal sealed partial class DeclarativeExecutor : Executor
{
    private readonly OpFunction _op;

    public DeclarativeExecutor(string id, OpFunction op) : base(id)
    {
        _op = op;
    }

    [MessageHandler]
    public async ValueTask HandleAsync(
        string message,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        (string? forward, string? terminal) = _op(message);
        if (terminal is not null)
        {
            await context.YieldOutputAsync(terminal, cancellationToken).ConfigureAwait(false);
            return;
        }
        if (forward is not null)
        {
            await context.SendMessageAsync(forward, cancellationToken).ConfigureAwait(false);
        }
    }
}

// ─────────────── Loader ───────────────

public sealed class WorkflowSpecException : Exception
{
    public WorkflowSpecException(string message) : base(message) { }
}

public static class DeclarativeWorkflowLoader
{
    /// <summary>
    /// Load a <see cref="Workflow"/> from a YAML spec file.
    /// </summary>
    /// <exception cref="WorkflowSpecException">
    /// The file is missing, malformed, or references undeclared executor ids.
    /// </exception>
    public static Workflow Load(string specPath)
    {
        if (!File.Exists(specPath))
        {
            throw new WorkflowSpecException($"workflow spec not found: {specPath}");
        }

        IDeserializer yaml = new DeserializerBuilder()
            .WithNamingConvention(UnderscoredNamingConvention.Instance)
            .IgnoreUnmatchedProperties()
            .Build();

        WorkflowSpec spec;
        try
        {
            spec = yaml.Deserialize<WorkflowSpec>(File.ReadAllText(specPath))
                ?? throw new WorkflowSpecException($"empty spec: {specPath}");
        }
        catch (YamlDotNet.Core.YamlException ex)
        {
            throw new WorkflowSpecException($"malformed YAML in {specPath}: {ex.Message}");
        }

        if (spec.Executors.Count == 0)
        {
            throw new WorkflowSpecException($"{specPath}: no executors declared");
        }
        if (string.IsNullOrEmpty(spec.Start))
        {
            throw new WorkflowSpecException($"{specPath}: 'start' is required");
        }

        // Build the Executor for every spec entry up-front so edges can
        // reference them by id. Each wraps an op function from the registry.
        var executors = new Dictionary<string, DeclarativeExecutor>(StringComparer.Ordinal);
        foreach (ExecutorSpec entry in spec.Executors)
        {
            if (string.IsNullOrEmpty(entry.Id) || string.IsNullOrEmpty(entry.Op))
            {
                throw new WorkflowSpecException(
                    $"{specPath}: every executor needs both 'id' and 'op' (got id='{entry.Id}', op='{entry.Op}')");
            }
            if (executors.ContainsKey(entry.Id))
            {
                throw new WorkflowSpecException($"{specPath}: duplicate executor id '{entry.Id}'");
            }

            OpFunction op = OpRegistry.Build(entry);
            executors[entry.Id] = new DeclarativeExecutor(entry.Id, op);
        }

        if (!executors.TryGetValue(spec.Start, out DeclarativeExecutor? start))
        {
            throw new WorkflowSpecException(
                $"{specPath}: start='{spec.Start}' is not among declared executor ids " +
                $"({string.Join(", ", executors.Keys)})");
        }

        WorkflowBuilder builder = new(start);
        // Every declarative executor can yield a terminal output; let the
        // runtime watch all of them so validate's short-circuit and log's
        // final message both surface as WorkflowOutputEvents.
        foreach (DeclarativeExecutor exec in executors.Values)
        {
            builder = builder.WithOutputFrom(exec);
        }

        foreach (EdgeSpec edge in spec.Edges)
        {
            if (!executors.TryGetValue(edge.From, out DeclarativeExecutor? source))
            {
                throw new WorkflowSpecException($"{specPath}: edge source '{edge.From}' is not declared");
            }
            if (!executors.TryGetValue(edge.To, out DeclarativeExecutor? target))
            {
                throw new WorkflowSpecException($"{specPath}: edge target '{edge.To}' is not declared");
            }
            builder = builder.AddEdge(source, target);
        }

        return builder.Build();
    }
}
