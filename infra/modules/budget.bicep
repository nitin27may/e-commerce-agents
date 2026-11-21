targetScope = 'resourceGroup'

param name string
@description('Monthly budget in the billing currency.')
param amount int
@minValue(1)
@maxValue(100)
param alertThresholdPercent int
@description('First day of the budget month. Must not be in the past on create.')
param startDate string = utcNow('yyyy-MM-01')

// Notifications go to subscription role holders, so no email address lives in the repo.
resource budget 'Microsoft.Consumption/budgets@2026-06-01' = {
  name: name
  properties: {
    category: 'Cost'
    amount: amount
    timeGrain: 'Monthly'
    timePeriod: { startDate: startDate }
    notifications: {
      actual: {
        enabled: true
        operator: 'GreaterThan'
        threshold: alertThresholdPercent
        thresholdType: 'Actual'
        contactEmails: []
        contactRoles: ['Owner', 'Contributor']
      }
      forecast: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: []
        contactRoles: ['Owner']
      }
    }
  }
}

output name string = budget.name
