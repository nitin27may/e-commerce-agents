# One command to an Azure deployment (PowerShell mirror of azure-up.sh).
#   ./scripts/azure-up.ps1 -EnvName demo1 -Location eastus2 [-ImageTag v1.4.0] [-Profile demo|reference]
#                          [-OpenAiLocation eastus2] [-PublicOpen] [-AllowedIps 198.51.100.0/24] [-AuthServer] [-Mcp]
#                          [-Stack python|dotnet] [-ImageSource ghcr|acr] [-BuildFromSource] [-DryRun]
# -ImageSource acr / -BuildFromSource fill the registry with scripts/azure/acr-images.sh, which needs bash (WSL or Git Bash).
# Costs real money while it is up (demo: roughly 4 to 12 USD a day). ./scripts/azure-down.ps1 removes everything.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$EnvName,
    [Parameter(Mandatory)][string]$Location,
    [string]$OpenAiLocation,
    [string]$Profile = 'demo',
    [string]$ImageTag,
    [switch]$PublicOpen,
    [string]$AllowedIps = '',
    [switch]$AuthServer,
    [switch]$Mcp,
    [ValidateSet('python', 'dotnet')][string]$Stack = 'python',
    [ValidateSet('auto', 'ghcr', 'acr')][string]$ImageSource = 'auto',
    [switch]$BuildFromSource,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
. (Join-Path $PSScriptRoot 'azure' 'lib' 'common.ps1')
if ($DryRun) { $env:AZURE_DRY_RUN = '1' }

if ($EnvName -cnotmatch '^[a-z0-9][a-z0-9-]{0,30}$') { Stop-Azure '-EnvName must be lowercase letters, digits and dashes (max 31)' }
if ($BuildFromSource) {
    if ($ImageSource -eq 'ghcr') { Stop-Azure '-BuildFromSource builds into ACR; it cannot be combined with -ImageSource ghcr' }
    $ImageSource = 'acr'
}
$effectiveSource = if ($ImageSource -eq 'auto') { if ($Profile -eq 'reference') { 'acr' } else { 'ghcr' } } else { $ImageSource }
if (-not $ImageTag -and $BuildFromSource) {
    $ImageTag = "src-$(git rev-parse --short=7 HEAD 2>$null)"
    if (git status --porcelain 2>$null) { $ImageTag += "-dirty$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" }
    Write-Log "image tag: $ImageTag (source build; override with -ImageTag)"
}
if (-not $ImageTag) {
    $ImageTag = (git describe --tags --abbrev=0 --match 'v[0-9]*' 2>$null)
    if (-not $ImageTag) { Stop-Azure 'no -ImageTag given and no vX.Y.Z git tag found. Pass -ImageTag <vX.Y.Z or sha-xxxxxxx>.' }
    Write-Log "image tag: $ImageTag (latest git tag; override with -ImageTag)"
}
if ($ImageTag -in 'latest', 'main') { Stop-Azure "-ImageTag $ImageTag floats; use vX.Y.Z or sha-<7>" }
if (-not $OpenAiLocation) { $OpenAiLocation = $Location }
$publicAccess = if ($PublicOpen) { 'open' } else { 'restricted' }

& (Join-Path $PSScriptRoot 'azure' 'prereqs.ps1') -Location $Location -OpenAiLocation $OpenAiLocation -Profile $Profile -EnvName $EnvName -DryRun:$DryRun

$settings = [ordered]@{
    AZURE_LOCATION = $Location; DEPLOY_PROFILE = $Profile; IMAGE_TAG = $ImageTag; PUBLIC_ACCESS = $publicAccess
    ALLOWED_IPS = $AllowedIps; DEPLOY_AUTH_SERVER = "$($AuthServer.IsPresent)".ToLowerInvariant()
    DEPLOY_MCP = "$($Mcp.IsPresent)".ToLowerInvariant(); AZURE_OPENAI_LOCATION = $OpenAiLocation
    DEPLOY_STACK = $Stack; IMAGE_SOURCE = $ImageSource; BUILD_FROM_SOURCE = "$($BuildFromSource.IsPresent)".ToLowerInvariant()
}
if ($env:AZURE_CHAT_MODEL) { $settings['AZURE_CHAT_MODEL'] = $env:AZURE_CHAT_MODEL }
if ($env:AZURE_CHAT_MODEL_VERSION) { $settings['AZURE_CHAT_MODEL_VERSION'] = $env:AZURE_CHAT_MODEL_VERSION }
# azd would otherwise prompt for a subscription (a failure under --no-prompt): pin the one az is using.
$subscriptionId = '<subscription-id>'
if (-not (Test-DryRun)) {
    $subscriptionId = (az account show --query id --output tsv)
    if (-not $subscriptionId) { Stop-Azure 'no active Azure subscription: az login, then az account set --subscription <id>' }
}
$settings['AZURE_SUBSCRIPTION_ID'] = $subscriptionId
if (Test-DryRun) {
    Invoke-Step azd env new $EnvName --location $Location --subscription $subscriptionId --no-prompt
    foreach ($k in $settings.Keys) { Invoke-Step azd env set $k $settings[$k] }
    if ($effectiveSource -eq 'acr') {
        Invoke-Step azd provision --environment $EnvName --no-prompt
        Invoke-Step bash scripts/azure/acr-images.sh $(if ($BuildFromSource) { 'build' } else { 'import' }) --registry '<AZURE_CONTAINER_REGISTRY_NAME>' --tag $ImageTag --stack $Stack --dry-run
        Invoke-Step azd provision --environment $EnvName --no-prompt
    } else {
        Invoke-Step azd provision --environment $EnvName --no-prompt
    }
    exit 0
}

$existing = azd env list --output json | ConvertFrom-Json
if (-not ($existing | Where-Object { $_.Name -eq $EnvName })) { azd env new $EnvName --location $Location --subscription $subscriptionId --no-prompt }
azd env select $EnvName
foreach ($k in $settings.Keys) { azd env set $k $settings[$k] | Out-Null }

# One retry: Key Vault role assignments take a minute to propagate.
function Invoke-Provision([string[]]$AzdArgs) {
    azd @AzdArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "azd $($AzdArgs -join ' ') failed; retrying provision once (Key Vault RBAC propagation is the usual cause)"
        Start-Sleep -Seconds 60
        azd provision --no-prompt
        if ($LASTEXITCODE -ne 0) { Stop-Azure 'azd provision failed twice' }
    }
}
# acr: two provisions (see azure-up.sh). preprovision sets DEPLOY_APPS=false until postprovision has filled the registry.
if ($effectiveSource -eq 'acr') {
    Invoke-Provision @('provision', '--no-prompt')
    if ((azd env get-value DEPLOY_APPS 2>$null) -eq 'false') {
        Write-Log 'registry filled; provisioning again to start the apps'
        Invoke-Provision @('provision', '--no-prompt')
    }
} else {
    Invoke-Provision @('provision', '--no-prompt')
}
