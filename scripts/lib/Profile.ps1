# Run profiles for the PowerShell scripts. Dot-sourced by scripts/dev.ps1; not run directly.
#
# The twin of scripts/lib/profile.sh (same rules, same precedence) and agents/python/shared/profiles.py.
# A profile is agents/python/config/profiles/<name>.env: KEY=VALUE overlays for chat, embeddings and the model,
# never a secret. Precedence, high to low: the process environment > the profile > .env > defaults.
# A value of <set-me> means "supply this": the profile leaves the environment and .env alone for it, and the run
# is refused when neither has one.
#
# NOTE: written without a PowerShell interpreter at hand (pwsh was not available when this was added); it mirrors
# profile.sh line for line and has not been parsed or run. Treat the first run on Windows as its test.

$script:ProfilePlaceholder = '<set-me>'
$script:ProfileSecretNameRegex = 'KEY|SECRET|TOKEN|PASSWORD'

function Get-ProfileDirectory {
    param([string]$RepoRoot)
    return (Join-Path $RepoRoot 'agents/python/config/profiles')
}

function Get-ProfileNames {
    param([string]$RepoRoot)
    $dir = Get-ProfileDirectory -RepoRoot $RepoRoot
    if (-not (Test-Path $dir)) { return @() }
    return @(Get-ChildItem -Path $dir -Filter '*.env' | ForEach-Object { $_.BaseName } | Sort-Object)
}

# The strict pattern is checked first, so a name such as ..\x never reaches the filesystem.
function Test-ProfileName {
    param([string]$RepoRoot, [string]$Name)
    if ($Name -cnotmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { return $false }
    return (Test-Path (Join-Path (Get-ProfileDirectory -RepoRoot $RepoRoot) "$Name.env"))
}

function Get-ProfileMeta {
    param([string]$RepoRoot, [string]$Name, [string]$Field)
    $file = Join-Path (Get-ProfileDirectory -RepoRoot $RepoRoot) "$Name.env"
    $line = Get-Content $file | Where-Object { $_ -match "^# @$($Field):" } | Select-Object -First 1
    if (-not $line) { return '' }
    return ($line -replace "^# @$($Field):\s*", '')
}

function Get-DotenvValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path $Path)) { return $null }
    $line = Get-Content $Path | Where-Object { $_ -match "^$Name=" } | Select-Object -Last 1
    if ($null -eq $line) { return $null }
    return $line.Substring($Name.Length + 1).Trim().Trim('"').Trim("'")
}

# Sets the profile's variables the process environment has not set. -ForContainers rewrites a *_BASE_URL on
# localhost / 127.0.0.1 to host.docker.internal. Returns an object with Applied, Kept, Overrides, Unresolved.
function Use-RunProfile {
    param([string]$RepoRoot, [string]$Name, [switch]$ForContainers)
    $result = [pscustomobject]@{ Applied = @(); Kept = @(); Overrides = @(); Unresolved = @() }
    $file = Join-Path (Get-ProfileDirectory -RepoRoot $RepoRoot) "$Name.env"
    $dotenv = Join-Path $RepoRoot '.env'
    foreach ($line in (Get-Content $file)) {
        if ($line -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') { continue }
        $key = $Matches[1]
        $value = $Matches[2].TrimEnd("`r")
        $current = [Environment]::GetEnvironmentVariable($key)
        $isSet = $null -ne $current
        if ($value -eq $script:ProfilePlaceholder) {
            $fromDotenv = Get-DotenvValue -Path $dotenv -Name $key
            if ($isSet -or $fromDotenv) { continue }
            $result.Unresolved += $key
            continue
        }
        if ($isSet) { $result.Kept += $key; continue }
        if ($ForContainers -and $key.EndsWith('_BASE_URL')) {
            $value = $value -replace '^(https?://)(localhost|127\.0\.0\.1)(?=[:/]|$)', '${1}host.docker.internal'
        }
        $previous = Get-DotenvValue -Path $dotenv -Name $key
        if ($null -ne $previous -and $previous -ne $value) {
            if ($key -match $script:ProfileSecretNameRegex) {
                $result.Overrides += $key
            } else {
                $result.Overrides += "$key (.env: $previous -> $value)"
            }
        }
        [Environment]::SetEnvironmentVariable($key, $value)
        $result.Applied += $key
    }
    [Environment]::SetEnvironmentVariable('RUN_PROFILE', $Name)
    return $result
}
