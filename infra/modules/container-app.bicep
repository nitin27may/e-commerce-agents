@description('App name; also its in-environment DNS name. Max 32 characters.')
@maxLength(32)
param name string
param location string
param tags object
param environmentId string
@description('User-assigned identity: resolves Key Vault secret references.')
param identityId string
param image string
@description('Container command override (empty keeps the image CMD).')
param command array = []

@description('Plain environment variables. Never put a secret here.')
param env array = []
@description('Key Vault backed variables: [{ env: "JWT_SECRET", secret: "jwt-secret" }]. Each Key Vault secret name may appear once.')
param secretEnv array = []
@description('Vault URI with trailing slash, e.g. https://kv-x.vault.azure.net/')
param keyVaultUri string

@description('Ingress target port. 0 = no ingress (worker).')
param targetPort int = 0
@allowed(['http', 'tcp'])
param transport string = 'http'
@description('Internet-facing ingress. Only the frontend sets this.')
param externalIngress bool = false
@description('IP allow-list (CIDR) applied to ingress; empty means no restriction.')
param allowedIpRanges array = []
@description('Port callers use for tcp ingress.')
param exposedPort int = 0

@description('HTTP liveness and startup path. Empty with a non-zero targetPort and transport tcp gives tcp probes.')
param healthPath string = '/health'
@description('HTTP readiness path.')
param readyPath string = '/ready'
@description('Probe the container at all. False for the worker, which has no port.')
param probesEnabled bool = true

param cpu string = '0.5'
param memory string = '1Gi'
param minReplicas int = 1
param maxReplicas int = 2
@description('Concurrent requests per replica that trigger scale-out. 0 = no HTTP rule.')
param httpConcurrency int = 0

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

var ipRules = [
  for (range, i) in allowedIpRanges: {
    name: 'allow-${i}'
    action: 'Allow'
    ipAddressRange: range
  }
]

var hasIngress = targetPort > 0
var httpProbes = [
  // Limits (Container Apps): initialDelaySeconds 1-60, periodSeconds 1-240, timeoutSeconds 1-240,
  // failureThreshold 1-10. Startup allows 10 x 10 s = 100 s for a slow Python import.
  {
    type: 'Startup'
    httpGet: { path: healthPath, port: targetPort }
    initialDelaySeconds: 5
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 10
  }
  {
    type: 'Liveness'
    httpGet: { path: healthPath, port: targetPort }
    periodSeconds: 20
    timeoutSeconds: 5
    failureThreshold: 3
  }
  {
    type: 'Readiness'
    httpGet: { path: readyPath, port: targetPort }
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 3
    successThreshold: 1
  }
]
var tcpProbes = [
  {
    type: 'Startup'
    tcpSocket: { port: targetPort }
    initialDelaySeconds: 2
    periodSeconds: 5
    timeoutSeconds: 3
    failureThreshold: 10
  }
  {
    type: 'Liveness'
    tcpSocket: { port: targetPort }
    periodSeconds: 20
    timeoutSeconds: 5
    failureThreshold: 3
  }
]
var probes = !probesEnabled || !hasIngress ? [] : (transport == 'tcp' ? tcpProbes : httpProbes)

var scaleRules = httpConcurrency > 0 && transport == 'http'
  ? [
      {
        name: 'http-concurrency'
        http: { metadata: { concurrentRequests: string(httpConcurrency) } }
      }
    ]
  : []

resource app 'Microsoft.App/containerApps@2025-07-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    managedEnvironmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: secretList
      ingress: hasIngress
        ? {
            external: externalIngress
            targetPort: targetPort
            transport: transport
            exposedPort: transport == 'tcp' ? (exposedPort > 0 ? exposedPort : targetPort) : null
            allowInsecure: false
            ipSecurityRestrictions: empty(allowedIpRanges) ? null : ipRules
          }
        : null
    }
    template: {
      containers: [
        {
          name: name
          image: image
          command: empty(command) ? null : command
          env: concat(env, secretEnvList)
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          probes: probes
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: scaleRules
      }
    }
  }
}

output id string = app.id
output name string = app.name
@description('Reachable inside the environment over HTTPS (http transport) at this host.')
output fqdn string = hasIngress ? app.properties.configuration.ingress.fqdn : ''
