// AZ-5: an API Management gateway in front of the AIServices account.
//
// Only deployed with `deployGateway`. The apps keep `LLM_PROVIDER=azure`; only
// AZURE_OPENAI_ENDPOINT changes (main.bicep points it at `gatewayEndpoint`).
//
// What it does, in gateway-policy.xml:
//   - per-agent token limits (`azure-openai-token-limit`), keyed on the agent's name;
//   - per-agent token metrics to Application Insights (`azure-openai-emit-token-metric`);
//   - calls the account with the gateway's own managed identity; the caller's key is dropped;
//   - the agent name is the `x-agent-name` header (added by the apps' model client), upgraded to
//     a trusted name when the caller's Entra token belongs to one of the per-app identities.
//
// UNVERIFIED, needs a real subscription before anyone relies on it (offline only compiles it):
//   1. Which API Management tiers support `azure-openai-token-limit` and
//      `azure-openai-emit-token-metric`. Microsoft's policy pages list the tiers; they were not
//      readable offline. `skuName` therefore defaults to BasicV2 (a v2 tier: provisions in
//      minutes, not the 30 to 45 minutes classic tiers take, and costs less than Standard/Premium)
//      and is a parameter. Consumption is deliberately not offered. If the PUT of the policy is
//      refused, raise the tier and record the answer in .claude/plans/remaining-work.md (M6.2).
//   2. That `tokens-per-minute` and the counter key accept the values built here (literals per
//      agent, no expressions in the limit itself).
//   3. That the policy expressions compile in the gateway: `(Jwt)context.Variables[...]`,
//      `Jwt.Claims.GetValueOrDefault`, `string.Split`, and double quotes inside an attribute.
//   4. That the metric dimensions arrive in Application Insights (the workspace-based component
//      needs custom metric dimensions enabled to split by Agent).
//
// API version: Microsoft.ApiManagement 2024-05-01, the newest GA the Bicep 0.49 type set carries
// (the newer ones with types are previews, which use-recent-api-versions does not prefer).
param name string
param location string
param tags object
@allowed(['Developer', 'BasicV2', 'StandardV2', 'Basic', 'Standard', 'Premium'])
@description('UNVERIFIED: tier support for the Azure OpenAI token policies is not confirmed offline. See the header.')
param skuName string = 'BasicV2'
@minValue(1)
param skuCapacity int = 1
@description('APIM requires a publisher email; it receives service notices. Set a real one.')
param publisherEmail string
param publisherName string = 'E-Commerce Agents'

@description('AIServices account the gateway fronts.')
param openAiAccountName string
param appInsightsName string

@allowed(['entra', 'subscription'])
@description('entra: the caller presents an Entra token (the apps identity). subscription: the caller presents an APIM subscription key as api-key, stored in Key Vault.')
param callerAuth string
@description('Client ids of the identities allowed to call (entra mode).')
param allowedCallerClientIds array = []
@description('{ app, principalId } per app identity: the token oid vouches for the agent name (entra mode).')
param agentIdentityMap array = []
@description('Key Vault that receives the subscription key as azure-openai-key (subscription mode).')
param keyVaultName string = ''

param agentNames array
@description('Tokens per minute for an agent without its own entry in agentTokenLimits, and for names that are not listed.')
param defaultTokensPerMinute int = 60000
@description('Per-agent tokens per minute, by agent name.')
param agentTokenLimits object = {}

var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: openAiAccountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource service 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: skuName
    capacity: skuCapacity
  }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

// The gateway calls the models; nothing else. The apps no longer need this role when it is on.
resource gatewayRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, service.id, openAiUserRoleId)
  scope: account
  properties: {
    principalId: service.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
  }
}

resource logger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: service
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsights.id
    credentials: { instrumentationKey: appInsights.properties.InstrumentationKey }
    isBuffered: true
  }
}

// `metrics: true` is what lets azure-openai-emit-token-metric reach Application Insights.
// Bodies are not logged: prompts and answers can hold customer data.
resource diagnostics 'Microsoft.ApiManagement/service/diagnostics@2024-05-01' = {
  parent: service
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    metrics: true
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    verbosity: 'information'
  }
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: service
  name: 'azure-openai'
  properties: {
    displayName: 'Azure OpenAI'
    path: 'openai'
    protocols: ['https']
    serviceUrl: '${account.properties.endpoint}openai'
    subscriptionRequired: callerAuth == 'subscription'
    subscriptionKeyParameterNames: {
      header: 'api-key'
      query: 'subscription-key'
    }
  }
}

resource chatCompletions 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'chat-completions'
  properties: {
    displayName: 'Chat completions'
    method: 'POST'
    urlTemplate: '/deployments/{deployment-id}/chat/completions'
    templateParameters: [{ name: 'deployment-id', required: true, type: 'string' }]
  }
}

resource embeddings 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'embeddings'
  properties: {
    displayName: 'Embeddings'
    method: 'POST'
    urlTemplate: '/deployments/{deployment-id}/embeddings'
    templateParameters: [{ name: 'deployment-id', required: true, type: 'string' }]
  }
}

resource responses 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'responses'
  properties: {
    displayName: 'Responses'
    method: 'POST'
    urlTemplate: '/v1/responses'
  }
}

var callerAuthXml = callerAuth == 'entra'
  ? '<validate-azure-ad-token tenant-id="${tenant().tenantId}" output-token-variable-name="callerJwt" failed-validation-httpcode="401" failed-validation-error-message="Unauthorized"><audiences><audience>https://cognitiveservices.azure.com</audience></audiences><client-application-ids>${join(map(allowedCallerClientIds, id => '<application-id>${id}</application-id>'), '')}</client-application-ids></validate-azure-ad-token>'
  : '<!-- subscription key checked by API Management -->'
// oid=agent pairs, ';' separated: the policy expression avoids generics, which are not safe in an XML attribute.
var oidMapXml = join(map(agentIdentityMap, m => '${m.principalId}=${m.app}'), ';')
var agentLimitsXml = join(
  map(
    agentNames,
    a => '<when condition="@((string)context.Variables["agent"] == "${a}")"><azure-openai-token-limit counter-key="${a}" tokens-per-minute="${agentTokenLimits[?a] ?? defaultTokensPerMinute}" estimate-prompt-tokens="true" remaining-tokens-header-name="x-ratelimit-remaining-tokens" /></when>'
  ),
  '\n      '
)

resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  dependsOn: [diagnostics]
  properties: {
    format: 'rawxml'
    value: replace(
      replace(
        replace(
          replace(loadTextContent('gateway-policy.xml'), '{{CALLER_AUTH}}', callerAuthXml),
          '{{OID_MAP}}',
          oidMapXml
        ),
        '{{AGENT_LIMITS}}',
        agentLimitsXml
      ),
      '{{DEFAULT_TPM}}',
      string(defaultTokensPerMinute)
    )
  }
}

// Subscription mode: one subscription for the apps, its key stored where the apps already read
// the model key from. The key is written through ARM, like the account key today, and so appears
// in the deployment history the same way.
resource appsSubscription 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = if (callerAuth == 'subscription') {
  parent: service
  name: 'agents'
  properties: {
    displayName: 'E-Commerce Agents apps'
    scope: api.id
    state: 'active'
  }
}

resource vault 'Microsoft.KeyVault/vaults@2025-05-01' existing = if (callerAuth == 'subscription') {
  name: keyVaultName
}

resource subscriptionKeyEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = if (callerAuth == 'subscription') {
  parent: vault
  name: 'azure-openai-key'
  properties: { value: appsSubscription!.listSecrets().primaryKey }
}

output name string = service.name
output gatewayEndpoint string = service.properties.gatewayUrl
