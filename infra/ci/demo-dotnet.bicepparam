// Offline compile fixture for CI. Values are placeholders, never deployed.
// The demo profile on the .NET stack, with the MCP servers on (only the inventory host exists on .NET).
using '../main.bicep'

param environmentName = 'ci-demo-net'
param location = 'eastus2'
param profile = 'demo'
param stack = 'dotnet'
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
