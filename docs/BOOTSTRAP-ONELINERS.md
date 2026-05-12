# Bootstrap one-liners — Azure Cloud Shell (Bash)

Plak deze blokjes in volgorde in **Cloud Shell (Bash)** op portal.azure.com,
ingelogd met je GA / break-glass account (`cmp-breakglass-tq1072@Campai.onmicrosoft.com`).

Tussen blokjes door even wachten tot het commando klaar is. Elk blok eindigt
met een echo zodat je ziet of het werkte.

---

## Blok 1 — Variabelen + subscription

```bash
SUB=1886dd8f-1ce7-4af2-8491-338341544b55
TENANT=bf030cec-d527-4f3c-9843-db5473bee3f7
LOCATION=westeurope
ENV=prod
DAILY_USER=Janneke@campai.nl
REPO=Github-campai/AzureMCP-CMS

az account set --subscription $SUB && az account show --query "{name:name,user:user.name}" -o table
```

---

## Blok 2 — Repo clonen in Cloud Shell

```bash
rm -rf ~/AzureMCP-CMS && git clone https://github.com/$REPO.git ~/AzureMCP-CMS && cd ~/AzureMCP-CMS && pwd
```

---

## Blok 3 — Entra app registratie aanmaken (multi-tenant)

```bash
APP_ID=$(az ad app create --display-name CMS-MCP-API --sign-in-audience AzureADMultipleOrgs --query appId -o tsv) && \
APP_OBJ=$(az ad app show --id $APP_ID --query id -o tsv) && \
az ad app update --id $APP_OBJ --identifier-uris api://cms-mcp && \
SCOPE_ID=$(cat /proc/sys/kernel/random/uuid) && \
az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJ" --headers "Content-Type=application/json" --body "{\"api\":{\"oauth2PermissionScopes\":[{\"id\":\"$SCOPE_ID\",\"adminConsentDescription\":\"Read ADMCMS data on behalf of user\",\"adminConsentDisplayName\":\"Read ADMCMS data\",\"userConsentDescription\":\"Read ADMCMS data on your behalf\",\"userConsentDisplayName\":\"Read ADMCMS data\",\"value\":\"admcms.read\",\"type\":\"User\",\"isEnabled\":true}]}}" && \
az ad sp create --id $APP_ID 2>/dev/null; \
echo "=== APP_ID=$APP_ID ==="
```

> **Belangrijk**: Schrijf de APP_ID op die uit dit blok komt. Je hebt 'm later nodig.

---

## Blok 4 — Bicep deployen (resource group + alle resources)

```bash
MY_OID=$(az ad signed-in-user show --query id -o tsv) && \
az deployment sub create \
  --name cms-mcp-$ENV-$(date +%s) \
  --location $LOCATION \
  --template-file infra/main.bicep \
  --parameters environmentName=$ENV location=$LOCATION deployerPrincipalId=$MY_OID entraAppId=$APP_ID entraAllowedTenants=$TENANT \
  --query "properties.outputs" -o json
```

Looptijd: ~2-3 minuten. Bewaar de output (resource names komen eruit).

---

## Blok 5 — Roland's image bouwen in ACR + Container App updaten

```bash
RG=CMS-MCP-rg-$ENV && \
ACR=$(az acr list -g $RG --query "[0].name" -o tsv) && \
SERVER=$(az acr show -n $ACR --query loginServer -o tsv) && \
TAG=$(date +%Y%m%d%H%M%S) && \
echo "Building image in $ACR (this takes ~5 minutes — ODBC driver install is slow)..." && \
az acr build -r $ACR -t admcms-mcp:$TAG -t admcms-mcp:latest -f Dockerfile . && \
az containerapp update -n cms-mcp-app-$ENV -g $RG --image $SERVER/admcms-mcp:$TAG -o none && \
FQDN=$(az containerapp show -n cms-mcp-app-$ENV -g $RG --query "properties.configuration.ingress.fqdn" -o tsv) && \
echo "=== DEPLOYED: https://$FQDN ==="
```

---

## Blok 6 — Daily account (Janneke@campai.nl) ops-toegang geven

```bash
RG=CMS-MCP-rg-$ENV && \
KV=cms-mcp-kv-$ENV && \
DAILY_OID=$(az ad user show --id $DAILY_USER --query id -o tsv) && \
az role assignment create --assignee $DAILY_OID --role Contributor --scope /subscriptions/$SUB/resourceGroups/$RG -o none && \
az role assignment create --assignee $DAILY_OID --role "Key Vault Secrets Officer" --scope /subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/$KV -o none && \
echo "=== $DAILY_USER granted Contributor + KV Secrets Officer on $RG ==="
```

---

## Blok 7 — GitHub Actions federated OIDC opzetten

```bash
CI_APP_ID=$(az ad app create --display-name CMS-MCP-GitHubActions --sign-in-audience AzureADMyOrg --query appId -o tsv) && \
az ad sp create --id $CI_APP_ID 2>/dev/null; \
CI_APP_OBJ=$(az ad app show --id $CI_APP_ID --query id -o tsv) && \
CI_SP_OID=$(az ad sp show --id $CI_APP_ID --query id -o tsv) && \
az ad app federated-credential create --id $CI_APP_OBJ --parameters "{\"name\":\"github-azuremcp-cms-main\",\"issuer\":\"https://token.actions.githubusercontent.com\",\"subject\":\"repo:$REPO:ref:refs/heads/main\",\"audiences\":[\"api://AzureADTokenExchange\"]}" && \
RG=CMS-MCP-rg-$ENV && \
ACR=$(az acr list -g $RG --query "[0].name" -o tsv) && \
az role assignment create --assignee $CI_SP_OID --role Contributor --scope /subscriptions/$SUB/resourceGroups/$RG -o none && \
az role assignment create --assignee $CI_SP_OID --role AcrPush --scope /subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.ContainerRegistry/registries/$ACR -o none && \
echo "" && echo "=== GITHUB SECRETS TO ADD ===" && \
echo "  Repo: https://github.com/$REPO/settings/secrets/actions" && \
echo "" && \
echo "  AZURE_CLIENT_ID = $CI_APP_ID" && \
echo "  AZURE_TENANT_ID = $TENANT"
```

---

## Blok 8 — Health check + samenvatting

```bash
RG=CMS-MCP-rg-$ENV && \
FQDN=$(az containerapp show -n cms-mcp-app-$ENV -g $RG --query "properties.configuration.ingress.fqdn" -o tsv) && \
echo "" && \
echo "=== SUMMARY ===" && \
echo "Container App:   https://$FQDN" && \
echo "Healthz:         $(curl -s -o /dev/null -w '%{http_code}' https://$FQDN/healthz)" && \
echo "OAuth metadata:  https://$FQDN/.well-known/oauth-protected-resource" && \
echo "Entra App ID:    $APP_ID" && \
echo "Admin consent URL voor klant tenant:" && \
echo "  https://login.microsoftonline.com/<CMS_TENANT_ID>/adminconsent?client_id=$APP_ID"
```

---

# Daarna (vanaf je daily account, niet meer GA-account nodig)

## Key Vault secrets seeden

In Cloud Shell of lokaal (na `az login` met je daily account):

```bash
SUB=1886dd8f-1ce7-4af2-8491-338341544b55
RG=CMS-MCP-rg-prod
KV=cms-mcp-kv-prod
MY_OID=$(az ad signed-in-user show --query id -o tsv)

az account set --subscription $SUB

# DB password (vervang met echte waarde)
read -s -p "ADMCMS DB password: " DB_PWD && echo ""
az keyvault secret set --vault-name $KV --name admcms-db-password --value "$DB_PWD" -o none && echo "DB password set"

# Allowlist (jouw OID voor eerste test)
ALLOWLIST="{\"users\":[\"$MY_OID\"],\"groups\":[]}"
az keyvault secret set --vault-name $KV --name admcms-allowlist --value "$ALLOWLIST" -o none && echo "Allowlist set"
```

## SQL firewall openen (ergens, niet hier — admcms server staat in andere sub)

Op de sub waar `cmsnl.database.windows.net` staat:

```bash
az sql server firewall-rule create -g <admcms-rg> -s cmsnl -n AllowAzureServices --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0
```

(`0.0.0.0/0.0.0.0` is de Azure-conventie voor "alle Azure-services".)

## CMS tenant ID toevoegen wanneer bekend

```bash
SUB=1886dd8f-1ce7-4af2-8491-338341544b55
RG=CMS-MCP-rg-prod
CMS_TENANT=<vul-in>
CAMPAI=bf030cec-d527-4f3c-9843-db5473bee3f7

az account set --subscription $SUB
az containerapp update -n cms-mcp-app-prod -g $RG --set-env-vars "ENTRA_ALLOWED_TENANTS=$CAMPAI,$CMS_TENANT" -o none
```
