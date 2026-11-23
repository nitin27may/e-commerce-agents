// Offline compile fixture for CI. Values are placeholders, never deployed.
// The reference profile with its defaults (VNet, private endpoints, managed Redis, ACR, identity auth,
// A2A token mode) plus the auth server and both MCP servers.
using '../main.bicep'

param environmentName = 'ci-ref'
param location = 'eastus2'
param profile = 'reference'
param deployAuthServer = true
param deployMcp = true
param postgresAdminPassword = 'ci-placeholder'
param jwtSecret = 'ci-placeholder'
param agentSharedSecret = 'ci-placeholder'
param deployerIp = '203.0.113.10'
param imageTag = 'v0.0.0-ci'
param a2aIdentityKey = 'ci-placeholder'
param oauthSeedKey = 'ci-placeholder'
param authSigningKeyEncryptionKey = 'ci-placeholder'
param seedStaffPassword = 'ci-placeholder'
