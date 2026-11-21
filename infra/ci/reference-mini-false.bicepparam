// Offline compile fixture for CI. Values are placeholders, never deployed.
using '../main.bicep'

param environmentName = 'ci-reference'
param location = 'eastus2'
param profile = 'reference'
param deployMiniModel = false
param postgresAdminPassword = 'ci-placeholder'
param jwtSecret = 'ci-placeholder'
param agentSharedSecret = 'ci-placeholder'
