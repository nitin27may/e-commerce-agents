using Microsoft.Extensions.Configuration;

namespace ECommerceAgents.AppHost;

/// <summary>Which implementation runs the orchestrator and the five specialists.</summary>
public enum AgentStack
{
    Dotnet,
    Python,
}

/// <summary>
/// Reads the stack choice from configuration, the one switch between the two backends. A bad value fails the
/// AppHost at model-build time rather than quietly falling back, because the fallback would run the other
/// stack and the dashboard would look healthy.
/// </summary>
/// <remarks>
/// <c>AgentStack=python</c> (env var, or <c>-- --AgentStack python</c> on the command line) moves all six
/// processes. <c>AgentStackOverrides:order-management=python</c> (env var
/// <c>AgentStackOverrides__order-management</c>) moves one, which is a third way to run a mixed stack beside
/// <c>docker-compose.mixed.yml</c>. Both stacks read the same database, prompts and wire contract, so any mix works.
/// </remarks>
public static class AgentStackSelection
{
    public const string StackKey = "AgentStack";
    public const string OverridesSection = "AgentStackOverrides";

    public static AgentStack Resolve(IConfiguration configuration, string agentName)
    {
        ArgumentNullException.ThrowIfNull(configuration);
        ArgumentException.ThrowIfNullOrWhiteSpace(agentName);

        var overrideValue = configuration[$"{OverridesSection}:{agentName}"];
        return Parse(string.IsNullOrWhiteSpace(overrideValue) ? configuration[StackKey] : overrideValue);
    }

    public static AgentStack Parse(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return AgentStack.Dotnet;
        }

        return value.Trim().ToLowerInvariant() switch
        {
            "dotnet" or ".net" or "net" => AgentStack.Dotnet,
            "python" or "py" => AgentStack.Python,
            _ => throw new ArgumentException(
                $"Unknown {StackKey} '{value}'. Use 'dotnet' or 'python'.", nameof(value)),
        };
    }
}
