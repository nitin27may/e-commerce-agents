@description('Container Apps environment name.')
param name string
param location string
param tags object
@description('Log Analytics workspace that receives console and system logs.')
param logAnalyticsName string
@description('Application Insights component that receives the managed OpenTelemetry traces and logs.')
param appInsightsName string

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2025-07-01' existing = {
  name: logAnalyticsName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

// Consumption-only workload profile. The managed OpenTelemetry agent injects
// OTEL_EXPORTER_OTLP_ENDPOINT into every container, which is why no app sets that
// variable itself (a value of ours would point the SDK away from the agent).
// Traces and logs reach Application Insights; metrics do not (documented limitation).
// Preview API on purpose: appInsightsConfiguration and openTelemetryConfiguration are not in a GA version yet.
#disable-next-line use-recent-api-versions
resource environment 'Microsoft.App/managedEnvironments@2025-02-02-preview' = {
  name: name
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    appInsightsConfiguration: {
      connectionString: appInsights.properties.ConnectionString
    }
    openTelemetryConfiguration: {
      tracesConfiguration: { destinations: ['appInsights'] }
      logsConfiguration: { destinations: ['appInsights'] }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

output id string = environment.id
output name string = environment.name
@description('Apps are reachable at <app>.<defaultDomain> (external) or <app>.internal.<defaultDomain>.')
output defaultDomain string = environment.properties.defaultDomain
