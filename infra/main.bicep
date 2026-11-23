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
@description('Azure OpenAI API version the apps call. gpt-5.1 tool calling on the Responses API is unverified; see docs/azure-deployment.md.')
param openAiApiVersion string = '2025-04-01-preview'
@allowed(['key', 'identity'])
@description('key: apps read the account key from Key Vault. identity: Entra ID through the user-assigned identity (needs Azure P3 in the image) and local auth is disabled on the account.')
param openAiAuth string = 'key'

@description('GHCR repository prefix the images are pulled from.')
param imageRegistry string = 'ghcr.io/nitin27may/e-commerce-agents'
@minLength(1)
@description('Pinned image tag (release or sha-<7>). No default on purpose.')
param imageTag string

@allowed(['restricted', 'open'])
@description('restricted (default): the frontend accepts only the deployer IP and ALLOWED_IPS. open: anyone, an open spend tap.')
param publicAccess string = 'restricted'
@description('Deployer public IPv4 (set by preprovision). Allowed through the frontend ingress when restricted.')
param deployerIp string = ''
@description('Extra CIDR ranges, comma separated, allowed through the frontend ingress when restricted.')
param allowedIps string = ''
param deployAuthServer bool = false
param deployMcp bool = false

// ---- Foundry and gateway switches (infra-foundry-project-and-gateway lane) ----
@description('AZ-2: create a Foundry project under the AIServices account, connect App Insights to it, grant the app identity Azure AI User.')
param deployFoundryProject bool = false
@description('Per-app identities: one user-assigned identity per app (modules/app-identities.bicep). Created but NOT attached to the apps; wiring is in apps.bicep and documented in docs/azure-deployment.md.')
param deployPerAppIdentities bool = false
@description('AZ-5: API Management in front of the model account; the apps call it instead. Provisioning takes minutes (v2 tiers) to 45 minutes (classic). See docs/azure-deployment.md.')
param deployGateway bool = false
@allowed(['Developer', 'BasicV2', 'StandardV2', 'Basic', 'Standard', 'Premium'])
@description('UNVERIFIED which tiers support the Azure OpenAI token policies; see modules/gateway-apim.bicep.')
param gatewaySku string = 'BasicV2'
@description('APIM publisher email (service notices). Set a real one for a lasting gateway.')
param gatewayPublisherEmail string = 'noreply@example.com'
@description('Tokens per minute for an agent without its own entry in gatewayAgentTokenLimits.')
param gatewayDefaultTokensPerMinute int = 60000
@description('Per-agent tokens per minute by agent name, e.g. { orchestrator: 120000 }.')
param gatewayAgentTokenLimits object = {}

@secure()
param postgresAdminPassword string
@secure()
param jwtSecret string
@secure()
param agentSharedSecret string
@secure()
param a2aIdentityKey string
@secure()
param oauthSeedKey string
@secure()
param authSigningKeyEncryptionKey string
@secure()
param seedStaffPassword string

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
    a2aIdentityKey: a2aIdentityKey
    oauthSeedKey: oauthSeedKey
    authSigningKeyEncryptionKey: authSigningKeyEncryptionKey
    seedStaffPassword: seedStaffPassword
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
    disableLocalAuth: openAiAuth == 'identity'
    allowProjectManagement: deployFoundryProject
  }
}

// Secrets that only exist once Postgres and the OpenAI account do.
module derivedSecrets 'modules/keyvault-secrets.bicep' = {
  scope: rg
  name: 'keyvault-secrets'
  params: {
    vaultName: keyVault.outputs.name
    databaseUrl: 'postgresql://${postgres.outputs.administratorLogin}:${postgresAdminPassword}@${postgres.outputs.fqdn}:5432/${postgres.outputs.databaseName}?sslmode=require'
    // With the gateway on, the secret holds the gateway subscription key (written by the gateway module).
    openAiAccountName: (openAiAuth == 'key' && !deployGateway) ? openAi.outputs.name : ''
  }
}

module cae 'modules/cae.bicep' = {
  scope: rg
  name: 'cae'
  params: {
    name: 'cae-${resourceToken}'
    location: location
    tags: tags
    logAnalyticsName: monitoring.outputs.logAnalyticsName
    appInsightsName: monitoring.outputs.appInsightsName
  }
}

var frontendAllowedIps = concat(
  empty(deployerIp) ? [] : ['${deployerIp}/32'],
  empty(allowedIps) ? [] : split(replace(allowedIps, ' ', ''), ',')
)

module apps 'modules/apps.bicep' = {
  scope: rg
  name: 'apps'
  dependsOn: [derivedSecrets]
  params: {
    location: location
    tags: tags
    environmentId: cae.outputs.id
    defaultDomain: cae.outputs.defaultDomain
    identityId: identity.outputs.id
    identityClientId: identity.outputs.clientId
    keyVaultUri: keyVault.outputs.uri
    imageRegistry: imageRegistry
    imageTag: imageTag
    openAiEndpoint: deployGateway ? gateway!.outputs.gatewayEndpoint : openAi.outputs.endpoint
    chatDeployment: openAi.outputs.chatDeploymentName
    embeddingDeployment: openAi.outputs.embeddingDeploymentName
    chatModelName: chatModelName
    openAiApiVersion: openAiApiVersion
    openAiAuth: openAiAuth
    scale: cfg.containerApps
    publicAccess: publicAccess
    allowedIpRanges: frontendAllowedIps
    deployAuthServer: deployAuthServer
    deployMcp: deployMcp
  }
}

// ---- Foundry project (AZ-2). Appended section; see modules/foundry-project.bicep ----
module foundryProject 'modules/foundry-project.bicep' = if (deployFoundryProject) {
  scope: rg
  name: 'foundry-project'
  params: {
    accountName: openAi.outputs.name
    projectName: 'proj-${resourceToken}'
    location: openAiLocation
    tags: tags
    appInsightsName: monitoring.outputs.appInsightsName
    callerPrincipalId: identity.outputs.principalId
  }
}

// ---- Per-app identities. Appended section; wiring into apps.bicep is separate ----
var appIdentityNames = [
  'orchestrator'
  'product-discovery'
  'order-management'
  'pricing-promotions'
  'review-sentiment'
  'inventory-fulfillment'
  'outbox-worker'
]

module appIdentities 'modules/app-identities.bicep' = if (deployPerAppIdentities) {
  scope: rg
  name: 'app-identities'
  params: {
    namePrefix: 'id-${resourceToken}'
    location: location
    tags: tags
    appNames: appIdentityNames
    keyVaultName: keyVault.outputs.name
    openAiAccountName: openAi.outputs.name
  }
}

// ---- AI gateway (AZ-5). Appended section; see modules/gateway-apim.bicep ----
module gateway 'modules/gateway-apim.bicep' = if (deployGateway) {
  scope: rg
  name: 'gateway'
  params: {
    name: 'apim-${resourceToken}'
    location: location
    tags: tags
    skuName: gatewaySku
    publisherEmail: gatewayPublisherEmail
    openAiAccountName: openAi.outputs.name
    appInsightsName: monitoring.outputs.appInsightsName
    callerAuth: openAiAuth == 'identity' ? 'entra' : 'subscription'
    // The shared identity stays allowed until the apps use their own (header-asserted, untrusted).
    allowedCallerClientIds: concat(
      [identity.outputs.clientId],
      deployPerAppIdentities ? map(appIdentities!.outputs.identities, i => i.clientId) : []
    )
    agentIdentityMap: deployPerAppIdentities
      ? map(appIdentities!.outputs.identities, i => { app: i.app, principalId: i.principalId })
      : []
    keyVaultName: keyVault.outputs.name
    agentNames: appIdentityNames
    defaultTokensPerMinute: gatewayDefaultTokensPerMinute
    agentTokenLimits: gatewayAgentTokenLimits
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
output AZURE_CHAT_DEPLOYMENT string = openAi.outputs.chatDeploymentName
output AZURE_POSTGRES_FQDN string = postgres.outputs.fqdn
output APPLICATIONINSIGHTS_NAME string = monitoring.outputs.appInsightsName
output AZURE_AI_PROJECT_NAME string = deployFoundryProject ? foundryProject!.outputs.name : ''
output AZURE_AI_PROJECT_ENDPOINT string = deployFoundryProject ? foundryProject!.outputs.endpoint : ''
output AZURE_GATEWAY_ENDPOINT string = deployGateway ? gateway!.outputs.gatewayEndpoint : ''
output AZURE_APP_IDENTITIES array = deployPerAppIdentities ? appIdentities!.outputs.identities : []
output AZURE_CONTAINER_APPS_ENVIRONMENT string = cae.outputs.name
output FRONTEND_URL string = apps.outputs.frontendUrl
output ORCHESTRATOR_INTERNAL_URL string = apps.outputs.orchestratorInternalUrl
output DB_INIT_JOB_NAME string = apps.outputs.dbInitJobName
output ADMIN_LOGIN_EMAIL string = 'admin.demo@gmail.com'
output SUPPORT_LOGIN_EMAIL string = 'support.demo@gmail.com'
