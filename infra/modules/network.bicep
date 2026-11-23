// Reference-profile network: one VNet with a delegated subnet for the Container Apps environment,
// a subnet for private endpoints, and the private DNS zones those endpoints register in.
//
// Sizing: a workload-profiles environment needs a delegated subnet of /27 or larger; the default
// /24 leaves room for replica growth (each replica draws an address). The private endpoint subnet
// holds one address per endpoint (Postgres, Key Vault, three for the AIServices account, Redis).
// Zone names are the public-cloud ones; a sovereign cloud uses different suffixes.
param name string
param location string
param tags object
param addressPrefix string = '10.40.0.0/16'
@description('Delegated to Microsoft.App/environments. /27 is the minimum for a workload-profiles environment.')
param environmentSubnetPrefix string = '10.40.0.0/24'
param privateEndpointSubnetPrefix string = '10.40.1.0/24'

resource vnet 'Microsoft.Network/virtualNetworks@2025-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [addressPrefix] }
    subnets: [
      {
        name: 'snet-containerapps'
        properties: {
          addressPrefix: environmentSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: { serviceName: 'Microsoft.App/environments' }
            }
          ]
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

module zonePostgres 'private-dns-zone.bicep' = {
  name: 'dns-postgres'
  params: { zoneName: 'privatelink.postgres.database.azure.com', vnetId: vnet.id, tags: tags }
}
module zoneVault 'private-dns-zone.bicep' = {
  name: 'dns-vault'
  params: { zoneName: 'privatelink.vaultcore.azure.net', vnetId: vnet.id, tags: tags }
}
// An AIServices account answers on three hostnames (cognitiveservices, openai, services.ai).
module zoneCognitive 'private-dns-zone.bicep' = {
  name: 'dns-cognitive'
  params: { zoneName: 'privatelink.cognitiveservices.azure.com', vnetId: vnet.id, tags: tags }
}
module zoneOpenAi 'private-dns-zone.bicep' = {
  name: 'dns-openai'
  params: { zoneName: 'privatelink.openai.azure.com', vnetId: vnet.id, tags: tags }
}
module zoneAiServices 'private-dns-zone.bicep' = {
  name: 'dns-ai-services'
  params: { zoneName: 'privatelink.services.ai.azure.com', vnetId: vnet.id, tags: tags }
}
module zoneRedis 'private-dns-zone.bicep' = {
  name: 'dns-redis'
  params: { zoneName: 'privatelink.redis.azure.net', vnetId: vnet.id, tags: tags }
}

output vnetId string = vnet.id
output environmentSubnetId string = '${vnet.id}/subnets/snet-containerapps'
output privateEndpointSubnetId string = '${vnet.id}/subnets/snet-private-endpoints'
output postgresZoneId string = zonePostgres.outputs.id
output vaultZoneId string = zoneVault.outputs.id
output aiServicesZoneIds array = [
  zoneCognitive.outputs.id
  zoneOpenAi.outputs.id
  zoneAiServices.outputs.id
]
output redisZoneId string = zoneRedis.outputs.id
