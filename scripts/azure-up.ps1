# One command to an Azure deployment (PowerShell mirror of azure-up.sh).
#   ./scripts/azure-up.ps1 -EnvName demo1 -Location eastus2 [-ImageTag v1.4.0] [-Profile demo|reference]
#                          [-OpenAiLocation eastus2] [-PublicOpen] [-AllowedIps 198.51.100.0/24] [-AuthServer] [-Mcp] [-DryRun]
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
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
. (Join-Path $PSScriptRoot 'azure' 'lib' 'common.ps1')
if ($DryRun) { $env:AZURE_DRY_RUN = '1' }

if ($EnvName -cnotmatch '^[a-z0-9][a-z0-9-]{0,30}$') { Stop-Azure '-EnvName must be lowercase letters, digits and dashes (max 31)' }
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
}
if (Test-DryRun) {
    Invoke-Step azd env new $EnvName --location $Location --no-prompt
    foreach ($k in $settings.Keys) { Invoke-Step azd env set $k $settings[$k] }
    Invoke-Step azd up --environment $EnvName --no-prompt
    exit 0
}

$existing = azd env list --output json | ConvertFrom-Json
if (-not ($existing | Where-Object { $_.Name -eq $EnvName })) { azd env new $EnvName --location $Location --no-prompt }
azd env select $EnvName
foreach ($k in $settings.Keys) { azd env set $k $settings[$k] | Out-Null }

# One retry: Key Vault role assignments take a minute to propagate.
azd up --no-prompt
if ($LASTEXITCODE -ne 0) {
    Write-Warn 'azd up failed; retrying provision once (Key Vault RBAC propagation is the usual cause)'
    Start-Sleep -Seconds 60
    azd provision --no-prompt
    if ($LASTEXITCODE -ne 0) { Stop-Azure 'azd provision failed twice' }
}
