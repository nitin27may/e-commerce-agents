@description('Log Analytics workspace name.')
param logAnalyticsName string
@description('Application Insights name.')
param appInsightsName string
param location string
param tags object
@description('Daily ingestion cap in GB. -1 disables the cap.')
param dailyQuotaGb int
param retentionDays int

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2025-07-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: retentionDays
    workspaceCapping: { dailyQuotaGb: dailyQuotaGb }
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

output logAnalyticsId string = logAnalytics.id
output logAnalyticsName string = logAnalytics.name
output appInsightsName string = appInsights.name
