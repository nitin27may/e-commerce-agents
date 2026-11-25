@description('Vault name, 3-24 chars, globally unique.')
param name string
param location string
param tags object
@description('Principal that reads secrets at runtime (the user-assigned identity).')
param readerPrincipalId string
@description('Deploying user or pipeline; gets Secrets Officer. Empty to skip.')
param deployerPrincipalId string = ''
param enablePurgeProtection bool
@description('Reference profile: public network access off and the default firewall action Deny (trusted Azure services bypass). Reached through a private endpoint; secrets are still written through ARM.')
param privateOnly bool = false
@minValue(7)
@maxValue(90)
param softDeleteRetentionDays int

@secure()
param postgresAdminPassword string
@secure()
param jwtSecret string
@secure()
param agentSharedSecret string
@secure()
param a2aIdentityKey string
@secure()
param oauthSeedKey string
@secure()
param authSigningKeyEncryptionKey string
@secure()
param seedStaffPassword string
@secure()
@description('Operator-supplied API key for an OpenAI-compatible endpoint (small path).')
param llmApiKey string = ''
@description('Write the postgres-admin-password secret. Nothing reads it (the apps use database-url, which carries the password); the small profile turns it off so the password exists in one secret only.')
param storePostgresPassword bool = true
@description('Write a2a-identity-key, oauth-seed-key and auth-signing-key-encryption-key. The first is optional outside token mode (the key falls back to the agent secret) and the other two are read only with AUTH_MODE=oauth; the small profile turns them off unless the auth server is deployed.')
param storeAuthSecrets bool = true
@description('Write the llm-api-key secret. A plain flag, not !empty(llmApiKey): a secure value is not known when the template is evaluated offline.')
param storeLlmApiKey bool = false

var secretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var secretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource vault 'Microsoft.KeyVault/vaults@2025-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionDays
    // Once true it can never be set back to false, so the demo profile omits it.
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: privateOnly ? 'Disabled' : 'Enabled'
    networkAcls: privateOnly ? { defaultAction: 'Deny', bypass: 'AzureServices' } : null
  }
}

resource readerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, readerPrincipalId, secretsUserRoleId)
  scope: vault
  properties: {
    principalId: readerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRoleId)
  }
}

resource deployerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(vault.id, deployerPrincipalId, secretsOfficerRoleId)
  scope: vault
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsOfficerRoleId)
  }
}

// Written through the ARM control plane, so the deployment itself needs no
// data-plane role.
resource pgPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = if (storePostgresPassword) {
  parent: vault
  name: 'postgres-admin-password'
  properties: { value: postgresAdminPassword }
}

resource jwtSecretEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = {
  parent: vault
  name: 'jwt-secret'
  properties: { value: jwtSecret }
}

resource agentSecretEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = {
  parent: vault
  name: 'agent-shared-secret'
  properties: { value: agentSharedSecret }
}

resource a2aKeyEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = if (storeAuthSecrets) {
  parent: vault
  name: 'a2a-identity-key'
  properties: { value: a2aIdentityKey }
}

resource oauthSeedEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = if (storeAuthSecrets) {
  parent: vault
  name: 'oauth-seed-key'
  properties: { value: oauthSeedKey }
}

resource signingKeyEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = if (storeAuthSecrets) {
  parent: vault
  name: 'auth-signing-key-encryption-key'
  properties: { value: authSigningKeyEncryptionKey }
}

resource staffPasswordEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = {
  parent: vault
  name: 'seed-staff-password'
  properties: { value: seedStaffPassword }
}

resource llmApiKeyEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = if (storeLlmApiKey) {
  parent: vault
  name: 'llm-api-key'
  properties: { value: llmApiKey }
}

output id string = vault.id
output name string = vault.name
output uri string = vault.properties.vaultUri
