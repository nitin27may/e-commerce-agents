using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// YAML-defined sequential workflow loader. .NET parity port of
/// <c>agents/python/shared/workflow_loader.py</c>.
/// <para>
/// Declarative YAML shape:
/// </para>
/// <code>
/// name: return-replace
/// start: check-eligibility
/// executors:
///   - id: check-eligibility
///     op: passthrough
///   - id: finalize
///     op: prefix
///     prefix: "FINAL: "
/// edges:
///   - from: check-eligibility
///     to: finalize
/// </code>
/// <para>
/// Built-in ops match the Python registry: <c>passthrough</c>,
/// <c>upper</c>, <c>lower</c>, <c>strip</c>, <c>reverse</c>,
/// <c>non_empty</c>, <c>prefix</c>. Callers can extend via
/// <see cref="DeclarativeWorkflow.RegisterOp"/>.
/// </para>
/// </summary>
public sealed class DeclarativeWorkflow
{
    public string Name { get; }
    public string Description { get; }
    public string StartId { get; }
    public IReadOnlyDictionary<string, DeclarativeExecutor> Executors { get; }
    public IReadOnlyList<DeclarativeEdge> Edges { get; }

    private DeclarativeWorkflow(
        string name,
        string description,
        string startId,
        IReadOnlyDictionary<string, DeclarativeExecutor> executors,
        IReadOnlyList<DeclarativeEdge> edges
    )
    {
        Name = name;
        Description = description;
        StartId = startId;
        Executors = executors;
        Edges = edges;
    }

    // ─────────────────────── op registry ─────────────────────

    public delegate (string? Forward, string? Terminal) OpFunc(string input);
    public delegate OpFunc OpFactory(IReadOnlyDictionary<string, object?> config);

    private static readonly Dictionary<string, OpFactory> _ops = new(StringComparer.Ordinal)
    {
        ["passthrough"] = _ => s => (s, null),
        ["upper"] = _ => s => (s.ToUpperInvariant(), null),
        ["lower"] = _ => s => (s.ToLowerInvariant(), null),
        ["strip"] = _ => s => (s.Trim(), null),
        ["reverse"] = _ => s => (new string(s.Reverse().ToArray()), null),
        ["non_empty"] = config =>
        {
            string emptyMsg = config.TryGetValue("empty_output", out var v)
                ? v?.ToString() ?? "[skipped: empty input]"
                : "[skipped: empty input]";
            return s => string.IsNullOrWhiteSpace(s) ? (null, emptyMsg) : (s, null);
        },
        ["prefix"] = config =>
        {
            string prefix = config.TryGetValue("prefix", out var v) ? v?.ToString() ?? "" : "";
            return s => (null, prefix + s);
        },
    };

    public static void RegisterOp(string name, OpFactory factory)
    {
        ArgumentException.ThrowIfNullOrEmpty(name);
        ArgumentNullException.ThrowIfNull(factory);
        _ops[name] = factory;
    }

    public static IReadOnlyCollection<string> RegisteredOps => _ops.Keys.ToList();

    // ─────────────────────── loading ─────────────────────────

    public static DeclarativeWorkflow Load(string yamlPath)
    {
        if (!File.Exists(yamlPath))
        {
            throw new WorkflowSpecException($"Workflow spec not found: {yamlPath}");
        }
        var text = File.ReadAllText(yamlPath);
        return Parse(text, source: yamlPath);
    }

    public static DeclarativeWorkflow Parse(string yaml, string source = "<string>")
    {
        var deserializer = new DeserializerBuilder()
            .WithNamingConvention(UnderscoredNamingConvention.Instance)
            .IgnoreUnmatchedProperties()
            .Build();

        Dictionary<object, object?>? raw;
        try
        {
            raw = deserializer.Deserialize<Dictionary<object, object?>>(yaml);
        }
        catch (Exception ex)
        {
            throw new WorkflowSpecException($"Malformed YAML in {source}: {ex.Message}", ex);
        }
        if (raw is null)
        {
            throw new WorkflowSpecException($"{source}: top-level must be a mapping");
        }

        foreach (var key in new[] { "name", "start", "executors", "edges" })
        {
            if (!raw.ContainsKey(key))
            {
                throw new WorkflowSpecException($"{source}: missing required key '{key}'");
            }
        }

        string name = raw["name"]?.ToString() ?? throw new WorkflowSpecException($"{source}: 'name' must be a string");
        string start = raw["start"]?.ToString() ?? throw new WorkflowSpecException($"{source}: 'start' must be a string");
        string description = raw.TryGetValue("description", out var d) ? d?.ToString() ?? "" : "";

        var executors = new Dictionary<string, DeclarativeExecutor>(StringComparer.Ordinal);
        if (raw["executors"] is not IEnumerable<object?> executorList)
        {
            throw new WorkflowSpecException($"{source}: 'executors' must be a list");
        }
        foreach (var item in executorList)
        {
            if (item is not Dictionary<object, object?> entry)
            {
                throw new WorkflowSpecException($"{source}: each executor entry must be a mapping");
            }
            if (!entry.TryGetValue("id", out var idObj) || idObj is null)
            {
                throw new WorkflowSpecException($"{source}: executor is missing 'id'");
            }
            if (!entry.TryGetValue("op", out var opObj) || opObj is null)
            {
                throw new WorkflowSpecException($"{source}: executor '{idObj}' is missing 'op'");
            }
            string eid = idObj.ToString()!;
            string op = opObj.ToString()!;
            if (executors.ContainsKey(eid))
            {
                throw new WorkflowSpecException($"{source}: duplicate executor id '{eid}'");
            }
            if (!_ops.TryGetValue(op, out var factory))
            {
                throw new WorkflowSpecException(
                    $"{source}: unknown op '{op}' for executor '{eid}'. Registered: {string.Join(", ", RegisteredOps.OrderBy(x => x))}"
                );
            }
            var config = entry
                .Where(kv => kv.Key?.ToString() is { } k && k != "id" && k != "op")
                .ToDictionary(kv => kv.Key!.ToString()!, kv => kv.Value, StringComparer.Ordinal);

            executors[eid] = new DeclarativeExecutor(eid, op, factory(config));
        }

        if (!executors.ContainsKey(start))
        {
            throw new WorkflowSpecException(
                $"{source}: start='{start}' is not among declared executor ids ({string.Join(", ", executors.Keys.OrderBy(x => x))})"
            );
        }

        var edges = new List<DeclarativeEdge>();
        if (raw["edges"] is not IEnumerable<object?> edgeList)
        {
            throw new WorkflowSpecException($"{source}: 'edges' must be a list");
        }
        foreach (var item in edgeList)
        {
            if (item is not Dictionary<object, object?> edge
                || !edge.TryGetValue("from", out var fromObj)
                || !edge.TryGetValue("to", out var toObj))
            {
                throw new WorkflowSpecException($"{source}: each edge needs 'from' and 'to'");
            }
            string src = fromObj!.ToString()!;
            string dst = toObj!.ToString()!;
            if (!executors.ContainsKey(src))
            {
                throw new WorkflowSpecException($"{source}: edge source '{src}' is not declared");
            }
            if (!executors.ContainsKey(dst))
            {
                throw new WorkflowSpecException($"{source}: edge target '{dst}' is not declared");
            }
            edges.Add(new DeclarativeEdge(src, dst));
        }

        return new DeclarativeWorkflow(name, description, start, executors, edges);
    }

    public static IReadOnlyDictionary<string, DeclarativeWorkflow> LoadDirectory(string directory)
    {
        if (!Directory.Exists(directory))
        {
            throw new WorkflowSpecException($"Workflow directory not found: {directory}");
        }
        var map = new Dictionary<string, DeclarativeWorkflow>(StringComparer.Ordinal);
        foreach (var file in Directory.EnumerateFiles(directory, "*.yaml").OrderBy(p => p))
        {
            var wf = Load(file);
            map[Path.GetFileNameWithoutExtension(file)] = wf;
        }
        return map;
    }

    // ─────────────────────── execution ───────────────────────

    public string Run(string input)
    {
        string current = input;
        string currentId = StartId;
        var visited = new HashSet<string>();

        while (true)
        {
            if (!visited.Add(currentId))
            {
                throw new InvalidOperationException($"Cycle detected at executor '{currentId}'");
            }
            var exec = Executors[currentId];
            var (forward, terminal) = exec.Op(current);
            if (terminal is not null)
            {
                return terminal;
            }
            if (forward is null)
            {
                return current;
            }
            current = forward;
            var next = Edges.FirstOrDefault(e => e.From == currentId);
            if (next is null)
            {
                return current; // no outgoing edge — terminal
            }
            currentId = next.To;
        }
    }
}

public sealed class DeclarativeExecutor
{
    public string Id { get; }
    public string Op { get; }
    public DeclarativeWorkflow.OpFunc Func { get; }

    public DeclarativeExecutor(string id, string op, DeclarativeWorkflow.OpFunc func)
    {
        Id = id;
        Op = op;
        Func = func;
    }

    internal (string? Forward, string? Terminal) Op_(string input) => Func(input);

    internal DeclarativeWorkflow.OpFunc OpFn => Func;
}

public sealed record DeclarativeEdge(string From, string To);

public sealed class WorkflowSpecException : Exception
{
    public WorkflowSpecException(string message) : base(message) { }
    public WorkflowSpecException(string message, Exception inner) : base(message, inner) { }
}

internal static class DeclarativeExecutorExtensions
{
    public static (string? Forward, string? Terminal) Op(this DeclarativeExecutor e, string input)
        => e.OpFn(input);
}
