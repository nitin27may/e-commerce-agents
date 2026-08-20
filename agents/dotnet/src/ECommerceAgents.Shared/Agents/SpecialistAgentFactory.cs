using ECommerceAgents.Shared.ContextProviders;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Prompts;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using OpenAI.Chat;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// Builds a MAF <see cref="AIAgent"/> for a specialist module. Mirrors
/// Python's <c>create_*_agent()</c> pattern: load the agent's YAML
/// system prompt, build the chat client, attach the provided tools.
/// </summary>
public static class SpecialistAgentFactory
{
    /// <param name="services">
    /// When provided, attaches the <see cref="EcommerceContextProvider"/> and
    /// wraps the agent in <see cref="SpecialistPipeline"/>'s middleware stack
    /// (issue #12) — resolving <see cref="ContextEnricher"/>,
    /// <c>AgentRunLogger</c>, <c>PiiRedactor</c> and <c>ToolAuditMiddleware</c>
    /// from it. Omit only for tests that construct a bare agent with no DI
    /// container (existing unit tests, e.g. <c>SpecialistAgentFactoryTests</c>,
    /// keep working unchanged).
    /// </param>
    public static AIAgent Create(
        AgentSettings settings,
        PromptLoader prompts,
        string agentName,
        IEnumerable<AITool>? tools = null,
        string? userRole = null,
        IServiceProvider? services = null
    )
    {
        var instructions = prompts.Load(agentName, userRole);
        var chatClient = ChatClientFactory.CreateChatClient(settings);
        var options = BuildOptions(settings, instructions, agentName, tools);
        if (services is not null)
        {
            options.AIContextProviders = [new EcommerceContextProvider(services.GetRequiredService<ContextEnricher>())];
        }

        var agent = chatClient.AsAIAgent(options);
        return services is not null ? SpecialistPipeline.Apply(agent, settings, services, agentName) : agent;
    }

    /// <summary>
    /// Pure construction-time options builder, factored out of <see cref="Create"/>
    /// so the temperature/instructions/tools wiring is unit-testable without
    /// standing up a real (network-backed) chat client.
    /// </summary>
    public static ChatClientAgentOptions BuildOptions(
        AgentSettings settings,
        string instructions,
        string agentName,
        IEnumerable<AITool>? tools = null
    )
    {
        var options = new ChatClientAgentOptions
        {
            Name = agentName,
            ChatOptions = new ChatOptions
            {
                Instructions = instructions,
                // Pins sampling temperature so identical prompts yield consistent
                // answers — mirrors Python's per-run options={"temperature": ...}
                // (shared/agent_host.py::_run_options). Set once here at
                // construction time rather than per-run since it never varies
                // per-request in this codebase.
                Temperature = (float)settings.Temperature,
            },
        };

        var toolList = tools?.ToList();
        if (toolList is { Count: > 0 })
        {
            options.ChatOptions.Tools = toolList;
        }

        return options;
    }
}
