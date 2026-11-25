// One user-assigned identity per app, so an app's identity is a fact the platform vouches for
// (the Entra token's oid) and not a header the caller wrote. The API Management gateway
// (gateway-apim.bicep) maps that oid to the agent name for per-agent limits and metrics.
//
// Only deployed with `deployPerAppIdentities`. This module creates the identities and their
// roles; it does NOT attach them to the Container Apps. Wiring is a change to apps.bicep and
// container-app.bicep (each app takes its own identity id instead of the shared one); see
// "Per-app identities" in docs/azure-deployment.md for the exact change. Until it is made the
// identities exist and are unused, and the gateway treats the shared identity's calls as
// header-asserted (metric dimension AgentTrusted=false).
param namePrefix string
param location string
param tags object
@description('App names, e.g. orchestrator, product-discovery. One identity each.')
param appNames array
param keyVaultName string
@description('Azure OpenAI account the apps call directly. Empty when a gateway sits in front (the gateway holds that role).')
param openAiAccountName string = ''

var secretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource vault 'Microsoft.KeyVault/vaults@2025-05-01' existing = {
  name: keyVaultName
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = if (!empty(openAiAccountName)) {
  name: openAiAccountName
}

resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = [
  for app in appNames: {
    name: '${namePrefix}-${app}'
    location: location
    tags: union(tags, { app: app })
  }
]

// Each identity resolves Key Vault references: Secrets User, the same role the shared identity has.
resource vaultRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (app, i) in appNames: {
    name: guid(vault.id, identities[i].id, secretsUserRoleId)
    scope: vault
    properties: {
      principalId: identities[i].properties.principalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRoleId)
    }
  }
]

// Direct model access only when there is no gateway.
resource modelRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (app, i) in appNames: if (!empty(openAiAccountName)) {
    name: guid(account.id, identities[i].id, openAiUserRoleId)
    scope: account
    properties: {
      principalId: identities[i].properties.principalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
    }
  }
]

output identities array = [
  for (app, i) in appNames: {
    app: app
    id: identities[i].id
    clientId: identities[i].properties.clientId
    principalId: identities[i].properties.principalId
  }
]
