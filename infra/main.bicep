targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Environment name (dev, prod, staging)')
param environmentName string

@minLength(1)
@description('Primary Azure region for all resources')
param location string = 'westeurope'

@description('Deployer principal ID (run: az ad signed-in-user show --query id -o tsv)')
param deployerPrincipalId string

@description('Resource group name (default: CMS-MCP-rg-<env>)')
param resourceGroupName string = 'CMS-MCP-rg-${environmentName}'

@description('Entra app registration client ID for ADMCMS API (set after app reg is created)')
param entraAppId string = '00000000-0000-0000-0000-000000000000'

@description('Comma-separated Entra tenant IDs allowed to authenticate (Campai + CMS Lelystad)')
param entraAllowedTenants string = 'bf030cec-d527-4f3c-9843-db5473bee3f7'

@description('Disable auth entirely (DEV ONLY — never set true in prod)')
param authDisabled bool = false

@description('ADMCMS SQL server FQDN')
param admcmsServer string = 'cmsnl.database.windows.net'

@description('ADMCMS database name')
param admcmsDatabase string = 'ADMCMS-PRODUCTION-REPL01'

var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'environment': environmentName
  'customer': 'CMS-Lelystad'
  'project': 'CMS-MCP'
  'managed-by': 'Campai-MSP'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'cms-mcp-resources'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    resourceToken: resourceToken
    tags: tags
    deployerPrincipalId: deployerPrincipalId
    entraAppId: entraAppId
    entraAllowedTenants: entraAllowedTenants
    authDisabled: authDisabled
    admcmsServer: admcmsServer
    admcmsDatabase: admcmsDatabase
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output AZURE_RESOURCE_TOKEN string = resourceToken
output CONTAINER_APP_NAME string = resources.outputs.containerAppName
output CONTAINER_APP_FQDN string = resources.outputs.containerAppFqdn
output CONTAINER_REGISTRY_NAME string = resources.outputs.containerRegistryName
output CONTAINER_REGISTRY_LOGIN_SERVER string = resources.outputs.containerRegistryLoginServer
output KEY_VAULT_NAME string = resources.outputs.keyVaultName
output KEY_VAULT_URI string = resources.outputs.keyVaultUri
output MANAGED_IDENTITY_NAME string = resources.outputs.managedIdentityName
output MANAGED_IDENTITY_CLIENT_ID string = resources.outputs.managedIdentityClientId
output APP_INSIGHTS_NAME string = resources.outputs.appInsightsName
