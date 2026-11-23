// Secrets derived from other resources, so they cannot be generated up front:
// the database connection string and (key auth only) the Azure OpenAI key.
// Both are written through the ARM control plane and never leave the vault as outputs.
param vaultName string
@secure()
param databaseUrl string
@description('Account whose key1 is stored. Empty skips the secret (identity auth).')
param openAiAccountName string = ''
@description('Azure Managed Redis cluster to store a connection secret for (reference profile). Empty skips it.')
param redisName string = ''
@description('Python stack: the secret is a redis-py URL. dotnet: a StackExchange.Redis connection string.')
@allowed(['python', 'dotnet'])
param stack string = 'python'

resource vault 'Microsoft.KeyVault/vaults@2025-05-01' existing = {
  name: vaultName
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = if (!empty(openAiAccountName)) {
  name: openAiAccountName
}

resource redisCluster 'Microsoft.Cache/redisEnterprise@2025-07-01' existing = if (!empty(redisName)) {
  name: redisName

  resource database 'databases' existing = {
    name: 'default'
  }
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

// The access key is base64 and can contain / + =, so the URL form encodes it.
var redisHost = !empty(redisName) ? redisCluster!.properties.hostName : ''
var redisKey = !empty(redisName) ? redisCluster::database!.listKeys().primaryKey : ''

resource redisUrlEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = if (!empty(redisName) && stack == 'python') {
  parent: vault
  name: 'redis-url'
  properties: { value: 'rediss://:${uriComponent(redisKey)}@${redisHost}:10000/0' }
}

resource redisConnectionEntry 'Microsoft.KeyVault/vaults/secrets@2025-05-01' = if (!empty(redisName) && stack == 'dotnet') {
  parent: vault
  name: 'redis-connection-string'
  properties: { value: '${redisHost}:10000,password=${redisKey},ssl=True,abortConnect=False' }
}
