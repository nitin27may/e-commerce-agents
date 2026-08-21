using ECommerceAgents.Evals;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Orchestrator.Agent;
using ECommerceAgents.Orchestrator.Modes;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.ContextProviders;
using ECommerceAgents.Shared.Guardrails;
using ECommerceAgents.Shared.Middleware;
using ECommerceAgents.Shared.Prompts;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using System.Text.Json;

// Evaluates the .NET stack against the same datasets the Python suite uses.
//
// Runs through ModeRegistry rather than around it. Python's original evaluator
// hand-rolled its own chat-completions loop and invoked raw undecorated functions, so
// guardrails, sanitization, HITL gates and the step recorder never ran during an eval —
// the red-team suite's passing score was not evidence the deployed system resisted
// anything. Driving the real mode is the whole design constraint here.
//
//   dotnet run --project src/ECommerceAgents.Evals -- \
//       --agent orchestrator --dataset datasets/orchestrator_routing.json \
//       --baseline baselines/orchestrator.json --max-regression 0.05

var options = CliOptions.Parse(args);
if (options is null)
{
    CliOptions.PrintUsage();
    return 2;
}

var settings = AgentSettingsLoader.Load(
    new ConfigurationBuilder().AddEnvironmentVariables().Build());

if (!File.Exists(options.DatasetPath))
{
    Console.Error.WriteLine($"Dataset not found: {options.DatasetPath}");
    return 1;
}

var cases = JsonSerializer.Deserialize<List<EvalCase>>(
    await File.ReadAllTextAsync(options.DatasetPath)) ?? [];

if (cases.Count == 0)
{
    Console.Error.WriteLine($"Dataset is empty: {options.DatasetPath}");
    return 1;
}

await using var pool = new DatabasePool(settings);
var runner = new ProductionRunner(pool, settings);
var results = new List<CaseResult>();

Console.WriteLine($"Running {cases.Count} cases against {options.Agent} "
    + $"(LLM_PROVIDER={settings.LlmProvider})\n");

foreach (var (evalCase, index) in cases.Select((c, i) => (c, i)))
{
    var outcome = await runner.RunAsync(evalCase.Input);

    var groundedness = outcome.FixtureMissing || outcome.Error is not null
        ? 0.0
        : await Scorers.GroundednessAsync(outcome.Text, pool);

    var correctness = evalCase.ExpectedRoute is { } route
        ? Scorers.Routing(route, outcome.Routes)
        : Scorers.Correctness(evalCase.ExpectedTools, outcome.ToolsCalled);

    var (completeness, missing) = Scorers.Completeness(evalCase.ExpectedFields, outcome.Text);

    var result = new CaseResult(
        evalCase.Input, groundedness, correctness, completeness,
        outcome.ToolsCalled, missing, outcome.Error, outcome.FixtureMissing);
    results.Add(result);

    var marker = result.FixtureMissing ? "MISSING" : result.Overall >= options.PassThreshold ? "PASS" : "FAIL";
    Console.WriteLine($"[{index + 1,2}/{cases.Count}] {marker,-7} {result.Overall:P0}  {Truncate(evalCase.Input, 58)}");
    if (options.Verbose && result.MissingFields.Count > 0)
    {
        Console.WriteLine($"          missing fields: {string.Join(", ", result.MissingFields)}");
    }
}

var summary = new EvalSummary(
    options.Agent,
    options.DatasetPath,
    Avg(results, r => r.Groundedness),
    Avg(results, r => r.Correctness),
    Avg(results, r => r.Completeness),
    Avg(results, r => r.Overall)
) { Cases = results };

Console.WriteLine($"""

    ── {summary.AgentName} ─────────────────────────────
      groundedness  {summary.AvgGroundedness:P1}
      correctness   {summary.AvgCorrectness:P1}
      completeness  {summary.AvgCompleteness:P1}
      overall       {summary.OverallScore:P1}
    """);

if (options.OutputJson is { } outPath)
{
    await File.WriteAllTextAsync(outPath,
        JsonSerializer.Serialize(summary, new JsonSerializerOptions { WriteIndented = true }));
}

// A missing fixture is fatal regardless of score. The agent did not run, so whatever
// number came out is meaningless — reporting it as a quality result is how a corpus
// problem gets mistaken for a regression.
if (summary.FixtureMisses > 0)
{
    Console.Error.WriteLine($"""

        {summary.FixtureMisses} case(s) had no replay fixture, so the agent never ran for them.
        Re-record against a real provider, or re-key the corpus if the hashing scheme changed.
        The score above is not a quality signal.
        """);
    return 1;
}

if (options.UpdateBaseline is { } updatePath)
{
    await File.WriteAllTextAsync(updatePath,
        JsonSerializer.Serialize(summary, new JsonSerializerOptions { WriteIndented = true }));
    Console.WriteLine($"Baseline written to {updatePath}");
    return 0;
}

if (options.BaselinePath is { } baselinePath && File.Exists(baselinePath))
{
    var baseline = JsonSerializer.Deserialize<EvalSummary>(await File.ReadAllTextAsync(baselinePath));
    if (baseline is not null)
    {
        var regressions = Compare(baseline, summary, options.MaxRegression);
        if (regressions.Count > 0)
        {
            Console.Error.WriteLine("\nRegressed against baseline:");
            regressions.ForEach(r => Console.Error.WriteLine($"  {r}"));
            return 1;
        }
        Console.WriteLine("No regression against baseline.");
    }
}
else if (options.BaselinePath is not null)
{
    // A warning, not a failure: the first run for a new dataset has nothing to compare to.
    Console.Error.WriteLine($"Baseline not found, skipping comparison: {options.BaselinePath}");
}

return summary.OverallScore >= options.PassThreshold ? 0 : 1;

static double Avg(List<CaseResult> results, Func<CaseResult, double> select) =>
    results.Count == 0 ? 0 : results.Average(select);

static string Truncate(string s, int max) =>
    s.Length <= max ? s : string.Concat(s.AsSpan(0, max - 1), "…");

static List<string> Compare(EvalSummary baseline, EvalSummary current, double maxRegression)
{
    var checks = new (string Name, double Old, double New)[]
    {
        ("groundedness", baseline.AvgGroundedness, current.AvgGroundedness),
        ("correctness", baseline.AvgCorrectness, current.AvgCorrectness),
        ("completeness", baseline.AvgCompleteness, current.AvgCompleteness),
        ("overall", baseline.OverallScore, current.OverallScore),
    };

    return checks
        .Where(c => c.New - c.Old < -maxRegression)
        .Select(c => $"{c.Name}: {c.Old:P1} → {c.New:P1}")
        .ToList();
}

/// <summary>
/// Drives a real orchestration mode, the way a chat request does.
/// </summary>
internal sealed class ProductionRunner(DatabasePool pool, AgentSettings settings)
{
    private readonly ModeRegistry _registry = BuildRegistry(pool, settings);

    public async Task<RunOutcome> RunAsync(string input)
    {
        // Identity has to be set the way the auth middleware would, because every tool
        // reads it from RequestContext rather than taking it as an argument. Without this
        // the tools refuse with "No user context available" and every case scores zero for
        // a reason that has nothing to do with the agent.
        using var scope = RequestContext.Scope(
            Environment.GetEnvironmentVariable("EVAL_USER_EMAIL") ?? "alice.johnson@gmail.com",
            "customer",
            sessionId: "");

        try
        {
            var result = await _registry.Get(ModeRegistry.DefaultMode)
                .RunAsync(input, new RunContext([]));

            return new RunOutcome(
                result.Text,
                RequestContext.CurrentSteps.Select(s => s.ToolName).ToList(),
                RequestContext.CurrentInvokedAgents.ToList());
        }
        catch (ReplayChatClient.FixtureMissingException)
        {
            return new RunOutcome("", [], [], FixtureMissing: true);
        }
        catch (Exception ex) when (ex.Message.Contains("replay fixture", StringComparison.OrdinalIgnoreCase))
        {
            // A specialist's missing fixture reaches the orchestrator as an A2A failure
            // rather than the original exception type, so the message is the only signal
            // left. Python needed the same fallback for the same reason.
            return new RunOutcome("", [], [], FixtureMissing: true);
        }
        catch (Exception ex)
        {
            return new RunOutcome("", [], [], Error: $"{ex.GetType().Name}: {ex.Message}");
        }
    }

    /// <summary>
    /// Builds the orchestrator the way <c>Program.cs</c> does.
    /// </summary>
    /// <remarks>
    /// The DI graph is replicated rather than shortcut, because the point of running
    /// through the registry is that the middleware pipeline, guardrails and step recorder
    /// are all in the path. A hand-built agent that skipped them would score a different
    /// system from the one that is deployed — which is the exact mistake Python's first
    /// evaluator made.
    /// </remarks>
    private static ModeRegistry BuildRegistry(DatabasePool pool, AgentSettings settings)
    {
        var services = new ServiceCollection();
        services.AddLogging(b => b.SetMinimumLevel(LogLevel.Warning));
        services.AddHttpClient();
        services.AddSingleton(settings);
        services.AddSingleton(pool);
        // Prompts are shared with the Python stack and live outside this project, so the
        // root is walked for rather than assumed — same helper shape as every Program.cs.
        services.AddSingleton(new PromptLoader(PromptsRoot()));
        services.AddSingleton(sp => new AuthServerClient(
            sp.GetRequiredService<IHttpClientFactory>().CreateClient("auth"), settings));
        services.AddSingleton(sp =>
        {
            var http = sp.GetRequiredService<IHttpClientFactory>().CreateClient("a2a");
            http.Timeout = TimeSpan.FromSeconds(30);
            return new A2AClient(http, settings, sp.GetRequiredService<AuthServerClient>(),
                sp.GetRequiredService<ILogger<A2AClient>>());
        });
        services.AddSingleton<OrchestratorTools>();

        // The cross-cutting pipeline SpecialistAgentFactory expects. Registered here for
        // the same reason Program.cs registers them: without these the agent is built
        // without its middleware, and an eval would score a system that is not the one
        // that ships.
        services.AddSingleton<AgentRunLogger>();
        services.AddSingleton<ToolAuditMiddleware>();
        services.AddSingleton<PiiRedactor>();
        services.AddSingleton<ContextEnricher>();
        services.AddSingleton<HitlGate>();

        var provider = services.BuildServiceProvider();
        var agent = OrchestratorAgentFactory.Create(
            settings,
            provider.GetRequiredService<PromptLoader>(),
            provider.GetRequiredService<OrchestratorTools>(),
            provider);

        return new ModeRegistry([new ToolRouterMode(agent)]);
    }

    private static string PromptsRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "python", "config", "prompts")))
        {
            dir = dir.Parent;
        }
        return dir is not null
            ? Path.Combine(dir.FullName, "agents", "python", "config", "prompts")
            : throw new InvalidOperationException("Could not locate agents/python/config/prompts.");
    }
}
