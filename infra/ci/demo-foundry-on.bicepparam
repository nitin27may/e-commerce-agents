// Offline compile fixture for CI. Values are placeholders, never deployed.
// AZ-2: the Foundry project, its App Insights connection and the project role.
using '../main.bicep'

param environmentName = 'ci-foundry'
param location = 'eastus2'
param profile = 'demo'
param deployFoundryProject = true
param postgresAdminPassword = 'ci-placeholder'
param jwtSecret = 'ci-placeholder'
param agentSharedSecret = 'ci-placeholder'
param imageTag = 'v0.0.0-ci'
param a2aIdentityKey = 'ci-placeholder'
param oauthSeedKey = 'ci-placeholder'
param authSigningKeyEncryptionKey = 'ci-placeholder'
param seedStaffPassword = 'ci-placeholder'
