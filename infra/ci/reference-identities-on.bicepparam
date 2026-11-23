// Offline compile fixture for CI. Values are placeholders, never deployed.
// One user-assigned identity per app (attached to the orchestrator, specialists and worker).
using '../main.bicep'

param environmentName = 'ci-ref-ids'
param location = 'eastus2'
param profile = 'reference'
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
