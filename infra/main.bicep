targetScope = 'subscription'

@minLength(1)
@maxLength(32)
@description('azd environment name; names the resource group and seeds unique names.')
param environmentName string
@description('Primary region.')
param location string
@description('Region for the Azure OpenAI account. Defaults to location.')
param openAiLocation string = location
@description('Deploying user object id (AZURE_PRINCIPAL_ID); empty in CI.')
param principalId string = ''
@allowed(['demo', 'reference'])
param profile string = 'demo'
param chatModelName string = 'gpt-5.1'
param chatModelVersion string = '2025-11-13'
param deployMiniModel bool = false

@secure()
param postgresAdminPassword string
@secure()
param jwtSecret string
@secure()
param agentSharedSecret string

var profiles = {
  demo: loadJsonContent('profiles/demo.json')
  reference: loadJsonContent('profiles/reference.json')
}
var cfg = profiles[profile]
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  profile: profile
}

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  name: 'monitoring'
  params: {
    logAnalyticsName: 'log-${resourceToken}'
    appInsightsName: 'appi-${resourceToken}'
    location: location
    tags: tags
    dailyQuotaGb: cfg.logAnalytics.dailyQuotaGb
    retentionDays: cfg.logAnalytics.retentionDays
  }
}

module identity 'modules/identity.bicep' = {
  scope: rg
  name: 'identity'
  params: {
    name: 'id-${resourceToken}'
    location: location
    tags: tags
  }
}

module keyVault 'modules/keyvault.bicep' = {
  scope: rg
  name: 'keyvault'
  params: {
    name: 'kv-${resourceToken}'
    location: location
    tags: tags
    readerPrincipalId: identity.outputs.principalId
    deployerPrincipalId: principalId
    enablePurgeProtection: cfg.keyVault.enablePurgeProtection
    softDeleteRetentionDays: cfg.keyVault.softDeleteRetentionDays
    postgresAdminPassword: postgresAdminPassword
    jwtSecret: jwtSecret
    agentSharedSecret: agentSharedSecret
  }
}

module postgres 'modules/postgres.bicep' = {
  scope: rg
  name: 'postgres'
  params: {
    name: 'psql-${resourceToken}'
    location: location
    tags: tags
    administratorPassword: postgresAdminPassword
    tier: cfg.postgres.tier
    skuName: cfg.postgres.skuName
    storageSizeGB: cfg.postgres.storageSizeGB
    highAvailability: cfg.postgres.highAvailability
    backupRetentionDays: cfg.postgres.backupRetentionDays
    geoRedundantBackup: cfg.postgres.geoRedundantBackup
    allowAzureServices: cfg.postgres.allowAzureServices
  }
}

module openAi 'modules/openai.bicep' = {
  scope: rg
  name: 'openai'
  params: {
    name: 'ai-${resourceToken}'
    location: openAiLocation
    tags: tags
    callerPrincipalId: identity.outputs.principalId
    chatModelName: chatModelName
    chatModelVersion: chatModelVersion
    chatCapacity: cfg.openAi.chatCapacity
    embeddingCapacity: cfg.openAi.embeddingCapacity
    deployMiniModel: deployMiniModel
    miniCapacity: cfg.openAi.miniCapacity
  }
}

module budget 'modules/budget.bicep' = {
  scope: rg
  name: 'budget'
  params: {
    name: 'budget-${environmentName}'
    amount: cfg.budget.monthlyAmount
    alertThresholdPercent: cfg.budget.alertThresholdPercent
  }
}

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_USER_ASSIGNED_IDENTITY_ID string = identity.outputs.id
output AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID string = identity.outputs.clientId
output AZURE_KEY_VAULT_NAME string = keyVault.outputs.name
output AZURE_KEY_VAULT_ENDPOINT string = keyVault.outputs.uri
output AZURE_LOG_ANALYTICS_NAME string = monitoring.outputs.logAnalyticsName
output AZURE_APP_INSIGHTS_NAME string = monitoring.outputs.appInsightsName
output POSTGRES_HOST string = postgres.outputs.fqdn
output POSTGRES_DB string = postgres.outputs.databaseName
output POSTGRES_USER string = postgres.outputs.administratorLogin
output AZURE_OPENAI_ENDPOINT string = openAi.outputs.endpoint
output AZURE_OPENAI_CHAT_DEPLOYMENT string = openAi.outputs.chatDeploymentName
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT string = openAi.outputs.embeddingDeploymentName
