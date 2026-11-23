# Azure deployment prerequisites (PowerShell mirror of prereqs.sh).
#   ./scripts/azure/prereqs.ps1 [-Location eastus2] [-OpenAiLocation eastus2] [-Profile demo] [-EnvName demo1] [-DryRun]
# Installs the containerapp CLI extension and registers missing resource providers; changes nothing else.
[CmdletBinding()]
param(
    [string]$Location = $env:AZURE_LOCATION,
    [string]$OpenAiLocation = $env:AZURE_OPENAI_LOCATION,
    [string]$Profile = $(if ($env:DEPLOY_PROFILE) { $env:DEPLOY_PROFILE } else { 'demo' }),
    [string]$EnvName = $env:AZURE_ENV_NAME,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..' '..')
. (Join-Path $PSScriptRoot 'lib' 'common.ps1')
if ($DryRun) { $env:AZURE_DRY_RUN = '1' }
if (-not $OpenAiLocation) { $OpenAiLocation = $Location }

$minAz = [version]'2.67.0'; $minAzd = [version]'1.18.0'; $minBicep = [version]'0.34.0'
$providers = @('Microsoft.App', 'Microsoft.OperationalInsights', 'Microsoft.Insights', 'Microsoft.CognitiveServices',
    'Microsoft.DBforPostgreSQL', 'Microsoft.KeyVault', 'Microsoft.ManagedIdentity', 'Microsoft.Consumption', 'Microsoft.Authorization', 'Microsoft.ContainerRegistry')
if ($Profile -eq 'reference') { $providers += 'Microsoft.Cache', 'Microsoft.Network' }

foreach ($tool in 'az', 'azd') {
    if (-not (Test-Tool $tool)) {
        if (Test-DryRun) { Write-Warn "$tool is not installed (dry run, continuing)"; continue }
        Stop-Azure "$tool is not installed. az: https://aka.ms/installazurecli  azd: https://aka.ms/azd"
    }
}
if (Test-Tool 'az') {
    $azVersion = Get-FirstVersion (az version --query '"azure-cli"' -o tsv)
    if ($azVersion -lt $minAz) { Stop-Azure "az $minAz or newer is required (found $azVersion)" }
}
if (Test-Tool 'azd') {
    $azdVersion = Get-FirstVersion ((azd version) | Select-Object -First 1)
    if ($azdVersion -lt $minAzd) { Stop-Azure "azd $minAzd or newer is required (found $azdVersion)" }
}
$bicep = if (Test-Tool 'bicep') { 'bicep' } elseif (Test-Path (Join-Path $HOME '.azure' 'bin' 'bicep')) { Join-Path $HOME '.azure' 'bin' 'bicep' } else { $null }
if ($bicep) {
    $bicepVersion = Get-FirstVersion (& $bicep --version)
    if ($bicepVersion -lt $minBicep) { Stop-Azure "bicep $minBicep or newer is required (found $bicepVersion): az bicep upgrade" }
} elseif (-not (Test-DryRun)) { Stop-Azure "the Bicep CLI is missing: az bicep install" }
Write-Log 'tools ok'

if (Test-DryRun) { Invoke-Step az account show } else {
    az account show *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Azure "not logged in: run 'az login' (and 'azd auth login')" }
}
Invoke-Step az extension add --name containerapp --upgrade --only-show-errors

foreach ($p in $providers) {
    if (Test-DryRun) { Invoke-Step az provider register --namespace $p; continue }
    $state = az provider show --namespace $p --query registrationState -o tsv 2>$null
    if ($state -ne 'Registered') { Write-Log "registering $p (was $state)"; az provider register --namespace $p --wait --only-show-errors }
}
Write-Log 'resource providers registered'

if ($env:SKIP_RBAC_CHECK -eq '1') { Write-Warn 'SKIP_RBAC_CHECK=1: not checking role-assignment rights' }
elseif (Test-DryRun) { Invoke-Step az role assignment list --assignee '<signed-in principal>' --include-inherited }
else {
    $subscriptionId = az account show --query id -o tsv
    $principal = az ad signed-in-user show --query id -o tsv 2>$null
    if (-not $principal) { Write-Warn 'cannot resolve the signed-in user; set SKIP_RBAC_CHECK=1 once Owner or User Access Administrator is confirmed' }
    else {
        $roles = @(az role assignment list --assignee $principal --include-inherited --scope "/subscriptions/$subscriptionId" --query '[].roleDefinitionName' -o tsv)
        $ok = ($roles -contains 'Owner') -or (($roles -contains 'Contributor') -and ($roles -contains 'User Access Administrator')) -or ($roles -contains 'Role Based Access Control Administrator')
        if (-not $ok) { Stop-Azure "cannot create role assignments on subscription $subscriptionId (found: $($roles -join ', ')). Needs Owner, or Contributor plus User Access Administrator." }
        Write-Log 'role-assignment rights ok'
    }
}

if (-not $Location) { Write-Log 'no -Location / AZURE_LOCATION: skipping SKU, model, quota checks (preprovision repeats them)'; Write-Log 'prerequisites ok'; exit 0 }
$profileFile = Join-Path 'infra' 'profiles' "$Profile.json"
if (-not (Test-Path $profileFile)) { Stop-Azure "unknown profile $Profile" }
$cfg = Get-Content $profileFile -Raw | ConvertFrom-Json
$model = if ($env:AZURE_CHAT_MODEL) { $env:AZURE_CHAT_MODEL } else { 'gpt-5.1' }
$version = if ($env:AZURE_CHAT_MODEL_VERSION) { $env:AZURE_CHAT_MODEL_VERSION } else { '2025-11-13' }
Test-PostgresSku $Location $cfg.postgres.skuName
Test-ChatModel $OpenAiLocation $model $version $cfg.openAi.chatCapacity
if ($EnvName -and -not (Test-SoftDeleted $EnvName)) { Stop-Azure "soft-deleted resources from a previous '$EnvName' deployment hold their names: ./scripts/azure-down.ps1 -EnvName $EnvName -PurgeOnly" }
Write-Log 'prerequisites ok'
