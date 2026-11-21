// Placeholder. The Redis container app lands with the Container Apps
// environment in I2. This file pins the interface main.bicep will call.
// TODO(I2): define Microsoft.App/containerApps (redis:7, internal TCP ingress
// on 6379, min/max replicas from the profile) against environmentId.
param name string
#disable-next-line no-unused-params
param environmentId string
#disable-next-line no-unused-params
param location string
#disable-next-line no-unused-params
param tags object
#disable-next-line no-unused-params
param minReplicas int = 1
#disable-next-line no-unused-params
param maxReplicas int = 1

@description('In-environment DNS name callers use for REDIS_URL.')
output host string = name
