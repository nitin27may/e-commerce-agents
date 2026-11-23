using System.ClientModel.Primitives;
using System.Text;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// Attribution headers on every model call, for an AI gateway (APIM, LiteLLM, OpenRouter, a proxy): <c>x-agent-name</c>,
/// <c>x-run-id</c> and <c>x-eval-run-id</c>. Python twin: <c>shared/gateway_headers.py</c>.
/// </summary>
/// <remarks>
/// Added by a pipeline policy (OpenAI and Azure OpenAI clients) or a delegating handler (the Anthropic client), so the
/// values are read per request: the run id is <see cref="RequestContext.CurrentRunId"/>, the agent name is pinned per
/// chat client. Never sent: the user's email, role or session id, or any credential. An empty value is omitted, values
/// are reduced to a conservative ASCII alphabet and capped. The replay client makes no HTTP call, so it never sees them
/// and the replay key (messages and tool schemas) is unaffected. A2A and MCP calls are not model calls and get none.
/// </remarks>
public static class GatewayHeaders
{
    public const string AgentNameHeader = "x-agent-name";
    public const string RunIdHeader = "x-run-id";
    public const string EvalRunIdHeader = "x-eval-run-id";
    public const int MaxValueLength = 128;

    // Embeddings clients are process singletons with no agent of their own: they read the agent most recently built
    // in this process (a specialist serves one agent), the twin of Python's current_agent_name ContextVar.
    private static volatile string _lastAgentName = "";

    /// <summary>Records which agent a chat client was just built for (embeddings attribute their calls to it).</summary>
    public static void NoteAgent(string? agentName)
    {
        if (!string.IsNullOrWhiteSpace(agentName))
        {
            _lastAgentName = agentName;
        }
    }

    /// <summary>ASCII letters, digits and <c>._:/+=-</c> only (no <c>@</c>, whitespace or control characters), capped.</summary>
    public static string Sanitize(string? value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return "";
        }

        var sb = new StringBuilder(Math.Min(value.Length, MaxValueLength));
        foreach (var c in value)
        {
            if (sb.Length >= MaxValueLength)
            {
                break;
            }

            if (c is (>= 'a' and <= 'z') or (>= 'A' and <= 'Z') or (>= '0' and <= '9')
                or '.' or '_' or ':' or '/' or '+' or '=' or '-')
            {
                sb.Append(c);
            }
        }

        return sb.ToString();
    }

    /// <summary>The headers for a model call made now; empty when disabled. A null <paramref name="agentName"/> uses the noted agent.</summary>
    public static IReadOnlyList<KeyValuePair<string, string>> Resolve(AgentSettings settings, string? agentName)
    {
        var result = new List<KeyValuePair<string, string>>(3);
        if (!settings.GatewayHeadersEnabled)
        {
            return result;
        }

        Add(result, AgentNameHeader, string.IsNullOrWhiteSpace(agentName) ? _lastAgentName : agentName);
        Add(result, RunIdHeader, RequestContext.CurrentRunId);
        Add(result, EvalRunIdHeader, settings.EvalRunId);
        return result;
    }

    private static void Add(List<KeyValuePair<string, string>> into, string name, string? raw)
    {
        var clean = Sanitize(raw);
        if (clean.Length > 0)
        {
            into.Add(new(name, clean));
        }
    }

    /// <summary>Adds the attribution policy to an OpenAI / Azure OpenAI client's options (no-op when disabled).</summary>
    public static void Apply(ClientPipelineOptions options, AgentSettings settings, string? agentName)
    {
        ArgumentNullException.ThrowIfNull(options);
        if (settings.GatewayHeadersEnabled)
        {
            options.AddPolicy(new GatewayHeadersPolicy(settings, agentName), PipelinePosition.PerCall);
        }
    }

    /// <summary>Wraps <paramref name="inner"/> so each request carries the headers; <paramref name="inner"/> is returned as is when disabled.</summary>
    public static HttpMessageHandler Wrap(HttpMessageHandler inner, AgentSettings settings, string? agentName) =>
        settings.GatewayHeadersEnabled ? new GatewayHeadersHandler(inner, settings, agentName) : inner;
}

/// <summary>System.ClientModel pipeline policy that stamps the headers on each request (OpenAI, Azure OpenAI).</summary>
public sealed class GatewayHeadersPolicy(AgentSettings settings, string? agentName) : PipelinePolicy
{
    public override void Process(PipelineMessage message, IReadOnlyList<PipelinePolicy> pipeline, int currentIndex)
    {
        Stamp(message);
        ProcessNext(message, pipeline, currentIndex);
    }

    public override async ValueTask ProcessAsync(PipelineMessage message, IReadOnlyList<PipelinePolicy> pipeline, int currentIndex)
    {
        Stamp(message);
        await ProcessNextAsync(message, pipeline, currentIndex).ConfigureAwait(false);
    }

    private void Stamp(PipelineMessage message)
    {
        foreach (var (name, value) in GatewayHeaders.Resolve(settings, agentName))
        {
            message.Request.Headers.Set(name, value);
        }
    }
}

/// <summary>HttpClient handler that stamps the headers on each request (the Anthropic client takes an HttpClient).</summary>
public sealed class GatewayHeadersHandler(HttpMessageHandler inner, AgentSettings settings, string? agentName)
    : DelegatingHandler(inner)
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        foreach (var (name, value) in GatewayHeaders.Resolve(settings, agentName))
        {
            request.Headers.Remove(name);
            request.Headers.TryAddWithoutValidation(name, value);
        }

        return base.SendAsync(request, cancellationToken);
    }
}
