using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Prompts;
using Microsoft.Agents.AI;

namespace ECommerceAgents.Orchestrator.Agent;

public static class OrchestratorAgentFactory
{
    public static AIAgent Create(AgentSettings settings, PromptLoader prompts, OrchestratorTools tools)
    {
        return SpecialistAgentFactory.Create(
            settings,
            prompts,
            agentName: "orchestrator",
            tools: tools.All()
        );
    }
}
