// Offline compile fixture for CI. Values are placeholders, never deployed.
// Free-trial profile, no Azure OpenAI account, an operator-supplied OpenAI-compatible endpoint.
using '../main.bicep'

param environmentName = 'ci-freetrial-byo'
param location = 'eastus2'
param profile = 'small'
param deployOpenAi = false
param externalLlmProvider = 'openai'
param externalLlmBaseUrl = 'https://llm.example.invalid/v1'
param externalLlmApiKey = 'ci-placeholder'
param postgresAdminPassword = 'ci-placeholder'
param jwtSecret = 'ci-placeholder'
param agentSharedSecret = 'ci-placeholder'
param deployerIp = '203.0.113.10'
param imageTag = 'v0.0.0-ci'
param a2aIdentityKey = 'ci-placeholder'
param oauthSeedKey = 'ci-placeholder'
param authSigningKeyEncryptionKey = 'ci-placeholder'
param seedStaffPassword = 'ci-placeholder'
