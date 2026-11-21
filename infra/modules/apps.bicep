// Every workload in the Container Apps environment: frontend (the only external ingress),
// orchestrator, five specialists, outbox worker, the Redis container, the db-init job and,
// opt-in, the auth server and the two MCP servers. See docs/azure-deployment.md.
//
// Wiring rules this file encodes (each one is a first-run failure in the catalogue):
//  - ENVIRONMENT=production turns the secret validator on, so the worker and the job carry the
//    same secret set as the services. Never a placeholder.
//  - Every secret is a Key Vault reference resolved by the user-assigned identity.
//  - Specialists are called at https://ca-<svc>.internal.<defaultDomain>: the environment redirects
//    plain http to https and an A2A POST does not survive that redirect.
//  - OTEL_EXPORTER_OTLP_ENDPOINT is never set: the managed OpenTelemetry agent injects it.
//  - Stream and approval timeouts stay under the 240 s ingress request limit.
param location string
param tags object
param environmentId string
@description('Environment default domain, from the Container Apps environment.')
param defaultDomain string
param identityId string
@description('Client id of the user-assigned identity (AZURE_CLIENT_ID for Entra auth).')
param identityClientId string
@description('Vault URI with trailing slash.')
param keyVaultUri string

param imageRegistry string
@description('Pinned release tag, e.g. v1.4.0 or sha-1a2b3c4. Never latest: a re-deploy must not change what runs.')
param imageTag string

param openAiEndpoint string
param chatDeployment string
param embeddingDeployment string
param chatModelName string
param openAiApiVersion string
@allowed(['key', 'identity'])
param openAiAuth string = 'key'

@description('containerApps section of the profile.')
param scale object
@description('Cost budget behaviour. enforce stops a run once it passes the per-run ceiling.')
@allowed(['off', 'observe', 'enforce'])
param costBudgetMode string = 'enforce'
param costBudgetUsdPerRun string = '0.50'

@description('restricted: the frontend accepts only allowedIpRanges (default). open: anyone, which is an open spend tap.')
@allowed(['restricted', 'open'])
param publicAccess string = 'restricted'
@description('CIDR ranges allowed to reach the frontend when publicAccess is restricted.')
param allowedIpRanges array = []

param deployAuthServer bool = false
param deployMcp bool = false

var domain = defaultDomain
var frontendUrl = 'https://ca-frontend.${domain}'
var orchestratorUrl = 'https://ca-orchestrator.internal.${domain}'
var authServerUrl = 'https://ca-auth-server.internal.${domain}'

func image(registry string, service string, tag string) string => '${registry}/${service}:${tag}'

// Fail closed: an empty allow-list means "no restriction" to Container Apps, so a restricted
// deployment with no address gets one that matches nobody (RFC 5737 documentation range).
var frontendAllowedRanges = publicAccess == 'open' ? [] : (empty(allowedIpRanges) ? ['192.0.2.1/32'] : allowedIpRanges)

var specialists = [
  { name: 'product-discovery', port: 8081 }
  { name: 'order-management', port: 8082 }
  { name: 'pricing-promotions', port: 8083 }
  { name: 'review-sentiment', port: 8084 }
  { name: 'inventory-fulfillment', port: 8085 }
]

var agentRegistry = string({
  'product-discovery': 'https://ca-product-discovery.internal.${domain}'
  'order-management': 'https://ca-order-management.internal.${domain}'
  'pricing-promotions': 'https://ca-pricing-promotions.internal.${domain}'
  'review-sentiment': 'https://ca-review-sentiment.internal.${domain}'
  'inventory-fulfillment': 'https://ca-inventory-fulfillment.internal.${domain}'
})

// gpt-5.x rejects `temperature`; an empty value makes the app omit it (Azure P2).
var llmTemperature = startsWith(chatModelName, 'gpt-5') ? '' : '0.2'

// ---------------------------------------------------------------- shared wiring

var backendSecretEnv = concat(
  [
    { env: 'DATABASE_URL', secret: 'database-url' }
    { env: 'JWT_SECRET', secret: 'jwt-secret' }
    { env: 'AGENT_SHARED_SECRET', secret: 'agent-shared-secret' }
    { env: 'A2A_IDENTITY_KEY', secret: 'a2a-identity-key' }
    { env: 'OAUTH_SEED_KEY', secret: 'oauth-seed-key' }
    { env: 'AUTH_SIGNING_KEY_ENCRYPTION_KEY', secret: 'auth-signing-key-encryption-key' }
  ],
  openAiAuth == 'key' ? [{ env: 'AZURE_OPENAI_KEY', secret: 'azure-openai-key' }] : []
)

var authEnv = deployAuthServer
  ? [
      { name: 'AUTH_MODE', value: 'oauth' }
      { name: 'AUTH_SERVER_ISSUER', value: authServerUrl }
      { name: 'AUTH_SERVER_JWKS_URL', value: '${authServerUrl}/.well-known/jwks.json' }
      { name: 'AUTH_SERVER_TOKEN_URL', value: '${authServerUrl}/oauth/token' }
    ]
  : [{ name: 'AUTH_MODE', value: 'local' }]

var backendEnv = concat(
  [
    { name: 'ENVIRONMENT', value: 'production' }
    { name: 'LLM_PROVIDER', value: 'azure' }
    { name: 'LLM_MODEL', value: chatModelName }
    { name: 'LLM_TEMPERATURE', value: llmTemperature }
    { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
    { name: 'AZURE_OPENAI_DEPLOYMENT', value: chatDeployment }
    { name: 'AZURE_EMBEDDING_DEPLOYMENT', value: embeddingDeployment }
    { name: 'AZURE_OPENAI_API_VERSION', value: openAiApiVersion }
    { name: 'AZURE_OPENAI_AUTH', value: openAiAuth }
    { name: 'AZURE_CLIENT_ID', value: identityClientId }
    { name: 'EMBEDDING_MODEL', value: 'text-embedding-3-small' }
    { name: 'REDIS_URL', value: 'redis://${redis.outputs.host}:6379' }
    { name: 'COST_BUDGET_MODE', value: costBudgetMode }
    { name: 'COST_BUDGET_USD_PER_RUN', value: costBudgetUsdPerRun }
    { name: 'DB_POOL_MIN_SIZE', value: '1' }
    { name: 'DB_POOL_MAX_SIZE', value: '4' }
    { name: 'MAF_STREAM_TIMEOUT_SECONDS', value: '120' }
    { name: 'HITL_RESUME_TIMEOUT_SECONDS', value: '200' }
    { name: 'A2A_TRANSPORT', value: 'legacy' }
    { name: 'A2A_IDENTITY', value: 'both' }
    { name: 'OTEL_ENABLED', value: 'true' }
  ],
  authEnv
)

// ---------------------------------------------------------------- Redis

module redis 'redis-container.bicep' = {
  name: 'app-redis'
  params: {
    name: 'ca-redis'
    location: location
    tags: tags
    environmentId: environmentId
    identityId: identityId
  }
}

// ---------------------------------------------------------------- db-init job

module dbInit 'db-init-job.bicep' = {
  name: 'job-db-init'
  params: {
    name: 'job-db-init'
    location: location
    tags: tags
    environmentId: environmentId
    identityId: identityId
    image: image(imageRegistry, 'db-init', imageTag)
    keyVaultUri: keyVaultUri
    env: concat(backendEnv, [{ name: 'OTEL_SERVICE_NAME', value: 'ecommerce.db-init' }])
    secretEnv: concat(backendSecretEnv, [{ env: 'SEED_STAFF_PASSWORD', secret: 'seed-staff-password' }])
  }
}

// ---------------------------------------------------------------- orchestrator

module orchestrator 'container-app.bicep' = {
  name: 'app-orchestrator'
  params: {
    name: 'ca-orchestrator'
    location: location
    tags: tags
    environmentId: environmentId
    identityId: identityId
    keyVaultUri: keyVaultUri
    image: image(imageRegistry, 'orchestrator', imageTag)
    targetPort: 8080
    minReplicas: scale.orchestratorMinReplicas
    maxReplicas: scale.orchestratorMaxReplicas
    httpConcurrency: scale.httpConcurrency
    cpu: '1'
    memory: '2Gi'
    env: concat(backendEnv, [
      { name: 'OTEL_SERVICE_NAME', value: 'ecommerce.orchestrator' }
      { name: 'OAUTH_CLIENT_ID', value: 'orchestrator' }
      { name: 'AGENT_REGISTRY', value: agentRegistry }
      { name: 'CORS_ALLOWED_ORIGINS', value: frontendUrl }
      // Container Apps ingress plus the Next.js proxy: two hops append to X-Forwarded-For.
      // Verify with two client addresses on the first real deploy.
      { name: 'RATE_LIMIT_TRUSTED_PROXY_HOPS', value: '2' }
    ])
    secretEnv: backendSecretEnv
  }
}

// ---------------------------------------------------------------- specialists

module specialistApps 'container-app.bicep' = [
  for s in specialists: {
    name: 'app-${s.name}'
    params: {
      name: 'ca-${s.name}'
      location: location
      tags: tags
      environmentId: environmentId
      identityId: identityId
      keyVaultUri: keyVaultUri
      image: image(imageRegistry, s.name, imageTag)
      targetPort: s.port
      minReplicas: scale.minReplicas
      maxReplicas: scale.maxReplicas
      httpConcurrency: scale.httpConcurrency
      env: concat(
        backendEnv,
        [
          { name: 'OTEL_SERVICE_NAME', value: 'ecommerce.${s.name}' }
          { name: 'OAUTH_CLIENT_ID', value: s.name }
          { name: 'A2A_PUBLIC_BASE_URL', value: 'https://ca-${s.name}.internal.${domain}' }
        ],
        deployMcp && s.name == 'product-discovery'
          ? [
              { name: 'MCP_ENABLED', value: 'true' }
              { name: 'MCP_AUTH_ENABLED', value: (deployAuthServer ? 'true' : 'false') }
              { name: 'MCP_PRODUCT_SERVER_URL', value: 'https://ca-mcp-product.internal.${domain}/mcp' }
            ]
          : [],
        deployMcp && s.name == 'inventory-fulfillment'
          ? [
              { name: 'MCP_ENABLED', value: 'true' }
              { name: 'MCP_AUTH_ENABLED', value: (deployAuthServer ? 'true' : 'false') }
              { name: 'MCP_INVENTORY_SERVER_URL', value: 'https://ca-mcp-inventory.internal.${domain}/mcp' }
            ]
          : []
      )
      secretEnv: backendSecretEnv
    }
  }
]

// ---------------------------------------------------------------- outbox worker

module worker 'container-app.bicep' = {
  name: 'app-outbox-worker'
  params: {
    name: 'ca-outbox-worker'
    location: location
    tags: tags
    environmentId: environmentId
    identityId: identityId
    keyVaultUri: keyVaultUri
    image: image(imageRegistry, 'outbox-worker', imageTag)
    // The image CMD is uvicorn worker.main:app; the worker is a loop, not an app (same override as compose).
    command: ['uv', 'run', '--no-project', 'python', '-m', 'worker.main']
    targetPort: 0
    probesEnabled: false
    minReplicas: scale.workerMinReplicas
    maxReplicas: 1
    env: concat(backendEnv, [
      { name: 'OTEL_SERVICE_NAME', value: 'ecommerce.outbox-worker' }
      { name: 'OUTBOX_WORKER_ENABLED', value: 'true' }
    ])
    secretEnv: backendSecretEnv
  }
}

// ---------------------------------------------------------------- frontend

module frontend 'container-app.bicep' = {
  name: 'app-frontend'
  params: {
    name: 'ca-frontend'
    location: location
    tags: tags
    environmentId: environmentId
    identityId: identityId
    keyVaultUri: keyVaultUri
    image: image(imageRegistry, 'frontend', imageTag)
    targetPort: 3000
    externalIngress: true
    allowedIpRanges: frontendAllowedRanges
    healthPath: '/'
    readyPath: '/'
    minReplicas: scale.minReplicas
    maxReplicas: scale.maxReplicas
    httpConcurrency: scale.httpConcurrency
    env: [
      { name: 'ORCHESTRATOR_URL', value: orchestratorUrl }
      // Next.js standalone binds to the container hostname unless told otherwise, which the
      // ingress cannot reach.
      { name: 'HOSTNAME', value: '0.0.0.0' }
      { name: 'PORT', value: '3000' }
      // Container Apps ingress is the one reverse proxy in front of this app.
      { name: 'WEB_TRUSTED_PROXY_HOPS', value: '1' }
    ]
  }
}

// ---------------------------------------------------------------- opt-in: auth server

module authServer 'container-app.bicep' = if (deployAuthServer) {
  name: 'app-auth-server'
  params: {
    name: 'ca-auth-server'
    location: location
    tags: tags
    environmentId: environmentId
    identityId: identityId
    keyVaultUri: keyVaultUri
    image: image(imageRegistry, 'auth-server', imageTag)
    targetPort: 8090
    minReplicas: scale.minReplicas
    maxReplicas: 1
    env: [
      { name: 'ENVIRONMENT', value: 'production' }
      { name: 'AUTH_SERVER_ISSUER', value: authServerUrl }
      { name: 'AUTH_SERVER_AUDIENCE', value: 'ecommerce-auth-server' }
      { name: 'OTEL_SERVICE_NAME', value: 'ecommerce.auth-server' }
    ]
    secretEnv: [
      { env: 'DATABASE_URL', secret: 'database-url' }
      { env: 'OAUTH_SEED_KEY', secret: 'oauth-seed-key' }
      { env: 'AUTH_SIGNING_KEY_ENCRYPTION_KEY', secret: 'auth-signing-key-encryption-key' }
    ]
  }
}

// ---------------------------------------------------------------- opt-in: MCP servers

var mcpServers = [
  { name: 'mcp-product', port: 9000, audience: 'MCP_PRODUCT_AUDIENCE', audienceValue: 'mcp-product', resourceVar: 'MCP_PRODUCT_RESOURCE_URL' }
  { name: 'mcp-inventory', port: 9001, audience: 'MCP_INVENTORY_AUDIENCE', audienceValue: 'mcp-inventory', resourceVar: 'MCP_INVENTORY_RESOURCE_URL' }
]

module mcpApps 'container-app.bicep' = [
  for m in mcpServers: if (deployMcp) {
    name: 'app-${m.name}'
    params: {
      name: 'ca-${m.name}'
      location: location
      tags: tags
      environmentId: environmentId
      identityId: identityId
      keyVaultUri: keyVaultUri
      image: image(imageRegistry, m.name, imageTag)
      targetPort: m.port
      // The server answers /mcp (or the protected-resource metadata when auth is on); it has no /ready.
      healthPath: deployAuthServer ? '/.well-known/oauth-protected-resource/mcp' : '/mcp'
      readyPath: deployAuthServer ? '/.well-known/oauth-protected-resource/mcp' : '/mcp'
      minReplicas: scale.minReplicas
      maxReplicas: scale.maxReplicas
      env: [
        { name: 'MCP_AUTH_ENABLED', value: (deployAuthServer ? 'true' : 'false') }
        { name: 'AUTH_SERVER_ISSUER', value: authServerUrl }
        { name: 'AUTH_SERVER_JWKS_URL', value: '${authServerUrl}/.well-known/jwks.json' }
        { name: m.audience, value: m.audienceValue }
        { name: m.resourceVar, value: 'https://ca-${m.name}.internal.${domain}/mcp' }
      ]
      secretEnv: [{ env: 'DATABASE_URL', secret: 'database-url' }]
    }
  }
]

output frontendUrl string = frontendUrl
output orchestratorInternalUrl string = orchestratorUrl
output dbInitJobName string = dbInit.outputs.name
