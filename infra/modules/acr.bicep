// Azure Container Registry (Basic) for the reference profile and for imageSource=acr.
// The apps pull with the user-assigned identity (AcrPull); the admin user stays off.
// Basic has no private endpoint (Premium only), so the registry keeps public access with
// identity-only auth. Images arrive by `az acr import` from GHCR or `az acr build` from source
// (scripts/azure/postprovision.sh, scripts/azure/acr-images.sh).
@description('Registry name: 5-50 alphanumerics, globally unique.')
@minLength(5)
@maxLength(50)
param name string
param location string
param tags object
@description('Principals that pull images: the shared user-assigned identity and, with per-app identities, one per app.')
param pullerPrincipalIds array

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource pullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for principalId in pullerPrincipalIds: {
    name: guid(registry.id, principalId, acrPullRoleId)
    scope: registry
    properties: {
      principalId: principalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    }
  }
]

output id string = registry.id
output name string = registry.name
output loginServer string = registry.properties.loginServer
