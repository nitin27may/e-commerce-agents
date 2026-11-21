# azd preprovision hook (PowerShell mirror of preprovision.sh): secrets, image tag, deployer IP, region checks.
#   AZURE_SECRET_STORE_FILE=./x.env ./scripts/azure/preprovision.ps1 -SecretsOnly     # offline
[CmdletBinding()]
param([switch]$SecretsOnly, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..' '..')
. (Join-Path $PSScriptRoot 'lib' 'common.ps1')
if ($DryRun) { $env:AZURE_DRY_RUN = '1' }

$generated = @(Initialize-AzureSecrets)
if ($generated.Count -gt 0) { Write-Log "generated for this environment: $($generated -join ' ')" } else { Write-Log 'secrets already present; none rewritten' }
if ($SecretsOnly) { exit 0 }

$imageTag = Get-AzdValue 'IMAGE_TAG'
if (-not $imageTag) { Stop-Azure 'IMAGE_TAG is not set. Pin a published tag: azd env set IMAGE_TAG v1.4.0 (or sha-<7>; see docs/releasing.md).' }
if ($imageTag -in 'latest', 'main') { Stop-Azure "IMAGE_TAG=$imageTag floats. Use a release tag (vX.Y.Z) or sha-<7>." }

$publicAccess = Get-AzdValue 'PUBLIC_ACCESS'
if (-not $publicAccess) { $publicAccess = 'restricted' }
if ($publicAccess -eq 'open') {
    Write-Warn 'PUBLIC_ACCESS=open: anyone can reach the frontend and spend your model quota. Budget mode stays enforce.'
} else {
    $ip = Get-AzdValue 'DEPLOYER_IP'
    if (-not $ip -or (Get-AzdValue 'DEPLOYER_IP_PINNED') -ne 'true') {
        $detected = ''
        foreach ($url in 'https://api.ipify.org', 'https://checkip.amazonaws.com') {
            try { $candidate = (Invoke-RestMethod -Uri $url -TimeoutSec 8).ToString().Trim() } catch { $candidate = '' }
            if ($candidate -match '^(\d{1,3}\.){3}\d{1,3}$') { $detected = $candidate; break }
        }
        if ($detected) { $ip = $detected }
        elseif (-not $ip) { Stop-Azure "could not detect this machine's public IPv4 and DEPLOYER_IP is unset: azd env set DEPLOYER_IP <a.b.c.d>" }
        else { Write-Warn "could not re-detect the public IP; keeping DEPLOYER_IP=$ip" }
        Set-AzdValue 'DEPLOYER_IP' $ip
    }
    Write-Log "frontend ingress allows $ip/32 and ALLOWED_IPS"
}

$location = Get-AzdValue 'AZURE_LOCATION'
if (-not $location) { Stop-Azure 'AZURE_LOCATION is not set (azd env set AZURE_LOCATION eastus2)' }
$openAiLocation = Get-AzdValue 'AZURE_OPENAI_LOCATION'; if (-not $openAiLocation) { $openAiLocation = $location }
$profileName = Get-AzdValue 'DEPLOY_PROFILE'; if (-not $profileName) { $profileName = 'demo' }
$profileFile = Join-Path 'infra' 'profiles' "$profileName.json"
if (-not (Test-Path $profileFile)) { Stop-Azure "unknown DEPLOY_PROFILE=$profileName" }
$cfg = Get-Content $profileFile -Raw | ConvertFrom-Json
$model = Get-AzdValue 'AZURE_CHAT_MODEL'; if (-not $model) { $model = 'gpt-5.1' }
$version = Get-AzdValue 'AZURE_CHAT_MODEL_VERSION'; if (-not $version) { $version = '2025-11-13' }
Test-PostgresSku $location $cfg.postgres.skuName
Test-ChatModel $openAiLocation $model $version $cfg.openAi.chatCapacity
$envName = Get-AzdValue 'AZURE_ENV_NAME'
if ($envName -and -not (Test-SoftDeleted $envName)) { Stop-Azure "soft-deleted resources from an earlier '$envName' deployment still hold their names: ./scripts/azure-down.ps1 -EnvName $envName -PurgeOnly" }
Write-Log 'preprovision ok'
