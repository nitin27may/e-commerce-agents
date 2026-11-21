// Offline compile fixture for CI. Values are placeholders, never deployed.
// Every opt-in switched on: auth server, MCP servers, open ingress, Entra auth to the model.
using '../main.bicep'

param environmentName = 'ci-optional'
param location = 'eastus2'
param profile = 'demo'
param deployMiniModel = true
param deployAuthServer = true
param deployMcp = true
param publicAccess = 'open'
param openAiAuth = 'identity'
param allowedIps = '198.51.100.0/24, 203.0.113.0/24'
param postgresAdminPassword = 'ci-placeholder'
param jwtSecret = 'ci-placeholder'
param agentSharedSecret = 'ci-placeholder'
param imageTag = 'v0.0.0-ci'
param a2aIdentityKey = 'ci-placeholder'
param oauthSeedKey = 'ci-placeholder'
param authSigningKeyEncryptionKey = 'ci-placeholder'
param seedStaffPassword = 'ci-placeholder'
