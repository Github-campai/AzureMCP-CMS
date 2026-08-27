targetScope = 'resourceGroup'

param environmentName string
param location string
param resourceToken string
param tags object
param deployerPrincipalId string
param entraAppId string
param entraAllowedTenants string
param authDisabled bool
param admcmsServer string
param admcmsDatabase string

@description('Externally-visible base URL, e.g. https://admcms.example.com. Empty = derive from the ingress X-Forwarded-* headers. The deploy workflow keeps this in sync with the live hostname, so a bicep re-run no longer drops it.')
param publicBaseUrl string = ''

@description('Entra authority the OAuth proxy sends users to. organizations = any work/school tenant; validate_jwt still rejects tenants outside entraAllowedTenants.')
param entraAuthority string = 'organizations'

@description('Escape hatch for the Copilot Studio preview connector: allow unauthenticated GET on /mcp. Leave false — it lets anyone hold an SSE stream open.')
param allowUnauthenticatedMcpGet bool = false

@description('Container image to deploy. Bootstrap script seeds a hello-world placeholder; CI updates it on every push.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Container Apps CPU (cores)')
param cpu string = '0.5'

@description('Container Apps memory')
param memory string = '1Gi'

@description('Storage account holding the audit table. Defaults to the account that is already live in CMS-MCP-rg-prod; a name is passed explicitly rather than derived so a redeploy binds to the existing account instead of creating a second one.')
param auditStorageAccountName string = 'cmsmcpauditprod39b6'

@description('Table holding the audit entries. Must match AUDIT_TABLE_NAME.')
param auditTableName string = 'admcmsaudit'

@description('Assign Storage Table Data Contributor to the managed identity. The assignment already exists in prod (created by hand); set to false if a deploy trips over RoleAssignmentExists.')
param assignAuditTableRole bool = true

@description('How long audit rows are kept, in days. See docs/ARCHITECTUUR.md 8 for the reasoning behind the number.')
param auditRetentionDays int = 365

@description('Min replicas (0 = scale to zero)')
param minReplicas int = 1

@description('Max replicas')
param maxReplicas int = 3

// ─────────────────────────────────────────────────────────────────────
// Naming — CMS-MCP-{kind}-{env}
// ─────────────────────────────────────────────────────────────────────
var namingPrefix = 'CMS-MCP'
var namingPrefixLower = 'cms-mcp'

var logAnalyticsName = '${namingPrefix}-law-${environmentName}'
var appInsightsName = '${namingPrefix}-ai-${environmentName}'
var keyVaultName = '${namingPrefixLower}-kv-${environmentName}'
var managedIdentityName = '${namingPrefix}-id-${environmentName}'
var containerRegistryName = '${replace(namingPrefixLower, '-', '')}acr${environmentName}${substring(resourceToken, 0, 4)}'
var containerAppsEnvName = '${namingPrefix}-env-${environmentName}'
var containerAppName = '${namingPrefixLower}-app-${environmentName}'

// ─────────────────────────────────────────────────────────────────────
// Built-in role definition IDs
// ─────────────────────────────────────────────────────────────────────
var contributorRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'
var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var acrPushRoleId = '8311e382-0749-4cb8-b61a-304f252e45ec'

// ─────────────────────────────────────────────────────────────────────
// 1. User-Assigned Managed Identity (used by Container App for KV + ACR)
// ─────────────────────────────────────────────────────────────────────
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: managedIdentityName
  location: location
  tags: tags
}

// ─────────────────────────────────────────────────────────────────────
// 2. Log Analytics + Application Insights
// ─────────────────────────────────────────────────────────────────────
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ─────────────────────────────────────────────────────────────────────
// 3. Key Vault (RBAC-mode, soft-delete on by default)
// ─────────────────────────────────────────────────────────────────────
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

// Deployer (you) gets Secrets Officer on KV so deploy scripts can seed secrets.
resource kvDeployerSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, deployerPrincipalId, 'kv-secrets-officer')
  scope: keyVault
  properties: {
    principalId: deployerPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficerRoleId)
    principalType: 'User'
  }
}

// Managed Identity gets Secrets User on KV so Container App can read secrets at runtime.
resource kvIdentitySecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, managedIdentity.id, 'kv-secrets-user')
  scope: keyVault
  properties: {
    principalId: managedIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ─────────────────────────────────────────────────────────────────────
// 3b. Audit storage (Table Storage)
// ─────────────────────────────────────────────────────────────────────
// The container filesystem is ephemeral and there are up to three replicas, so
// the SQLite store meant the trail was split per replica and wiped by every
// restart. Table Storage is shared, survives restarts and needs no key: the
// managed identity authenticates with DefaultAzureCredential.
//
// These resources already exist in CMS-MCP-rg-prod; they were created by hand
// and never written down. Declaring them here is what stops the next `az
// deployment group create` from resetting the app back to AUDIT_STORE=local.
resource auditStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: auditStorageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // Identity-based access only; there is no key to leak or rotate.
    allowSharedKeyAccess: false
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource auditTableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: auditStorage
  name: 'default'
}

resource auditTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: auditTableService
  name: auditTableName
}

// Managed Identity may read and write audit rows. Table Data Contributor is the
// narrowest built-in role that allows both; the app never touches blobs.
resource auditTableRoleForIdentity 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (assignAuditTableRole) {
  name: guid(auditStorage.id, managedIdentity.id, 'storage-table-data-contributor')
  scope: auditStorage
  properties: {
    principalId: managedIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ─────────────────────────────────────────────────────────────────────
// 4. Container Registry
// ─────────────────────────────────────────────────────────────────────
resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: containerRegistryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

// Managed Identity can pull from ACR.
resource acrPullForIdentity 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, managedIdentity.id, 'acr-pull')
  scope: containerRegistry
  properties: {
    principalId: managedIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalType: 'ServicePrincipal'
  }
}

// Deployer can push to ACR (for `az acr build`).
resource acrPushForDeployer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, deployerPrincipalId, 'acr-push')
  scope: containerRegistry
  properties: {
    principalId: deployerPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPushRoleId)
    principalType: 'User'
  }
}

// ─────────────────────────────────────────────────────────────────────
// 5. Container Apps Environment
// ─────────────────────────────────────────────────────────────────────
resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppsEnvName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ─────────────────────────────────────────────────────────────────────
// 6. Container App (Roland's admcms_mcp server)
// ─────────────────────────────────────────────────────────────────────
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            weight: 100
            latestRevision: true
          }
        ]
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'admcms-mcp'
          image: containerImage
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            {
              name: 'KEY_VAULT_URI'
              value: keyVault.properties.vaultUri
            }
            {
              name: 'ENTRA_APP_ID'
              value: entraAppId
            }
            {
              name: 'ENTRA_ALLOWED_TENANTS'
              value: entraAllowedTenants
            }
            {
              name: 'AUTH_DISABLED'
              value: string(authDisabled)
            }
            {
              name: 'ADMCMS_SERVER'
              value: admcmsServer
            }
            {
              name: 'ADMCMS_DATABASE'
              value: admcmsDatabase
            }
            {
              name: 'PUBLIC_BASE_URL'
              value: publicBaseUrl
            }
            {
              name: 'ENTRA_AUTHORITY'
              value: entraAuthority
            }
            {
              name: 'ALLOW_UNAUTHENTICATED_MCP_GET'
              value: string(allowUnauthenticatedMcpGet)
            }
            {
              name: 'AUDIT_STORE'
              value: 'azure_table'
            }
            {
              name: 'AUDIT_TABLE_ACCOUNT_URL'
              value: 'https://${auditStorage.name}.table.${environment().suffixes.storage}'
            }
            {
              name: 'AUDIT_TABLE_NAME'
              value: auditTableName
            }
            {
              name: 'AUDIT_RETENTION_DAYS'
              value: string(auditRetentionDays)
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: managedIdentity.properties.clientId
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '30'
              }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    acrPullForIdentity
    kvIdentitySecretsUser
  ]
}

// Deployer needs Contributor on the Container App to run `az containerapp update` from CI.
resource appContributorForDeployer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerApp.id, deployerPrincipalId, 'app-contributor')
  scope: containerApp
  properties: {
    principalId: deployerPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleId)
    principalType: 'User'
  }
}

// ─────────────────────────────────────────────────────────────────────
// Outputs
// ─────────────────────────────────────────────────────────────────────
output containerAppName string = containerApp.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerRegistryName string = containerRegistry.name
output containerRegistryLoginServer string = containerRegistry.properties.loginServer
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output managedIdentityName string = managedIdentity.name
output managedIdentityClientId string = managedIdentity.properties.clientId
output managedIdentityPrincipalId string = managedIdentity.properties.principalId
output appInsightsName string = appInsights.name
output logAnalyticsWorkspaceName string = logAnalytics.name
