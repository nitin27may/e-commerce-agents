using System.Reflection;
using System.Text.Json;
using A2A;
using ECommerceAgents.Shared.Guardrails;
using Microsoft.Agents.AI;
using AgentSkill = global::A2A.AgentSkill;
using Microsoft.Extensions.AI;

namespace ECommerceAgents.Shared.A2A.Protocol;

/// <summary>
/// The A2A 1.0 agent card for one specialist: built from the live agent so it cannot drift from what the
/// agent can actually do. Twin of Python's <c>build_agent_card</c>.
/// </summary>
public static class SpecialistAgentCard
{
    /// <summary>
    /// One skill per tool the least-privileged role may call: its name and description, verbatim. The card
    /// is public (discovery is the point, and the auth middleware lets it through unauthenticated), so it
    /// must not advertise a tool a caller could not use: an admin-only tool's name and description are
    /// internal. The source of truth for who may call a tool is <see cref="RequiresRoleAttribute"/> on its
    /// method; a tool with none, or one whose roles include <c>customer</c>, is listed.
    /// </summary>
    public static IReadOnlyList<AgentSkill> Skills(AIAgent? agent, string agentName)
    {
        var tools = agent?.GetService<ChatOptions>()?.Tools;
        var skills = new List<AgentSkill>();
        if (tools is null)
        {
            return skills;
        }

        foreach (var tool in tools.OfType<AIFunction>())
        {
            var roles = tool.UnderlyingMethod?.GetCustomAttribute<RequiresRoleAttribute>()?.Roles;
            if (!RequiresRoleAttribute.Allows(roles, RequiresRoleAttribute.LeastPrivilegedRole))
            {
                continue;
            }

            skills.Add(new AgentSkill
            {
                Id = tool.Name,
                Name = tool.Name,
                Description = string.IsNullOrEmpty(tool.Description) ? tool.Name : tool.Description,
                Tags = [agentName],
            });
        }

        return skills;
    }

    public static AgentCard Build(
        AIAgent? agent,
        string agentName,
        string description,
        string baseUrl,
        A2ACustomOperationRegistry registry)
    {
        var schemes = new Dictionary<string, SecurityScheme>
        {
            ["agentSecret"] = new SecurityScheme
            {
                ApiKeySecurityScheme = new ApiKeySecurityScheme
                {
                    Description = "Inter-agent shared secret (AUTH_MODE=local).",
                    Location = "header",
                    Name = "X-Agent-Secret",
                },
            },
            ["serviceToken"] = new SecurityScheme
            {
                HttpAuthSecurityScheme = new HttpAuthSecurityScheme
                {
                    Description = "Authorization-server service token, scope agent:invoke (AUTH_MODE=oauth).",
                    Scheme = "Bearer",
                    BearerFormat = "JWT",
                },
            },
        };

        var card = new AgentCard
        {
            Name = agentName,
            Description = string.IsNullOrEmpty(description) ? agentName : description,
            Version = A2AWire.AgentVersion,
            SupportedInterfaces =
            [
                new AgentInterface
                {
                    Url = $"{baseUrl.TrimEnd('/')}{A2AWire.RpcPath}",
                    ProtocolBinding = ProtocolBindingNames.JsonRpc,
                    ProtocolVersion = A2AWire.ProtocolVersion,
                },
            ],
            Capabilities = new AgentCapabilities { Streaming = true, PushNotifications = false },
            SecuritySchemes = schemes,
            // Either credential admits a caller (AgentAuthMiddleware: shared secret in local mode, an
            // AS-issued service token in oauth mode). The end user's identity travels beside it, in
            // X-User-Email / X-User-Role or the signed X-User-Context token; neither is a security scheme.
            SecurityRequirements =
            [
                new SecurityRequirement { Schemes = new Dictionary<string, StringList> { ["agentSecret"] = new StringList() } },
                new SecurityRequirement { Schemes = new Dictionary<string, StringList> { ["serviceToken"] = new StringList() } },
            ],
            DefaultInputModes = ["text/plain"],
            DefaultOutputModes = ["text/plain"],
            Skills = Skills(agent, agentName).ToList(),
        };

        // Declares the three extensions (and fails loudly if the registry and the card ever disagree).
        return registry.PrepareAgentCard(card);
    }

    /// <summary>The card as the JSON a client resolves it from.</summary>
    public static string ToJson(AgentCard card) =>
        JsonSerializer.Serialize(card, A2AJsonUtilities.DefaultOptions.GetTypeInfo(typeof(AgentCard)));
}
