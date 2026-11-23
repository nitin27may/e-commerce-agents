# azd postprovision hook (PowerShell mirror of postprovision.sh): run job-db-init, print outputs, smoke test.
[CmdletBinding()]
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..' '..')
. (Join-Path $PSScriptRoot 'lib' 'common.ps1')
if ($DryRun) { $env:AZURE_DRY_RUN = '1' }

$timeout = if ($env:JOB_TIMEOUT_SECONDS) { [int]$env:JOB_TIMEOUT_SECONDS } else { 1200 }
$poll = if ($env:POLL_SECONDS) { [int]$env:POLL_SECONDS } else { 10 }
$resourceGroup = Get-AzdValue 'AZURE_RESOURCE_GROUP'

# Images into ACR (image source acr only; see postprovision.sh). acr-images.sh is bash: WSL or Git Bash.
$registry = Get-AzdValue 'AZURE_CONTAINER_REGISTRY_NAME'
if (Test-DryRun -and (Get-AzdValue 'IMAGE_SOURCE') -eq 'acr' -and -not $registry) { $registry = 'acr-example' }
if ($registry) {
    if (-not (Test-DryRun) -and -not (Test-Tool 'bash')) { Stop-Azure 'bash is required to fill the registry (scripts/azure/acr-images.sh): run it from WSL or Git Bash' }
    $mode = if ((Get-AzdValue 'BUILD_FROM_SOURCE') -eq 'true') { 'build' } else { 'import' }
    $stackName = Get-AzdValue 'DEPLOY_STACK'; if (-not $stackName) { $stackName = 'python' }
    $acrArgs = @("scripts/azure/acr-images.sh", $mode, '--registry', $registry, '--tag', (Get-AzdValue 'IMAGE_TAG'), '--stack', $stackName)
    if (Test-DryRun) { $acrArgs += '--dry-run' }
    bash @acrArgs
    if ($LASTEXITCODE -ne 0) { Stop-Azure 'filling the registry failed' }
    Set-AzdValue 'ACR_IMAGES_TAG' (Get-AzdValue 'IMAGE_TAG')
    if ((Get-AzdValue 'DEPLOY_APPS') -eq 'false') {
        Write-Log "registry holds the images. Run 'azd provision' again to start the apps (azure-up.ps1 does this for you)."
        exit 0
    }
}
$jobName = Get-AzdValue 'DB_INIT_JOB_NAME'
$frontendUrl = Get-AzdValue 'FRONTEND_URL'
if (Test-DryRun) {
    if (-not $resourceGroup) { $resourceGroup = 'rg-example' }
    if (-not $jobName) { $jobName = 'job-db-init' }
    if (-not $frontendUrl) { $frontendUrl = 'https://ca-frontend.example' }
}
if (-not $resourceGroup -or -not $jobName) { Stop-Azure 'AZURE_RESOURCE_GROUP / DB_INIT_JOB_NAME missing from the azd environment: did provisioning finish?' }

Write-Log "starting $jobName"
$execution = Invoke-Step az containerapp job start --name $jobName --resource-group $resourceGroup --query name --output tsv
if (Test-DryRun) {
    Invoke-Step az containerapp job execution show --name $jobName --resource-group $resourceGroup --job-execution-name '<execution>'
} else {
    if (-not $execution) { Stop-Azure 'az containerapp job start returned no execution name' }
    Write-Log "execution $execution"
    $deadline = (Get-Date).AddSeconds($timeout)
    while ($true) {
        $status = az containerapp job execution show --name $jobName --resource-group $resourceGroup --job-execution-name $execution --query properties.status --output tsv 2>$null
        if (-not $status) { $status = 'Unknown' }
        if ($status -eq 'Succeeded') { Write-Log 'db-init succeeded'; break }
        if ($status -in 'Failed', 'Stopped', 'Degraded') {
            Write-Log "db-init $status. Logs (may lag a minute or two):"
            Write-Log "  az containerapp job logs show --name $jobName --resource-group $resourceGroup --execution $execution --container db-init --follow false"
            Stop-Azure "database bootstrap failed (status $status). Re-run: az containerapp job start --name $jobName --resource-group $resourceGroup"
        }
        if ((Get-Date) -gt $deadline) { Stop-Azure "db-init still '$status' after ${timeout}s" }
        Start-Sleep -Seconds $poll
    }
}

$adminEmail = Get-AzdValue 'ADMIN_LOGIN_EMAIL'; if (-not $adminEmail) { $adminEmail = 'admin.demo@gmail.com' }
Write-Log ''
Write-Log "  Frontend           : $frontendUrl"
Write-Log "  Admin login        : $adminEmail"
Write-Log '  Staff password     : azd env get-value SEED_STAFF_PASSWORD   (admin, support and seller share it)'
Write-Log '  Customer demo user : alice.johnson@gmail.com / customer123 (published demo password)'
Write-Log "  Resource group     : $resourceGroup"
Write-Log '  Tear down          : ./scripts/azure-down.ps1'

if ($env:SKIP_SMOKE -eq '1') { Write-Log 'SKIP_SMOKE=1: not running the smoke test'; exit 0 }
if (-not (Test-Path 'scripts/azure/smoke.sh')) { Write-Warn 'scripts/azure/smoke.sh not found; skipping'; exit 0 }
if (Test-DryRun) { Invoke-Step bash scripts/azure/smoke.sh --quick; exit 0 }
if (-not (Test-Tool 'bash')) { Write-Warn 'bash is not available; run the smoke test from WSL or Git Bash: scripts/azure/smoke.sh --quick'; exit 0 }
$env:FRONTEND_URL = $frontendUrl; $env:AZURE_RESOURCE_GROUP = $resourceGroup; $env:DB_INIT_JOB_NAME = $jobName
if (-not $env:E2E_STAFF_PASSWORD) { $env:E2E_STAFF_PASSWORD = Get-AzdValue 'SEED_STAFF_PASSWORD' }
bash scripts/azure/smoke.sh --quick
if ($LASTEXITCODE -ne 0) { Stop-Azure 'smoke test failed' }
