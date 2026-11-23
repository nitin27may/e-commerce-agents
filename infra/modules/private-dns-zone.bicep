// One private DNS zone linked to the VNet, so a private endpoint's A record resolves inside it.
@description('Zone name, e.g. privatelink.vaultcore.azure.net.')
param zoneName string
param vnetId string
param tags object

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: zoneName
  location: 'global'
  tags: tags
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'link-vnet'
  location: 'global'
  tags: tags
  properties: {
    virtualNetwork: { id: vnetId }
    registrationEnabled: false
  }
}

output id string = zone.id
