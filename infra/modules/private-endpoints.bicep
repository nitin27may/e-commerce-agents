// Private endpoints with their DNS zone groups. One entry per target:
//   { name: 'pe-kv', resourceId: <id>, groupId: 'vault', zoneIds: [<zone id>, ...] }
// The target keeps its public name; the zone group makes that name resolve to the endpoint's
// private address from inside the linked VNet.
param location string
param tags object
param subnetId string
param endpoints array

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2025-05-01' = [
  for e in endpoints: {
    name: e.name
    location: location
    tags: tags
    properties: {
      subnet: { id: subnetId }
      privateLinkServiceConnections: [
        {
          name: e.name
          properties: {
            privateLinkServiceId: e.resourceId
            groupIds: [e.groupId]
          }
        }
      ]
    }
  }
]

resource zoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-05-01' = [
  for (e, i) in endpoints: {
    parent: privateEndpoint[i]
    name: 'default'
    properties: {
      privateDnsZoneConfigs: [
        for (zoneId, j) in e.zoneIds: {
          name: 'zone-${j}'
          properties: { privateDnsZoneId: zoneId }
        }
      ]
    }
  }
]
