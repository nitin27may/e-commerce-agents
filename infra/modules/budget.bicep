targetScope = 'resourceGroup'

param name string
@description('Monthly budget in the billing currency.')
param amount int
@minValue(1)
@maxValue(100)
param alertThresholdPercent int
@description('More actual-cost thresholds (percent), each its own notification. The small profile alerts at 50, 80 and 100.')
param additionalActualThresholds array = []
@description('First day of the budget month. Must not be in the past on create.')
param startDate string = utcNow('yyyy-MM-01')

var extraActual = toObject(additionalActualThresholds, t => 'actual_${t}', t => {
  enabled: true
  operator: 'GreaterThan'
  threshold: t
  thresholdType: 'Actual'
  contactEmails: []
  contactRoles: ['Owner', 'Contributor']
})

// Notifications go to subscription role holders, so no email address lives in the repo.
resource budget 'Microsoft.Consumption/budgets@2026-06-01' = {
  name: name
  properties: {
    category: 'Cost'
    amount: amount
    timeGrain: 'Monthly'
    timePeriod: { startDate: startDate }
    notifications: union({
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
    }, extraActual)
  }
}

output name string = budget.name
