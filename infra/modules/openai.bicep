param name string
param location string
param tags object
@description('Principal that calls the models with Entra ID (the user-assigned identity).')
param callerPrincipalId string
param chatModelName string
param chatModelVersion string
param chatCapacity int
param embeddingModelName string = 'text-embedding-3-small'
param embeddingModelVersion string = '1'
param embeddingCapacity int
@allowed(['GlobalStandard', 'Standard', 'DataZoneStandard'])
param embeddingSkuName string = 'GlobalStandard'
@description('False while the apps authenticate with the account key (Azure P3 not merged); true once AZURE_OPENAI_AUTH=identity is the default.')
param disableLocalAuth bool = false
@description('AZ-2: lets Foundry projects be created under this account. Adds a system-assigned identity. Off leaves the account exactly as it was.')
param allowProjectManagement bool = false
@description('Reference profile: public network access off, reached through a private endpoint.')
param privateOnly bool = false
param deployMiniModel bool = false
param miniModelName string = 'gpt-5.1-mini'
param miniModelVersion string = '2025-11-13'
param miniCapacity int = 50

@description('The person deploying (azd AZURE_PRINCIPAL_ID). Gets Cognitive Services OpenAI User so scripts/azure/sanity.sh can call the model with its own Entra token instead of an account key. Empty skips it.')
param deployerPrincipalId string = ''

var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: allowProjectManagement ? { type: 'SystemAssigned' } : null
  properties: {
    customSubDomainName: name
    publicNetworkAccess: privateOnly ? 'Disabled' : 'Enabled'
    networkAcls: privateOnly ? { defaultAction: 'Deny' } : null
    disableLocalAuth: disableLocalAuth
    allowProjectManagement: allowProjectManagement ? true : null
  }
}

// Deployments on one account are created one at a time, hence the dependsOn chain.
resource chat 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  // Named after the model, not 'chat': the apps price a turn by the deployment name the API reports, and the pricing file is
  // keyed by model name. A deployment called 'chat' has no price, and COST_BUDGET_MODE=enforce stops every run on it.
  name: chatModelName
  sku: {
    name: 'GlobalStandard'
    capacity: chatCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
      version: chatModelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: embeddingModelName
  sku: {
    name: embeddingSkuName
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: embeddingModelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
  dependsOn: [chat]
}

resource mini 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (deployMiniModel) {
  parent: account
  name: 'chat-mini'
  sku: {
    name: 'GlobalStandard'
    capacity: miniCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: miniModelName
      version: miniModelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
  dependsOn: [embedding]
}

resource callerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, callerPrincipalId, openAiUserRoleId)
  scope: account
  properties: {
    principalId: callerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
  }
}

resource deployerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(account.id, deployerPrincipalId, openAiUserRoleId)
  scope: account
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
  }
}

output id string = account.id
output name string = account.name
output endpoint string = account.properties.endpoint
output chatDeploymentName string = chat.name
output embeddingDeploymentName string = embedding.name
