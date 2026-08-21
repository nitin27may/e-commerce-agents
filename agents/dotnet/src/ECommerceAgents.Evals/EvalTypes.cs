using System.Text.Json.Serialization;

namespace ECommerceAgents.Evals;

/// <summary>
/// One eval case. Schema matches Python's <c>evals/datasets/*.json</c> exactly, so the
/// two stacks can be scored on the same questions.
/// </summary>
/// <remarks>
/// The datasets port verbatim; <c>expected_tools</c> is the one field that does not,
/// because tool names differ in casing between the stacks (<c>search_products</c> vs
/// <c>SearchProducts</c>). Comparison is therefore case-insensitive and
/// underscore-insensitive rather than the names being forked into two copies that drift.
/// </remarks>
public sealed record EvalCase(
    [property: JsonPropertyName("input")] string Input,
    [property: JsonPropertyName("expected_tools")] List<string> ExpectedTools,
    [property: JsonPropertyName("expected_fields")] List<string> ExpectedFields,
    [property: JsonPropertyName("criteria")] Dictionary<string, bool>? Criteria = null,
    [property: JsonPropertyName("expected_route")] string? ExpectedRoute = null
);

/// <summary>What one production run produced — everything the scorers need.</summary>
/// <remarks>
/// Mirrors Python's <c>RunOutcome</c>. <see cref="FixtureMissing"/> is separate from
/// <see cref="Error"/> on purpose: a missing recording means the agent never ran, and
/// scoring that as a zero is how a corpus problem gets reported as a quality collapse.
/// </remarks>
public sealed record RunOutcome(
    string Text,
    List<string> ToolsCalled,
    List<string> Routes,
    GroundingSummary? Grounding = null,
    string? Error = null,
    bool FixtureMissing = false
);

public sealed record GroundingSummary(int Total, int Verified);

/// <summary>Per-case scores. Weights match Python's `evaluator.py`.</summary>
public sealed record CaseResult(
    string Input,
    double Groundedness,
    double Correctness,
    double Completeness,
    List<string> ToolsCalled,
    List<string> MissingFields,
    string? Error,
    bool FixtureMissing
)
{
    /// <summary>0.4 groundedness + 0.4 correctness + 0.2 completeness, as Python weights it.</summary>
    public double Overall => (Groundedness * 0.4) + (Correctness * 0.4) + (Completeness * 0.2);
}

public sealed record EvalSummary(
    [property: JsonPropertyName("agent_name")] string AgentName,
    [property: JsonPropertyName("dataset_path")] string DatasetPath,
    [property: JsonPropertyName("avg_groundedness")] double AvgGroundedness,
    [property: JsonPropertyName("avg_correctness")] double AvgCorrectness,
    [property: JsonPropertyName("avg_completeness")] double AvgCompleteness,
    [property: JsonPropertyName("overall_score")] double OverallScore
)
{
    [JsonIgnore]
    public List<CaseResult> Cases { get; init; } = [];

    [JsonIgnore]
    public int FixtureMisses => Cases.Count(c => c.FixtureMissing);
}
