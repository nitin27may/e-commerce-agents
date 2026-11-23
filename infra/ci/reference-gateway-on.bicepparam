// Offline compile fixture for CI. Values are placeholders, never deployed.
// Everything on: gateway with Entra caller validation, Foundry project, one identity per app.
using '../main.bicep'

param environmentName = 'ci-ref-gateway'
param location = 'eastus2'
param profile = 'reference'
param openAiAuth = 'identity'
param deployGateway = true
param gatewaySku = 'StandardV2'
param deployFoundryProject = true
param deployPerAppIdentities = true
param deployerIp = '203.0.113.10'
param postgresAdminPassword = 'ci-placeholder'
param jwtSecret = 'ci-placeholder'
param agentSharedSecret = 'ci-placeholder'
param imageTag = 'v0.0.0-ci'
param a2aIdentityKey = 'ci-placeholder'
param oauthSeedKey = 'ci-placeholder'
param authSigningKeyEncryptionKey = 'ci-placeholder'
param seedStaffPassword = 'ci-placeholder'
