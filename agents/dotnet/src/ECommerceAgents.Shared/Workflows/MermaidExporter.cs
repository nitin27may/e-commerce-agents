using System.Text;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// Renders a <see cref="DeclarativeWorkflow"/> as Mermaid <c>flowchart</c>
/// text. .NET complement to the Python
/// <c>scripts/visualize_workflows.py</c> CLI — emits diagrams that
/// can be pasted into READMEs or rendered in CI.
/// </summary>
public static class MermaidExporter
{
    public static string ToMermaid(DeclarativeWorkflow workflow)
    {
        ArgumentNullException.ThrowIfNull(workflow);

        var sb = new StringBuilder();
        sb.AppendLine($"%% workflow: {workflow.Name}");
        sb.AppendLine("flowchart TD");

        foreach (var exec in workflow.Executors.Values.OrderBy(e => e.Id, StringComparer.Ordinal))
        {
            string label = string.IsNullOrEmpty(exec.Op)
                ? exec.Id
                : $"{exec.Id}<br/>op: {exec.Op}";
            sb.AppendLine($"    {Sanitize(exec.Id)}[\"{label}\"]");
        }

        // Mark start node.
        sb.AppendLine($"    {Sanitize(workflow.StartId)}:::start");

        foreach (var edge in workflow.Edges)
        {
            sb.AppendLine($"    {Sanitize(edge.From)} --> {Sanitize(edge.To)}");
        }

        sb.AppendLine("    classDef start fill:#0d6efd,stroke:#0a58ca,color:#fff");
        return sb.ToString();
    }

    private static string Sanitize(string id)
    {
        var cleaned = new StringBuilder(id.Length);
        foreach (var c in id)
        {
            cleaned.Append(char.IsLetterOrDigit(c) ? c : '_');
        }
        return cleaned.ToString();
    }
}
