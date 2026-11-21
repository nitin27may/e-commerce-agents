using Azure.Core;
using Azure.Identity;
using ECommerceAgents.Shared.Configuration;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// Microsoft Entra credentials for Azure OpenAI (<c>AZURE_OPENAI_AUTH=identity</c>). Python twin:
/// <c>shared/azure_auth.py</c>.
/// </summary>
public static class AzureIdentity
{
    public static bool IsIdentity(AgentSettings settings) =>
        string.Equals(settings.AzureOpenAiAuth, "identity", StringComparison.OrdinalIgnoreCase);

    /// <summary>
    /// The user-assigned managed identity when <c>AZURE_CLIENT_ID</c> is set, else <see cref="DefaultAzureCredential"/>.
    /// The SDK caches tokens, so one credential per client is enough.
    /// </summary>
    public static TokenCredential CreateCredential(AgentSettings settings) =>
        string.IsNullOrWhiteSpace(settings.AzureClientId)
            ? new DefaultAzureCredential()
            : new ManagedIdentityCredential(
                ManagedIdentityId.FromUserAssignedClientId(settings.AzureClientId.Trim()));
}
