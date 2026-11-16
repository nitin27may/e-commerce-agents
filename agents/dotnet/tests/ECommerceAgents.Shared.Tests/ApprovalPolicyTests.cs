using System.Globalization;
using ECommerceAgents.Shared.Policy;
using FluentAssertions;
using Xunit;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// D-4 — the .NET approval policy engine. It reads the SAME <c>approvals.yaml</c> as Python and runs the
/// SAME golden cases (<c>agents/python/config/policies/testdata/*.yaml</c>), so the two engines cannot drift.
/// </summary>
public sealed class ApprovalPolicyTests
{
    private static readonly string PoliciesDir = LocatePoliciesDir();

    private static string LocatePoliciesDir()
    {
        var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "python", "config", "policies")))
        {
            dir = dir.Parent;
        }
        dir.Should().NotBeNull("the shared policy directory must be locatable from the test working directory");
        return Path.Combine(dir!.FullName, "agents", "python", "config", "policies");
    }

    private static ApprovalPolicy Shipped() => ApprovalPolicy.LoadFile(Path.Combine(PoliciesDir, "approvals.yaml"));

    // ── golden cases ─────────────────────────────────────────────────────────

    private sealed class GoldenFile
    {
        public List<GoldenCase> Cases { get; set; } = [];
    }

    private sealed class GoldenCase
    {
        public string Name { get; set; } = "";
        public string Kind { get; set; } = "";
        public Dictionary<string, string> Facts { get; set; } = [];
        public GoldenExpect Expect { get; set; } = new();
    }

    private sealed class GoldenExpect
    {
        public string Route { get; set; } = "";
        public string Rule { get; set; } = "";
        public List<string> Reasons { get; set; } = [];
    }

    private static List<GoldenCase> LoadGolden()
    {
        var yaml = new DeserializerBuilder().WithNamingConvention(UnderscoredNamingConvention.Instance).Build();
        return Directory
            .GetFiles(Path.Combine(PoliciesDir, "testdata"), "*.yaml")
            .Order()
            .SelectMany(f => yaml.Deserialize<GoldenFile>(File.ReadAllText(f)).Cases)
            .ToList();
    }

    public static TheoryData<string, string> GoldenIds()
    {
        var data = new TheoryData<string, string>();
        foreach (var c in LoadGolden()) data.Add(c.Kind, c.Name);
        return data;
    }

    private static Dictionary<string, object?> FactsOf(GoldenCase c)
    {
        var facts = new Dictionary<string, object?>();
        foreach (var (name, text) in c.Facts)
        {
            facts[name] = ApprovalPolicy.FactTypes[name] == "number"
                ? double.Parse(text, NumberStyles.Float, CultureInfo.InvariantCulture)
                : text;
        }
        return facts;
    }

    [Fact]
    public void ThereAreGoldenCases() => LoadGolden().Count.Should().BeGreaterThanOrEqualTo(50);

    [Theory]
    [MemberData(nameof(GoldenIds))]
    public void RoutesLikePython(string kind, string name)
    {
        var c = LoadGolden().Single(g => g.Kind == kind && g.Name == name);
        var policy = Shipped();

        var decision = policy.Evaluate(c.Kind, FactsOf(c));

        decision.Route.Should().Be(c.Expect.Route);
        decision.Reasons.Should().Equal(c.Expect.Reasons);
        decision.RuleId.Should().Be(c.Expect.Rule);
        decision.Evidence["rule"].Should().Be(c.Expect.Rule);
        decision.Version.Should().Be(policy.Version);
    }

    [Fact]
    public void EveryRuleOfEveryKindIsCoveredByAGoldenCase()
    {
        var hit = LoadGolden().Select(c => (c.Kind, c.Expect.Rule)).ToHashSet();
        foreach (var kind in Shipped().Kinds.Values)
        {
            foreach (var rule in kind.Rules) hit.Should().Contain((kind.Kind, rule.Id), $"{kind.Kind}.{rule.Id} needs a golden case");
        }
    }

    [Fact]
    public void ShippedPolicyCarriesPerKindSettingsAndCoversTheGatedTools()
    {
        var policy = Shipped();
        policy.Version.Should().NotBeNullOrWhiteSpace();
        policy.Kinds.Keys.Should().Contain(
            ["return_approval", "cancel_order", "modify_order", "process_refund", "initiate_return", "place_backorder"]
        );
        foreach (var kind in policy.Kinds.Values)
        {
            kind.SlaHours.Should().BePositive();
            kind.OnTimeout.Should().BeOneOf("reject", "expire");
        }
        policy.Kind("return_approval").EditableFields.Should().Equal("amount", "resolution");
    }

    // ── engine behaviour ─────────────────────────────────────────────────────

    private static Dictionary<string, object?> Facts(params (string, object?)[] pairs) =>
        pairs.ToDictionary(p => p.Item1, p => p.Item2);

    [Fact]
    public void UnknownKindFailsLoudly() =>
        FluentActions.Invoking(() => Shipped().Evaluate("delete_everything", Facts()))
            .Should().Throw<PolicyException>().WithMessage("*no approval policy for kind*");

    [Fact]
    public void UnknownFactNameFailsLoudly() =>
        FluentActions.Invoking(() => Shipped().Evaluate("return_approval", Facts(("customer_mood", "grumpy"))))
            .Should().Throw<PolicyException>().WithMessage("*unknown fact*");

    [Fact]
    public void WrongFactTypeFailsLoudly()
    {
        FluentActions.Invoking(() => Shipped().Evaluate("return_approval", Facts(("amount", "lots"))))
            .Should().Throw<PolicyException>();
        FluentActions.Invoking(() => Shipped().Evaluate("return_approval", Facts(("amount", true))))
            .Should().Throw<PolicyException>();
        FluentActions.Invoking(() => Shipped().Evaluate("return_approval", Facts(("loyalty_tier", 3))))
            .Should().Throw<PolicyException>();
    }

    [Fact]
    public void IntegerAndDecimalFactsAreAccepted()
    {
        Shipped().Evaluate("return_approval", Facts(("amount", 600))).Route.Should().Be("manager");
        Shipped().Evaluate("return_approval", Facts(("amount", 600.00m))).Route.Should().Be("manager");
    }

    [Fact]
    public void EvidenceExplainsTheRule()
    {
        var decision = Shipped().Evaluate("return_approval", Facts(("amount", 600.0), ("loyalty_tier", "gold")));

        var conditions = decision.Evidence["conditions"].Should().BeAssignableTo<List<Dictionary<string, object?>>>().Subject;
        conditions.Should().ContainSingle();
        conditions[0]["fact"].Should().Be("amount");
        conditions[0]["actual"].Should().Be(600.0);
    }

    // ── loader validation ────────────────────────────────────────────────────

    private const string Header = "policy_version: \"v1\"\nkinds:\n  k:\n    editable_fields: []\n    sla_hours: 4\n    on_timeout: expire\n";
    private const string Default = "      - { id: default, route: support, reason: d, when: [] }\n";

    private static string Doc(string rules) => Header + "    rules:\n" + rules;

    private static string Cond(string fact, string op, string value) =>
        $"      - {{ id: a, route: manager, reason: x, when: [{{ fact: {fact}, op: {op}, value: {value} }}] }}\n";

    [Fact]
    public void AMinimalValidDocumentLoads() =>
        ApprovalPolicy.Parse(Doc(Default)).Kind("k").Rules[0].Route.Should().Be("support");

    [Theory]
    [InlineData("policy_version: \"\"\nkinds: { k: { editable_fields: [], sla_hours: 1, on_timeout: expire, rules: [] } }\n", "policy_version")]
    [InlineData("policy_version: v\nkinds: {}\n", "kinds")]
    [InlineData("policy_version: v\nkinds: { k: { editable_fields: [], sla_hours: 0, on_timeout: expire, rules: [] } }\n", "sla_hours")]
    [InlineData("policy_version: v\nkinds: { k: { editable_fields: [], sla_hours: 1, on_timeout: approve, rules: [] } }\n", "on_timeout")]
    [InlineData("policy_version: v\nkinds: { k: { editable_fields: [], sla_hours: 1, on_timeout: expire, rules: [] } }\n", "non-empty")]
    [InlineData("policy_version: v\nsurprise: 1\nkinds: {}\n", "cannot read")]
    public void InvalidTopLevelFailsLoudly(string yaml, string message) =>
        FluentActions.Invoking(() => ApprovalPolicy.Parse(yaml)).Should().Throw<PolicyException>().WithMessage($"*{message}*");

    [Theory]
    [InlineData("amount", "gt", "1", "last rule must be unconditional")]
    [InlineData("mood", "gt", "1", "unknown fact")]
    [InlineData("amount", "matches", "1", "unknown operator")]
    [InlineData("amount", "gt", "\"x\"", "numbers")]
    [InlineData("amount", "gt", "true", "numbers")]
    [InlineData("amount", "gt", "[1]", "only 'in' takes a list")]
    [InlineData("amount", "in", "[]", "non-empty list")]
    [InlineData("amount", "in", "5", "non-empty list")]
    [InlineData("loyalty_tier", "gt", "gold", "string fact")]
    [InlineData("loyalty_tier", "eq", "platinum", "unknown loyalty tier")]
    public void InvalidConditionsFailLoudly(string fact, string op, string value, string message)
    {
        var yaml = Doc(Cond(fact, op, value) + (message.StartsWith("last rule", StringComparison.Ordinal) ? "" : Default));

        FluentActions.Invoking(() => ApprovalPolicy.Parse(yaml)).Should().Throw<PolicyException>().WithMessage($"*{message}*");
    }

    [Fact]
    public void UnknownRouteFailsLoudly() =>
        FluentActions.Invoking(() => ApprovalPolicy.Parse(Doc("      - { id: a, route: boss, reason: x, when: [] }\n")))
            .Should().Throw<PolicyException>().WithMessage("*unknown route*");

    [Fact]
    public void UnknownConditionKeyFailsLoudly() =>
        FluentActions.Invoking(() => ApprovalPolicy.Parse(Doc(
            "      - { id: a, route: manager, reason: x, when: [{ fact: amount, op: gt, value: 1, expr: 1+1 }] }\n" + Default)))
            .Should().Throw<PolicyException>().WithMessage("*cannot read*");

    [Fact]
    public void UnreachableRulesAndDuplicateIdsFailLoudly()
    {
        FluentActions.Invoking(() => ApprovalPolicy.Parse(Doc(Default + "      - { id: after, route: support, reason: d, when: [] }\n")))
            .Should().Throw<PolicyException>().WithMessage("*unreachable*");
        FluentActions.Invoking(() => ApprovalPolicy.Parse(Doc(
            "      - { id: default, route: manager, reason: x, when: [{ fact: amount, op: gt, value: 1 }] }\n" + Default)))
            .Should().Throw<PolicyException>().WithMessage("*duplicate rule id*");
    }

    [Fact]
    public void AMissingFileIsAPolicyException() =>
        FluentActions.Invoking(() => ApprovalPolicy.LoadFile(Path.Combine(PoliciesDir, "nope.yaml")))
            .Should().Throw<PolicyException>().WithMessage("*cannot read*");

    // ── operator semantics ───────────────────────────────────────────────────

    [Theory]
    [InlineData("lt", "10", 9.99, true)]
    [InlineData("lt", "10", 10.0, false)]
    [InlineData("lte", "10", 10.0, true)]
    [InlineData("lte", "10", 10.01, false)]
    [InlineData("gt", "10", 10.01, true)]
    [InlineData("gt", "10", 10.0, false)]
    [InlineData("gte", "10", 10.0, true)]
    [InlineData("gte", "10", 9.99, false)]
    [InlineData("eq", "10", 10.0, true)]
    [InlineData("eq", "10", 11.0, false)]
    [InlineData("in", "[1, 2, 3]", 2.0, true)]
    [InlineData("in", "[1, 2, 3]", 4.0, false)]
    public void Operator(string op, string value, double actual, bool expected)
    {
        var policy = ApprovalPolicy.Parse(Doc(Cond("amount", op, value) + Default));

        (policy.Evaluate("k", Facts(("amount", actual))).Route == "manager").Should().Be(expected);
    }

    [Fact]
    public void StringOperators()
    {
        var eq = ApprovalPolicy.Parse(Doc(Cond("loyalty_tier", "eq", "gold") + Default));
        eq.Evaluate("k", Facts(("loyalty_tier", "gold"))).Route.Should().Be("manager");
        eq.Evaluate("k", Facts(("loyalty_tier", "silver"))).Route.Should().Be("support");
        var member = ApprovalPolicy.Parse(Doc(Cond("loyalty_tier", "in", "[silver, gold]") + Default));
        member.Evaluate("k", Facts(("loyalty_tier", "silver"))).Route.Should().Be("manager");
        member.Evaluate("k", Facts(("loyalty_tier", "bronze"))).Route.Should().Be("support");
    }

    [Fact]
    public void AnUnknownFactNeverSatisfiesAConditionEvenALessThan()
    {
        var policy = ApprovalPolicy.Parse(Doc(Cond("amount", "lt", "1000000") + Default));

        policy.Evaluate("k", Facts()).Route.Should().Be("support");
        policy.Evaluate("k", Facts(("amount", null))).Route.Should().Be("support");
    }

    [Fact]
    public void FirstMatchWinsOverALaterMatch()
    {
        var policy = ApprovalPolicy.Parse(Doc(
            "      - { id: first, route: auto, reason: first, when: [{ fact: amount, op: lt, value: 10 }] }\n"
            + "      - { id: second, route: manager, reason: second, when: [{ fact: amount, op: lt, value: 100 }] }\n"
            + Default));

        policy.Evaluate("k", Facts(("amount", 5.0))).RuleId.Should().Be("first");
        policy.Evaluate("k", Facts(("amount", 50.0))).RuleId.Should().Be("second");
    }
}
