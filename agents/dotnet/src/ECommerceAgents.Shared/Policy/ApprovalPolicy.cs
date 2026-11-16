using System.Globalization;
using YamlDotNet.Core;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace ECommerceAgents.Shared.Policy;

/// <summary>The policy file is invalid, or a kind / fact asked of the engine is unknown.</summary>
public sealed class PolicyException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>Where an approval goes and why. <see cref="Evidence"/> is deterministic: the rule, its conditions, the facts.</summary>
public sealed record PolicyDecision(
    string Route,
    IReadOnlyList<string> Reasons,
    string RuleId,
    IReadOnlyDictionary<string, object?> Evidence,
    string Version
);

/// <summary>One condition: <c>fact op value</c>. <see cref="Value"/> is a double / string, or a list of them for <c>in</c>.</summary>
public sealed record PolicyCondition(string Fact, string Op, object Value);

public sealed record PolicyRule(string Id, string Route, string Reason, IReadOnlyList<PolicyCondition> When);

public sealed record KindPolicy(
    string Kind,
    IReadOnlyList<string> EditableFields,
    int SlaHours,
    string OnTimeout,
    IReadOnlyList<PolicyRule> Rules
);

/// <summary>
/// The approval routing policy: an ordered rule list per approval kind, first match wins, operators
/// <c>lt/lte/gt/gte/eq/in</c> only and no expression evaluation. Reads
/// <c>agents/python/config/policies/approvals.yaml</c> — the SAME file Python's
/// <c>shared/policy</c> reads — and validates it whole at load time, failing loudly on anything unknown.
/// The golden cases in <c>config/policies/testdata/*.yaml</c> are run by both stacks' tests so the two
/// engines cannot drift. Not wired into any route yet.
/// </summary>
public sealed class ApprovalPolicy
{
    /// <summary>Every fact the engine knows and its type: <c>number</c> or <c>string</c>.</summary>
    public static readonly IReadOnlyDictionary<string, string> FactTypes = new Dictionary<string, string>
    {
        ["amount"] = "number",
        ["days_since_delivery"] = "number",
        ["loyalty_tier"] = "string",
        ["returns_90d"] = "number",
        ["refund_to_spend_ratio"] = "number",
        ["account_age_days"] = "number",
    };

    private static readonly HashSet<string> Operators = ["lt", "lte", "gt", "gte", "eq", "in"];
    private static readonly HashSet<string> Routes = ["auto", "support", "manager"];
    private static readonly HashSet<string> OnTimeoutActions = ["reject", "expire"];
    private static readonly HashSet<string> LoyaltyTiers = ["bronze", "silver", "gold"];

    private static readonly IDeserializer Yaml = new DeserializerBuilder()
        .WithNamingConvention(UnderscoredNamingConvention.Instance)
        .Build();

    public string Version { get; }
    public IReadOnlyDictionary<string, KindPolicy> Kinds { get; }

    private ApprovalPolicy(string version, IReadOnlyDictionary<string, KindPolicy> kinds)
    {
        Version = version;
        Kinds = kinds;
    }

    public KindPolicy Kind(string name) =>
        Kinds.TryGetValue(name, out var kind) ? kind : throw new PolicyException($"no approval policy for kind '{name}'");

    // ── loading ──────────────────────────────────────────────────────────────

    public static ApprovalPolicy LoadFile(string path)
    {
        string text;
        try
        {
            text = File.ReadAllText(path);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            throw new PolicyException($"cannot read approval policy {Path.GetFileName(path)}", ex);
        }
        return Parse(text);
    }

    public static ApprovalPolicy Parse(string yaml)
    {
        PolicyDoc? doc;
        try
        {
            doc = Yaml.Deserialize<PolicyDoc>(yaml);
        }
        catch (YamlException ex)
        {
            throw new PolicyException($"cannot read approval policy: {ex.Message}", ex);
        }
        if (doc is null) throw new PolicyException("policy: expected a mapping");
        if (string.IsNullOrWhiteSpace(doc.PolicyVersion))
            throw new PolicyException("policy_version must be a non-empty string");
        if (doc.Kinds is null || doc.Kinds.Count == 0) throw new PolicyException("kinds: expected a non-empty mapping");

        var kinds = new Dictionary<string, KindPolicy>();
        foreach (var (name, raw) in doc.Kinds) kinds[name] = BuildKind(name, raw);
        return new ApprovalPolicy(doc.PolicyVersion, kinds);
    }

    private static KindPolicy BuildKind(string name, KindDoc? raw)
    {
        var where = $"kinds.{name}";
        if (raw is null) throw new PolicyException($"{where}: expected a mapping");
        if (raw.EditableFields is null || raw.EditableFields.Any(string.IsNullOrEmpty))
            throw new PolicyException($"{where}.editable_fields: expected a list of field names");
        if (raw.SlaHours is not > 0) throw new PolicyException($"{where}.sla_hours: expected a positive integer");
        if (raw.OnTimeout is null || !OnTimeoutActions.Contains(raw.OnTimeout))
            throw new PolicyException($"{where}.on_timeout: must be one of reject, expire");
        if (raw.Rules is null || raw.Rules.Count == 0) throw new PolicyException($"{where}.rules: expected a non-empty list");

        var rules = raw.Rules.Select((r, i) => BuildRule(r, $"{where}.rules[{i}]")).ToList();
        if (rules.Select(r => r.Id).Distinct().Count() != rules.Count)
            throw new PolicyException($"{where}.rules: duplicate rule id");
        if (rules[^1].When.Count > 0)
            throw new PolicyException($"{where}.rules: the last rule must be unconditional so a route is always found");
        if (rules.Take(rules.Count - 1).Any(r => r.When.Count == 0))
            throw new PolicyException($"{where}.rules: an unconditional rule before the last one makes the rest unreachable");
        return new KindPolicy(name, raw.EditableFields, raw.SlaHours.Value, raw.OnTimeout, rules);
    }

    private static PolicyRule BuildRule(RuleDoc? raw, string where)
    {
        if (raw is null) throw new PolicyException($"{where}: expected a mapping");
        foreach (var (key, value) in new[] { ("id", raw.Id), ("route", raw.Route), ("reason", raw.Reason) })
        {
            if (string.IsNullOrWhiteSpace(value)) throw new PolicyException($"{where}: '{key}' must be a non-empty string");
        }
        if (!Routes.Contains(raw.Route!)) throw new PolicyException($"{where}: unknown route '{raw.Route}'");
        if (raw.When is null) throw new PolicyException($"{where}: 'when' must be a list (empty for an unconditional rule)");
        var when = raw.When.Select((c, i) => BuildCondition(c, $"{where}.when[{i}]")).ToList();
        return new PolicyRule(raw.Id!, raw.Route!, raw.Reason!, when);
    }

    private static PolicyCondition BuildCondition(ConditionDoc? raw, string where)
    {
        if (raw is null || raw.Fact is null || raw.Op is null || raw.Value is null)
            throw new PolicyException($"{where}: needs fact, op and value");
        if (!FactTypes.TryGetValue(raw.Fact, out var factType)) throw new PolicyException($"{where}: unknown fact '{raw.Fact}'");
        if (!Operators.Contains(raw.Op)) throw new PolicyException($"{where}: unknown operator '{raw.Op}'");
        if (factType == "string" && raw.Op is not ("eq" or "in"))
            throw new PolicyException($"{where}: operator '{raw.Op}' does not apply to string fact '{raw.Fact}'");

        if (raw.Op == "in")
        {
            if (raw.Value is not List<object> items || items.Count == 0)
                throw new PolicyException($"{where}: 'in' needs a non-empty list");
            return new PolicyCondition(raw.Fact, raw.Op, items.Select(v => Scalar(raw.Fact, factType, v, where)).ToList());
        }
        if (raw.Value is List<object>) throw new PolicyException($"{where}: only 'in' takes a list");
        return new PolicyCondition(raw.Fact, raw.Op, Scalar(raw.Fact, factType, raw.Value, where));
    }

    private static object Scalar(string fact, string factType, object value, string where)
    {
        var text = value as string;
        if (factType == "number")
        {
            if (
                text is null
                || !double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var number)
                || !double.IsFinite(number)
            )
                throw new PolicyException($"{where}: fact '{fact}' compares against numbers, got '{text ?? value}'");
            return number;
        }
        if (text is null) throw new PolicyException($"{where}: fact '{fact}' compares against strings");
        if (fact == "loyalty_tier" && !LoyaltyTiers.Contains(text))
            throw new PolicyException($"{where}: unknown loyalty tier '{text}'");
        return text;
    }

    // ── evaluation ───────────────────────────────────────────────────────────

    /// <summary>
    /// Route <paramref name="kind"/> given <paramref name="facts"/>: the first rule whose conditions all hold
    /// decides. A missing or null fact is unknown and conditions on it are not met. Number facts must be
    /// numeric (double, float, decimal or an integer type), string facts must be strings; an unrecognised
    /// fact name or a wrong type throws <see cref="PolicyException"/>.
    /// </summary>
    public PolicyDecision Evaluate(string kind, IReadOnlyDictionary<string, object?> facts)
    {
        var normalised = Normalise(facts);
        var policy = Kind(kind);
        foreach (var rule in policy.Rules)
        {
            if (!rule.When.All(c => Holds(c, normalised[c.Fact]))) continue;
            return new PolicyDecision(rule.Route, [rule.Reason], rule.Id, EvidenceFor(rule, normalised), Version);
        }
        throw new PolicyException($"no rule matched for kind '{kind}'");
    }

    private static Dictionary<string, object?> Normalise(IReadOnlyDictionary<string, object?> facts)
    {
        var unknown = facts.Keys.Where(k => !FactTypes.ContainsKey(k)).Order().ToList();
        if (unknown.Count > 0) throw new PolicyException($"unknown fact(s): {string.Join(", ", unknown)}");

        var result = new Dictionary<string, object?>();
        foreach (var (name, type) in FactTypes)
        {
            facts.TryGetValue(name, out var value);
            if (value is null)
            {
                result[name] = null;
            }
            else if (type == "number")
            {
                result[name] = value switch
                {
                    double d => d,
                    float f => (double)f,
                    decimal m => (double)m,
                    int or long or short or byte or uint or ulong or ushort or sbyte =>
                        Convert.ToDouble(value, CultureInfo.InvariantCulture),
                    _ => throw new PolicyException($"fact '{name}' must be a number"),
                };
                if (!double.IsFinite((double)result[name]!)) throw new PolicyException($"fact '{name}' must be a number");
            }
            else
            {
                result[name] = value as string ?? throw new PolicyException($"fact '{name}' must be a string");
            }
        }
        return result;
    }

    private static bool Holds(PolicyCondition cond, object? actual)
    {
        if (actual is null) return false; // unknown evidence never satisfies a condition
        if (cond.Op == "in") return ((List<object>)cond.Value).Any(v => v.Equals(actual));
        if (cond.Op == "eq") return cond.Value.Equals(actual);
        var a = (double)actual; // ordering operators only reach number facts: the loader refuses them on strings
        var e = (double)cond.Value;
        return cond.Op switch
        {
            "lt" => a < e,
            "lte" => a <= e,
            "gt" => a > e,
            "gte" => a >= e,
            _ => throw new PolicyException($"unknown operator '{cond.Op}'"),
        };
    }

    private static Dictionary<string, object?> EvidenceFor(PolicyRule rule, Dictionary<string, object?> facts) =>
        new()
        {
            ["rule"] = rule.Id,
            ["conditions"] = rule.When.Select(c => new Dictionary<string, object?>
            {
                ["fact"] = c.Fact,
                ["op"] = c.Op,
                ["value"] = c.Value,
                ["actual"] = facts[c.Fact],
            }).ToList(),
            ["facts"] = new Dictionary<string, object?>(facts),
        };

    // ── YAML shape (unknown keys are rejected by YamlDotNet) ──────────────────

    private sealed class PolicyDoc
    {
        public string? PolicyVersion { get; set; }
        public Dictionary<string, KindDoc?>? Kinds { get; set; }
    }

    private sealed class KindDoc
    {
        public List<string>? EditableFields { get; set; }
        public int? SlaHours { get; set; }
        public string? OnTimeout { get; set; }
        public List<RuleDoc?>? Rules { get; set; }
    }

    private sealed class RuleDoc
    {
        public string? Id { get; set; }
        public string? Route { get; set; }
        public string? Reason { get; set; }
        public List<ConditionDoc?>? When { get; set; }
    }

    private sealed class ConditionDoc
    {
        public string? Fact { get; set; }
        public string? Op { get; set; }
        public object? Value { get; set; }
    }
}
