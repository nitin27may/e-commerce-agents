// Offline compile fixture for CI. Values are placeholders, never deployed.
// The reference profile on the .NET stack: managed Redis reaches .NET as a StackExchange.Redis connection string.
using '../main.bicep'

param environmentName = 'ci-ref-net'
param location = 'eastus2'
param profile = 'reference'
param stack = 'dotnet'
param postgresAdminPassword = 'ci-placeholder'
param jwtSecret = 'ci-placeholder'
param agentSharedSecret = 'ci-placeholder'
param deployerIp = '203.0.113.10'
param imageTag = 'v0.0.0-ci'
param a2aIdentityKey = 'ci-placeholder'
param oauthSeedKey = 'ci-placeholder'
param authSigningKeyEncryptionKey = 'ci-placeholder'
param seedStaffPassword = 'ci-placeholder'
