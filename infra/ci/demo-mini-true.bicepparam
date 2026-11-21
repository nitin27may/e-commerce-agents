// Offline compile fixture for CI. Values are placeholders, never deployed.
using '../main.bicep'

param environmentName = 'ci-demo'
param location = 'eastus2'
param profile = 'demo'
param deployMiniModel = true
param postgresAdminPassword = 'ci-placeholder'
param jwtSecret = 'ci-placeholder'
param agentSharedSecret = 'ci-placeholder'
