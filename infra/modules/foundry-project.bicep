// AZ-2: a Microsoft Foundry project under the existing AIServices account.
//
// Only deployed with `deployFoundryProject`. The account must have been created with
// `allowProjectManagement` and a system-assigned identity (openai.bicep does that behind the
// same flag); without them the project PUT is refused.
//
// What it gives you: a project endpoint (agents, evaluations and the tracing view live there)
// and the shared App Insights component connected to it, so traces the apps already send to
// Application Insights show up in the project's tracing view.
//
// API versions: Microsoft.CognitiveServices 2026-09-01, the newest version the Bicep 0.49 type
// set carries (probed offline: 2026-10-01 and later have no types; 2026-09-01 lints clean with
// use-recent-api-versions as an error). openai.bicep keeps 2025-06-01 for the account itself;
// both address the same ARM resource. Whether 2026-09-01 is live in the target region is
// unverified (docs/azure-deployment.md, Assumptions): fall back to 2025-06-01 if the PUT is refused.
param accountName string
param projectName string
param location string
param tags object
@description('Existing Application Insights component the project is connected to.')
param appInsightsName string
@description('Principal that calls the project at run time (the apps identity).')
param callerPrincipalId string

// Azure AI User: the data actions a caller needs on a project (run agents, create evaluation
// runs, read project resources) and none of the management ones. Not Azure AI Project Manager
// (which can assign roles) and not Contributor/Owner. The model calls themselves are covered by
// Cognitive Services OpenAI User on the account (openai.bicep). Role id to confirm on a real
// subscription; listed under "Assumptions" in docs/azure-deployment.md.
var azureAiUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

resource account 'Microsoft.CognitiveServices/accounts@2026-09-01' existing = {
  name: accountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2026-09-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: projectName
    description: 'E-Commerce Agents: evaluations and tracing.'
  }
}

// The connection carries the connection string as a credential; it is an input to ARM and never an output.
resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2026-09-01' = {
  parent: project
  name: 'appinsights'
  properties: {
    category: 'AppInsights'
    target: appInsights.id
    authType: 'ApiKey'
    isSharedToAll: false
    credentials: { key: appInsights.properties.ConnectionString }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsights.id
    }
  }
}

resource callerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, callerPrincipalId, azureAiUserRoleId)
  scope: project
  properties: {
    principalId: callerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAiUserRoleId)
  }
}

output name string = project.name
// Foundry project endpoint: https://<account>.services.ai.azure.com/api/projects/<project>
output endpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${project.name}'
output principalId string = project.identity.principalId
