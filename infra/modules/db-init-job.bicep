// One-shot database bootstrap: migrate, seed if empty, embeddings, ANALYZE
// (scripts/db_init.py, image db-init). Manual trigger; scripts/azure/postprovision starts it.
// It needs the same secret set as the services: ENVIRONMENT=production turns the secret
// validator on, and a job with a placeholder secret crash-loops.
param name string
param location string
param tags object
param environmentId string
param identityId string
param image string
@description('Registry the image comes from when it is an ACR (pulled with the user-assigned identity). Empty for GHCR.')
param registryServer string = ''
param env array
param secretEnv array
param keyVaultUri string
@description('Replica timeout. The embeddings step dominates a first run.')
@minValue(60)
@maxValue(1800)
param timeoutSeconds int = 900

var secretList = [
  for s in secretEnv: {
    name: s.secret
    keyVaultUrl: '${keyVaultUri}secrets/${s.secret}'
    identity: identityId
  }
]
var secretEnvList = [
  for s in secretEnv: {
    name: s.env
    secretRef: s.secret
  }
]

resource job 'Microsoft.App/jobs@2025-07-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: timeoutSeconds
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: secretList
      registries: empty(registryServer) ? null : [{ server: registryServer, identity: identityId }]
    }
    template: {
      containers: [
        {
          name: 'db-init'
          image: image
          env: concat(env, secretEnvList)
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output id string = job.id
output name string = job.name
