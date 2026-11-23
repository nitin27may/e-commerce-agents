// Offline compile fixture for CI. Values are placeholders, never deployed.
// AZ-5: API Management in front of the account, key auth (subscription key in Key Vault).
using '../main.bicep'

param environmentName = 'ci-gateway'
param location = 'eastus2'
param profile = 'demo'
param deployGateway = true
param gatewayAgentTokenLimits = { orchestrator: 120000 }
param postgresAdminPassword = 'ci-placeholder'
param jwtSecret = 'ci-placeholder'
param agentSharedSecret = 'ci-placeholder'
param imageTag = 'v0.0.0-ci'
param a2aIdentityKey = 'ci-placeholder'
param oauthSeedKey = 'ci-placeholder'
param authSigningKeyEncryptionKey = 'ci-placeholder'
param seedStaffPassword = 'ci-placeholder'
