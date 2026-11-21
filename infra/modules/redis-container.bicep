// Demo-profile Redis: redis:8-alpine as an internal TCP container app (Azure Cache for
// Redis is closed to new customers). Rate limiting and caching only, and both fail open,
// so losing it never takes the stack down. No persistence: a restart drops the counters.
@description('Container app name; also the in-environment DNS name callers use in REDIS_URL.')
param name string
param environmentId string
param identityId string
param location string
param tags object
param minReplicas int = 1
param maxReplicas int = 1
param image string = 'docker.io/library/redis:8-alpine'

module app 'container-app.bicep' = {
  name: 'app-${name}'
  params: {
    name: name
    location: location
    tags: tags
    environmentId: environmentId
    identityId: identityId
    keyVaultUri: ''
    image: image
    // No password: the app is reachable only from inside the environment (internal TCP ingress).
    command: ['redis-server', '--save', '', '--appendonly', 'no']
    targetPort: 6379
    transport: 'tcp'
    exposedPort: 6379
    cpu: '0.25'
    memory: '0.5Gi'
    minReplicas: minReplicas
    maxReplicas: maxReplicas
  }
}

@description('In-environment host callers use for REDIS_URL (redis://<host>:6379).')
output host string = name
