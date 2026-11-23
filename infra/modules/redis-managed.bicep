// Azure Managed Redis (Microsoft.Cache/redisEnterprise) for the reference profile, replacing the
// redis container. Rate limiting and caching only, and both fail open.
//
// Availability, SKU names and price vary by region and subscription and are NOT verified here:
// confirm at deploy time (the portal or `az redisenterprise`) before relying on the default SKU.
// Balanced_B0 is the smallest Azure Managed Redis size. The database uses the EnterpriseCluster
// policy: one endpoint, so a client that is not cluster-aware works (redis-py from_url,
// StackExchange.Redis). TLS only, on port 10000.
param name string
param location string
param tags object
@description('Azure Managed Redis SKU, e.g. Balanced_B0. Verify availability in the region.')
param skuName string = 'Balanced_B0'
@description('Disabled: reached through the private endpoint only.')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccess string = 'Disabled'

resource cluster 'Microsoft.Cache/redisEnterprise@2025-07-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: skuName }
  properties: {
    minimumTlsVersion: '1.2'
    publicNetworkAccess: publicNetworkAccess
  }
}

resource database 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' = {
  parent: cluster
  name: 'default'
  properties: {
    clientProtocol: 'Encrypted'
    port: 10000
    clusteringPolicy: 'EnterpriseCluster'
    evictionPolicy: 'VolatileLRU'
    accessKeysAuthentication: 'Enabled'
  }
}

output id string = cluster.id
output name string = cluster.name
output hostName string = cluster.properties.hostName
output port int = database.properties.port
