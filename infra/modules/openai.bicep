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
param deployMiniModel bool = false
param miniModelName string = 'gpt-5.1-mini'
param miniModelVersion string = '2025-11-13'
param miniCapacity int = 50

var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

// Deployments on one account are created one at a time, hence the dependsOn chain.
resource chat 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'chat'
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
  name: 'embedding'
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

output name string = account.name
output endpoint string = account.properties.endpoint
output chatDeploymentName string = chat.name
output embeddingDeploymentName string = embedding.name
