using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Prompts;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenAI.Chat;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// Builds a MAF <see cref="AIAgent"/> for a specialist module. Mirrors
/// Python's <c>create_*_agent()</c> pattern: load the agent's YAML
/// system prompt, build the chat client, attach the provided tools.
/// </summary>
public static class SpecialistAgentFactory
{
    public static AIAgent Create(
        AgentSettings settings,
        PromptLoader prompts,
        string agentName,
        IEnumerable<AITool>? tools = null,
        string? userRole = null
    )
    {
        var instructions = prompts.Load(agentName, userRole);
        var chatClient = ChatClientFactory.CreateChatClient(settings);

        var toolList = tools?.ToList();
        if (toolList is { Count: > 0 })
        {
            return chatClient.AsAIAgent(
                instructions: instructions,
                name: agentName,
                tools: toolList
            );
        }

        return chatClient.AsAIAgent(instructions: instructions, name: agentName);
    }
}
