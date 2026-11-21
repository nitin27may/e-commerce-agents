# Remove an Azure deployment and prove nothing is left (PowerShell mirror of azure-down.sh).
#   ./scripts/azure-down.ps1 -EnvName demo1 [-DryRun] [-PurgeOnly]
# azd down --purge --force, then no soft-deleted Key Vault / Cognitive Services account for rg-<env>, then
# `az group exists` must be false. Exits 1 if anything remains.
[CmdletBinding()]
param([string]$EnvName = $env:AZURE_ENV_NAME, [switch]$PurgeOnly, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
. (Join-Path $PSScriptRoot 'azure' 'lib' 'common.ps1')
if ($DryRun) { $env:AZURE_DRY_RUN = '1' }
if (-not $EnvName) { Stop-Azure '-EnvName is required' }
$resourceGroup = "rg-$EnvName"

if (-not $PurgeOnly) {
    Write-Log "azd down --purge --force ($EnvName)"
    Invoke-Step azd down --environment $EnvName --purge --force --no-prompt
}

$clean = $true
if (-not (Test-SoftDeleted $EnvName)) {
    Write-Log 'leftovers found; purging'
    [void](Test-SoftDeleted $EnvName -Purge)
    if (-not (Test-SoftDeleted $EnvName)) { Write-Warn 'soft-deleted resources remain after the purge; purge them by hand'; $clean = $false }
}
if (Test-DryRun) { Invoke-Step az group exists --name $resourceGroup; Write-Log 'dry run complete'; exit 0 }
if ((az group exists --name $resourceGroup) -eq 'true') { Write-Warn "resource group $resourceGroup still exists"; $clean = $false }
if ($clean) { Write-Log "clean: no resource group, no soft-deleted Key Vault or Cognitive Services account for $EnvName" }
else { Stop-Azure 'teardown incomplete (see the warnings above)' }
