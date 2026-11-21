using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Tests that set, or assert on defaults read from, the process environment. <c>ScriptedSpecialist.StartAsync(env: ...)</c> sets
/// variables such as <c>A2A_IDENTITY</c> for the whole process while it builds, and <c>AgentSettingsLoader.Load</c> falls back to
/// that environment, so a loader test running at the same moment saw <c>"token"</c> where it expected the default. A collection
/// runs its classes one at a time. Any class that calls <c>ScriptedSpecialist.StartAsync</c> belongs here too, whether or not it
/// passes <c>env:</c>: a class outside the collection runs in parallel with the ones that read the environment.
/// </summary>
[CollectionDefinition(nameof(ProcessEnvironmentCollection))]
public sealed class ProcessEnvironmentCollection;
