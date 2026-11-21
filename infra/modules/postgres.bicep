param name string
param location string
param tags object
param administratorLogin string = 'ecomadmin'
@secure()
param administratorPassword string
param databaseName string = 'ecommerce_agents'
param tier string
param skuName string
param storageSizeGB int
@allowed(['Disabled', 'ZoneRedundant', 'SameZone'])
param highAvailability string
param backupRetentionDays int
@allowed(['Enabled', 'Disabled'])
param geoRedundantBackup string
@description('Demo only: lets Azure-hosted services reach the server without a private endpoint.')
param allowAzureServices bool

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2025-08-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: tier
  }
  properties: {
    version: '16'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
    storage: { storageSizeGB: storageSizeGB }
    backup: {
      backupRetentionDays: backupRetentionDays
      geoRedundantBackup: geoRedundantBackup
    }
    highAvailability: { mode: highAvailability }
    // TODO(I2+): the reference profile should move to VNet integration or a private endpoint.
    network: { publicNetworkAccess: 'Enabled' }
  }
}

// Server parameter writes and child creates on one flexible server must be
// serialised: concurrent operations fail with ServerIsBusy.
resource extensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2025-08-01' = {
  parent: server
  name: 'azure.extensions'
  properties: {
    value: 'VECTOR,PGCRYPTO'
    source: 'user-override'
  }
}

resource requireTls 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2025-08-01' = {
  parent: server
  name: 'require_secure_transport'
  properties: {
    value: 'ON'
    source: 'user-override'
  }
  dependsOn: [extensions]
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2025-08-01' = {
  parent: server
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
  dependsOn: [requireTls]
}

resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2025-08-01' = if (allowAzureServices) {
  parent: server
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
  dependsOn: [database]
}

output name string = server.name
output fqdn string = server.properties.fullyQualifiedDomainName
output databaseName string = database.name
output administratorLogin string = administratorLogin
