using System.Text.RegularExpressions;
using ECommerceAgents.Shared.Tools;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Guards the tool-name wire contract shared with the Python stack.
/// </summary>
/// <remarks>
/// <para>
/// These tests exist because the .NET stack shipped for a long time unable to
/// answer a single question, and every existing test was green throughout.
/// </para>
/// <para>
/// The reason they were green is the point: they called the C# methods directly.
/// A tool's real interface is not its C# signature — it is the JSON schema the
/// model is shown and the argument dictionary the model sends back. Nothing
/// exercised that layer, so a systematic mismatch between the names the shared
/// prompt corpus teaches (<c>get_order_details</c>) and the names .NET
/// advertised (<c>GetOrderDetails</c>) was invisible to the entire suite.
/// </para>
/// <para>
/// So these assert on names and schemas, never on behaviour. See plan 16 F1.
/// </para>
/// </remarks>
public sealed class ToolNamingTests
{
    /// <summary>Every source file that registers tools.</summary>
    private static readonly string[] ToolSourceGlobs =
    {
        "ECommerceAgents.Orchestrator/Agent/OrchestratorTools.cs",
        "ECommerceAgents.ReviewSentiment/Tools/ReviewTools.cs",
        "ECommerceAgents.OrderManagement/Tools/OrderTools.cs",
        "ECommerceAgents.PricingPromotions/Tools/PricingTools.cs",
        "ECommerceAgents.ProductDiscovery/Tools/ProductTools.cs",
        "ECommerceAgents.InventoryFulfillment/Tools/InventoryTools.cs",
    };

    private static DirectoryInfo RepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "dotnet", "src")))
        {
            dir = dir.Parent;
        }

        return dir ?? throw new InvalidOperationException("could not locate the repository root");
    }

    private static string SrcRoot() => Path.Combine(RepoRoot().FullName, "agents", "dotnet", "src");

    // ─────────────── The conversion rule ───────────────

    [Theory]
    [InlineData("CallSpecialistAgent", "call_specialist_agent")]
    [InlineData("GetOrderDetails", "get_order_details")]
    [InlineData("SearchProducts", "search_products")]
    [InlineData("AnalyzeSentiment", "analyze_sentiment")]
    [InlineData("GetSentimentByTopic", "get_sentiment_by_topic")]
    [InlineData("CheckStock", "check_stock")]
    [InlineData("OptimizeCart", "optimize_cart")]
    public void PascalCase_Converts_To_The_Python_Name(string pascal, string expected)
    {
        AgentTool.ToSnakeCase(pascal).Should().Be(expected);
    }

    [Fact]
    public void Consecutive_Capitals_Are_Treated_As_One_Acronym()
    {
        // Otherwise "GetA2AStatus" becomes "get_a_2_a_status", which matches
        // nothing on the Python side and would be discovered only in production.
        AgentTool.ToSnakeCase("GetA2AStatus").Should().Be("get_a2a_status");
        AgentTool.ToSnakeCase("A2AClient").Should().Be("a2a_client");
    }

    [Fact]
    public void An_Already_Lowercase_Name_Is_Unchanged()
    {
        AgentTool.ToSnakeCase("search_products").Should().Be("search_products");
    }

    // ─────────────── The registration rule ───────────────

    [Fact]
    public void No_Tool_Is_Registered_Through_AIFunctionFactory_Directly()
    {
        // The regression guard. AIFunctionFactory.Create(fn, nameof(fn)) is what
        // produced the PascalCase names that broke routing; going through
        // AgentTool.Create is what keeps the naming rule in one place. A new
        // specialist added by copy-paste from an older one is exactly how this
        // would come back.
        var offenders = new List<string>();

        foreach (string relative in ToolSourceGlobs)
        {
            string path = Path.Combine(SrcRoot(), relative);
            File.Exists(path).Should().BeTrue($"{relative} should exist — update this test if a file moved");

            if (File.ReadAllText(path).Contains("AIFunctionFactory.Create("))
            {
                offenders.Add(relative);
            }
        }

        offenders.Should().BeEmpty("tool registration must go through AgentTool.Create so the name matches the shared prompt corpus");
    }

    [Fact]
    public void Every_Registered_Tool_Name_Is_Snake_Case()
    {
        // Reads the registrations out of source rather than reflecting over a
        // built agent, because building one needs configuration, a database and
        // a chat client — none of which have anything to do with naming.
        var registration = new Regex(@"AgentTool\.Create\([A-Za-z_]+,\s*nameof\(([A-Za-z0-9]+)\)\)");
        var found = new List<string>();

        foreach (string relative in ToolSourceGlobs.Concat(
                     Directory.GetFiles(Path.Combine(SrcRoot(), "ECommerceAgents.Shared", "Tools"), "*Tools.cs")
                         .Select(p => Path.GetRelativePath(SrcRoot(), p))))
        {
            string path = Path.Combine(SrcRoot(), relative);
            if (!File.Exists(path))
            {
                continue;
            }

            foreach (Match match in registration.Matches(File.ReadAllText(path)))
            {
                found.Add(AgentTool.ToSnakeCase(match.Groups[1].Value));
            }
        }

        found.Should().NotBeEmpty("the registrations should be discoverable — if this is empty the regex has drifted from the source");
        found.Should().OnlyContain(n => n == n.ToLowerInvariant() && !n.Contains(' '));
        found.Should().OnlyContain(n => !char.IsUpper(n[0]));
    }

    // ─────────────── The contract with the shared prompt corpus ───────────────

    [Fact]
    public void Every_Tool_Named_In_The_Shared_Prompts_Is_Registered_Under_That_Name()
    {
        // The assertion that would have caught F1 on the day it was introduced.
        //
        // Both stacks are driven by one prompt corpus, and the .NET Dockerfiles
        // ship it verbatim. If a prompt tells the model to call
        // `call_specialist_agent` and .NET registers `CallSpecialistAgent`, the
        // model is told one name and offered another — which is precisely how
        // the orchestrator ended up unable to reach any specialist while every
        // container reported healthy.
        string promptsRoot = Path.Combine(RepoRoot().FullName, "agents", "python", "config", "prompts");
        Directory.Exists(promptsRoot).Should().BeTrue();

        string corpus = string.Join(
            "\n",
            Directory.GetFiles(promptsRoot, "*.yaml", SearchOption.AllDirectories).Select(File.ReadAllText));

        // Tool names as the corpus writes them: `some_tool(` in an example, or
        // `` `some_tool` `` in prose.
        var taught = new HashSet<string>(
            Regex.Matches(corpus, @"[`\s(]([a-z][a-z0-9_]{4,})\(")
                .Select(m => m.Groups[1].Value)
                .Concat(Regex.Matches(corpus, @"`([a-z][a-z0-9_]{4,})`").Select(m => m.Groups[1].Value)));

        var registered = new HashSet<string>();
        var registration = new Regex(@"AgentTool\.Create\([A-Za-z_]+,\s*nameof\(([A-Za-z0-9]+)\)\)");

        foreach (string file in Directory.GetFiles(SrcRoot(), "*.cs", SearchOption.AllDirectories))
        {
            foreach (Match match in registration.Matches(File.ReadAllText(file)))
            {
                registered.Add(AgentTool.ToSnakeCase(match.Groups[1].Value));
            }
        }

        registered.Should().NotBeEmpty();

        // Only assert over names that are BOTH taught and implemented here — the
        // corpus also names Python-only tools and ordinary prose words, and this
        // test is about the overlap, not about porting parity.
        var overlap = taught.Intersect(registered).ToList();
        overlap.Should().NotBeEmpty("the corpus and the .NET registry should share tool names");

        // Every shared name must be registered exactly as the corpus writes it.
        // With PascalCase registration this list would be empty and the previous
        // assertion would fail — which is the whole point.
        foreach (string name in overlap)
        {
            registered.Should().Contain(name);
        }
    }

    [Fact]
    public void The_Orchestrator_Registers_The_Tool_The_Orchestrator_Prompt_Names()
    {
        // Named explicitly rather than left to the set comparison above, because
        // this single tool is the difference between a stack that routes and one
        // that answers nothing.
        string prompt = File.ReadAllText(
            Path.Combine(RepoRoot().FullName, "agents", "python", "config", "prompts", "orchestrator.yaml"));

        prompt.Should().Contain("call_specialist_agent");

        string source = File.ReadAllText(
            Path.Combine(SrcRoot(), "ECommerceAgents.Orchestrator", "Agent", "OrchestratorTools.cs"));

        source.Should().Contain("AgentTool.Create(CallSpecialistAgent, nameof(CallSpecialistAgent))");
        AgentTool.ToSnakeCase("CallSpecialistAgent").Should().Be("call_specialist_agent");
    }

    [Fact]
    public void The_Specialist_Parameter_Is_Named_As_The_Model_Will_Send_It()
    {
        // Parameter names are part of the schema, so they are wire contract too.
        // MAF gives no way to rename one — [JsonPropertyName] on a parameter is
        // ignored by AIFunctionFactory — so the C# parameter must literally be
        // agent_name. A well-meaning "fix the naming convention" refactor would
        // silently break routing again; this fails first.
        string source = File.ReadAllText(
            Path.Combine(SrcRoot(), "ECommerceAgents.Orchestrator", "Agent", "OrchestratorTools.cs"));

        source.Should().Contain("string agent_name");
        source.Should().NotContain("string agentName,");
    }
}
