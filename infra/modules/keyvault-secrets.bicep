// Secrets derived from other resources, so they cannot be generated up front:
// the database connection string and (key auth only) the Azure OpenAI key.
// Both are written through the ARM control plane and never leave the vault as outputs.
param vaultName string
@secure()
param databaseUrl string
@description('Account whose key1 is stored. Empty skips the secret (identity auth).')
param openAiAccountName string = ''

resource vault 'Microsoft.KeyVault/vaults@2025-05-01' existing = {
  name: vaultName
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = if (!empty(openAiAccountName)) {
  name: openAiAccountName
}

resource databaseUrlEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = {
  parent: vault
  name: 'database-url'
  properties: { value: databaseUrl }
}

resource openAiKeyEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = if (!empty(openAiAccountName)) {
  parent: vault
  name: 'azure-openai-key'
  properties: { value: account!.listKeys().key1 }
}
