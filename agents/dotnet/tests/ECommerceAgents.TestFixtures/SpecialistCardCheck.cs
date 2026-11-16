using A2A;
using ECommerceAgents.Shared.A2A.Protocol;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;

namespace ECommerceAgents.TestFixtures;

/// <summary>
/// Builds a real specialist's public A2A agent card from its real tool set and checks what the card advertises.
/// The card is unauthenticated, so it must list only tools the least-privileged role (<c>customer</c>) may call: a
/// role-guarded tool's name and description are internal. <c>A2ARoleMetadataTests</c> reads the source for the
/// attribute/guard pairs; this is the behavioural half, per specialist, through <see cref="SpecialistAgentCard.Build"/>.
/// </summary>
public static class SpecialistCardCheck
{
    /// <summary>A pool that never connects (a data source is only opened when a tool runs), so tool objects can be built.</summary>
    public static DatabasePool OfflinePool() =>
        new(new AgentSettings { DatabaseUrl = "Host=localhost;Port=1;Database=offline;Username=u;Password=p" });

    /// <summary>The names of the skills on the card the specialist <paramref name="agentName"/> would serve with these tools.</summary>
    public static IReadOnlyList<string> CardSkillNames(string agentName, IEnumerable<AITool> tools)
    {
        var options = SpecialistAgentFactory.BuildOptions(new AgentSettings(), "test instructions", agentName, tools);
        var agent = new FakeChatClient().AsAIAgent(options);
        var registry = new A2ACustomOperationRegistryBuilder().Build();

        var card = SpecialistAgentCard.Build(agent, agentName, "test", "http://localhost:8081", registry);

        return card.Skills.Select(s => s.Name).ToList();
    }

    /// <summary>
    /// Asserts that every <paramref name="restricted"/> tool is in the agent's tool set (so the check cannot pass
    /// because a tool was renamed away) yet absent from its card, and that at least one customer tool is on it.
    /// </summary>
    public static void AssertCardHidesRestrictedTools(string agentName, IEnumerable<AITool> tools, params string[] restricted)
    {
        var toolList = tools.ToList();
        var toolNames = toolList.OfType<AIFunction>().Select(t => t.Name).ToList();
        var skills = CardSkillNames(agentName, toolList);

        if (restricted.Length > 0)
        {
            toolNames.Should().Contain(restricted, "the restricted tools must really be among the specialist's tools");
            skills.Should().NotContain(restricted, $"the public {agentName} card must not advertise a role-guarded tool");
        }

        skills.Should().NotBeEmpty($"the {agentName} card must list its customer tools");
        skills.Should().OnlyContain(s => toolNames.Contains(s));
        skills.Count.Should().Be(toolNames.Count - restricted.Length, "every tool but the restricted ones is a customer tool");
    }
}
