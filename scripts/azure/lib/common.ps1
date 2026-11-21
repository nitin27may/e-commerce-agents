# Shared helpers for scripts/azure/*.ps1 and scripts/azure-{up,down}.ps1. Dot-sourced.
# PowerShell mirror of lib/common.sh, lib/secrets.sh and lib/checks.sh. Not parse-checked in CI
# (no pwsh on the runner that wrote it); the bash scripts are the tested reference.
#
# $env:AZURE_DRY_RUN = '1' (or -DryRun on every entry script) prints commands instead of running them.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Log([string]$Message) { [Console]::Error.WriteLine("[azure] $Message") }
function Write-Warn([string]$Message) { [Console]::Error.WriteLine("[azure] WARN: $Message") }
function Stop-Azure([string]$Message) { [Console]::Error.WriteLine("[azure] ERROR: $Message"); exit 1 }

function Test-DryRun { return $env:AZURE_DRY_RUN -eq '1' }

# Invoke-Step <exe> <args...>: run it, or only print it under dry run. Output is returned.
function Invoke-Step {
    param([Parameter(Mandatory)][string]$Exe, [Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
    if (Test-DryRun) { [Console]::Error.WriteLine("[dry-run] $Exe $($Arguments -join ' ')"); return }
    $output = & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
    return $output
}

function Test-Tool([string]$Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Get-FirstVersion([string]$Text) {
    if ($Text -match '(\d+)\.(\d+)(?:\.(\d+))?') {
        $patch = if ($Matches[3]) { $Matches[3] } else { '0' }
        return [version]"$($Matches[1]).$($Matches[2]).$patch"
    }
    return [version]'0.0.0'
}

# Value of an azd environment variable ('' when unset). AZURE_SECRET_STORE_FILE (dotenv) replaces azd offline.
function Get-AzdValue([string]$Key) {
    if ($env:AZURE_SECRET_STORE_FILE) {
        if (-not (Test-Path $env:AZURE_SECRET_STORE_FILE)) { return '' }
        $line = Select-String -Path $env:AZURE_SECRET_STORE_FILE -Pattern "^$([regex]::Escape($Key))=" | Select-Object -Last 1
        if (-not $line) { return '' }
        return ($line.Line.Substring($Key.Length + 1)).Trim('"')
    }
    $fromProcess = [Environment]::GetEnvironmentVariable($Key)
    if ($fromProcess) { return $fromProcess }
    $value = & azd env get-value $Key 2>$null
    if ($LASTEXITCODE -ne 0) { return '' }
    return "$value"
}

function Set-AzdValue([string]$Key, [string]$Value) {
    if ($env:AZURE_SECRET_STORE_FILE) {
        $existing = if (Test-Path $env:AZURE_SECRET_STORE_FILE) { @(Get-Content $env:AZURE_SECRET_STORE_FILE | Where-Object { $_ -notmatch "^$([regex]::Escape($Key))=" }) } else { @() }
        Set-Content -Path $env:AZURE_SECRET_STORE_FILE -Value ($existing + "$Key=`"$Value`"")
        return
    }
    if (Test-DryRun) { [Console]::Error.WriteLine("[dry-run] azd env set $Key <value>"); return }
    & azd env set $Key $Value | Out-Null
}

# ---- secrets (formats: docs/azure-deployment.md) -----------------------------------------------

function New-Hex64 {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
}

# Letters and digits only, with at least one upper, one lower and one digit.
function New-Alnum([int]$Length) {
    $alphabet = [char[]]'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    while ($true) {
        $chars = for ($i = 0; $i -lt $Length; $i++) { $alphabet[[System.Security.Cryptography.RandomNumberGenerator]::GetInt32($alphabet.Length)] }
        $candidate = -join $chars
        if ($candidate -cmatch '[A-Z]' -and $candidate -cmatch '[a-z]' -and $candidate -match '[0-9]') { return $candidate }
    }
}

# Sets every missing secret in the azd environment, never overwrites, returns the names it generated.
function Initialize-AzureSecrets {
    $generated = @()
    if (-not (Get-AzdValue 'POSTGRES_ADMIN_PASSWORD')) { Set-AzdValue 'POSTGRES_ADMIN_PASSWORD' (New-Alnum 32); $generated += 'POSTGRES_ADMIN_PASSWORD' }
    foreach ($name in 'JWT_SECRET', 'AGENT_SHARED_SECRET', 'A2A_IDENTITY_KEY', 'OAUTH_SEED_KEY', 'AUTH_SIGNING_KEY_ENCRYPTION_KEY') {
        if (Get-AzdValue $name) { continue }
        $value = New-Hex64
        if ($name -eq 'A2A_IDENTITY_KEY') { while ($value -eq (Get-AzdValue 'AGENT_SHARED_SECRET')) { $value = New-Hex64 } }
        Set-AzdValue $name $value
        $generated += $name
    }
    if (-not (Get-AzdValue 'SEED_STAFF_PASSWORD')) { Set-AzdValue 'SEED_STAFF_PASSWORD' (New-Alnum 20); $generated += 'SEED_STAFF_PASSWORD' }
    if ((Get-AzdValue 'A2A_IDENTITY_KEY') -eq (Get-AzdValue 'AGENT_SHARED_SECRET')) {
        Write-Warn 'A2A_IDENTITY_KEY equals AGENT_SHARED_SECRET; A2A_IDENTITY=token will not protect against a caller holding the shared secret.'
    }
    return $generated
}

# ---- region, model, quota, soft-delete checks (shapes unverified against a live subscription) --

function Test-PostgresSku([string]$Location, [string]$Sku) {
    Write-Log "PostgreSQL Flexible Server: SKU $Sku in $Location"
    if (Test-DryRun) { Invoke-Step az postgres flexible-server list-skus --location $Location --output json; return }
    $skus = az postgres flexible-server list-skus --location $Location --output json | ConvertFrom-Json
    if (-not ($skus | Where-Object { $_.name -eq $Sku })) {
        Stop-Azure "PostgreSQL SKU $Sku is not offered in $Location. Offered: $((($skus | Select-Object -First 8).name) -join ', '). Pick another AZURE_LOCATION."
    }
}

function Test-ChatModel([string]$Location, [string]$Model, [string]$Version, [int]$Capacity) {
    Write-Log "Azure OpenAI: $Model $Version (GlobalStandard, ${Capacity}K TPM) in $Location"
    if (Test-DryRun) {
        Invoke-Step az cognitiveservices model list --location $Location --output json
        Invoke-Step az cognitiveservices usage list --location $Location --output json
        return
    }
    $models = az cognitiveservices model list --location $Location --output json | ConvertFrom-Json
    $match = $models | Where-Object { $_.model.name -eq $Model -and $_.model.version -eq $Version -and ($_.model.skus.name -contains 'GlobalStandard') } | Select-Object -First 1
    if (-not $match) { Stop-Azure "$Model $Version (GlobalStandard) is not available in $Location. Set AZURE_OPENAI_LOCATION or AZURE_CHAT_MODEL." }
    if ($match.model.lifecycleStatus -in 'Deprecated', 'Deprecating') { Stop-Azure "$Model $Version is $($match.model.lifecycleStatus) in $Location; new deployments are refused." }
    $usage = az cognitiveservices usage list --location $Location --output json | ConvertFrom-Json
    $row = $usage | Where-Object { $_.name.value -eq "OpenAI.GlobalStandard.$Model" } | Select-Object -First 1
    if (-not $row) { Write-Warn "no quota row for OpenAI.GlobalStandard.$Model in $Location; cannot confirm $Capacity K TPM is free."; return }
    $free = [int]($row.limit - $row.currentValue)
    if ($free -lt $Capacity) { Stop-Azure "quota for ${Model} in ${Location}: $free free of $($row.limit), need $Capacity." }
}

# Returns $true when nothing is left. With -Purge removes leftovers first.
function Test-SoftDeleted([string]$EnvName, [switch]$Purge) {
    $marker = "/resourcegroups/rg-$($EnvName.ToLowerInvariant())/"
    Write-Log "soft-deleted Key Vault / Cognitive Services for $EnvName"
    if (Test-DryRun) {
        Invoke-Step az keyvault list-deleted --output json
        Invoke-Step az cognitiveservices account list-deleted --output json
        return $true
    }
    $clean = $true
    $vaults = @(az keyvault list-deleted --output json | ConvertFrom-Json) | Where-Object { "$($_.properties.vaultId)".ToLowerInvariant().Contains($marker) }
    foreach ($v in $vaults) {
        if ($Purge) { Write-Log "purging Key Vault $($v.name)"; az keyvault purge --name $v.name --location $v.properties.location | Out-Null; if ($LASTEXITCODE -ne 0) { $clean = $false } }
        else { Write-Warn "soft-deleted Key Vault: $($v.name)"; $clean = $false }
    }
    $accounts = @(az cognitiveservices account list-deleted --output json | ConvertFrom-Json) | Where-Object { "$($_.id)".ToLowerInvariant().Contains($marker) }
    foreach ($a in $accounts) {
        $rg = [regex]::Match($a.id, '(?i)resourceGroups/([^/]+)').Groups[1].Value
        if ($Purge) { Write-Log "purging Cognitive Services account $($a.name)"; az cognitiveservices account purge --name $a.name --resource-group $rg --location $a.location | Out-Null; if ($LASTEXITCODE -ne 0) { $clean = $false } }
        else { Write-Warn "soft-deleted Cognitive Services account: $($a.name)"; $clean = $false }
    }
    return $clean
}
