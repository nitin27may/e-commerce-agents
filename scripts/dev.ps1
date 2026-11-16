#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Start the whole E-Commerce Agents stack — the PowerShell twin of dev.sh.

.DESCRIPTION
    Behaviourally identical to scripts/dev.sh: same profiles, same ordering,
    same health gates, same flags. It exists because dev.sh is bash, so it
    cannot run in PowerShell or cmd — and Windows was the one platform with no
    first-class way to start this project.

    Not Windows-only, though. PowerShell 7 is cross-platform, so this runs on
    macOS and Linux too; on those platforms dev.sh remains the more idiomatic
    choice and the two are interchangeable.

    Every step here is a plain `docker compose` invocation. If this script ever
    misbehaves, the commands in docs/quick-start.md do the same work by hand.

.PARAMETER Clean
    Remove containers and volumes, then rebuild without cache.

.PARAMETER SeedOnly
    Re-run the seeder against an existing database and exit.

.PARAMETER InfraOnly
    Start db + redis + aspire only; do not start agents or the frontend.

.PARAMETER Dotnet
    Target the .NET backend (docker-compose.dotnet.yml) instead of Python.

.PARAMETER Demo
    Pull prebuilt images from GHCR instead of building. The fast path: no local
    build, one command. Override the tag with $env:IMAGE_TAG (default: latest).

.EXAMPLE
    ./scripts/dev.ps1
    ./scripts/dev.ps1 -Clean
    ./scripts/dev.ps1 -Dotnet
    ./scripts/dev.ps1 -Demo
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SeedOnly,
    [switch]$InfraOnly,
    [switch]$Dotnet,
    [switch]$Demo,
    # The VARIABLE cannot be $Switch — that is a reserved automatic variable in
    # PowerShell (the switch-statement enumerator), and shadowing it is a real
    # bug waiting. The alias keeps the user-facing flag matching dev.sh's
    # --switch, which is what the repo convention promises.
    [Alias('Switch')]
    [switch]$SwitchStack
)

# Stop on the first unhandled error. Native commands don't trip this on their
# own, so exit codes are checked explicitly via Invoke-Compose below.
$ErrorActionPreference = 'Stop'

function Write-Step    { param([string]$Message) Write-Host "`n── $Message ──`n" -ForegroundColor Cyan }
function Write-Info    { param([string]$Message) Write-Host "[INFO]  $Message" -ForegroundColor Blue }
function Write-Ok      { param([string]$Message) Write-Host "[OK]    $Message" -ForegroundColor Green }
function Write-Warn    { param([string]$Message) Write-Host "[WARN]  $Message" -ForegroundColor Yellow }
function Write-Err     { param([string]$Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }

# ── Local secrets ────────────────────────────────────────────
# Mirrors scripts/lib/env-secrets.sh. Keep the placeholder list in step with
# _UNSAFE_SECRET_DEFAULTS (agents/python/shared/config.py) and UnsafeDefaults
# (AgentSettingsValidator.cs), plus the empty string.
$SecretVars = @('JWT_SECRET', 'AGENT_SHARED_SECRET', 'OAUTH_SEED_KEY')
$SecretPlaceholders = @(
    '',
    'change-me-in-production',
    'change-me-generate-a-random-256-bit-key',
    'change-me-in-production-use-random-256-bit-key',
    'agent-internal-secret',
    'agent-internal-shared-secret',
    'dev-oauth-seed-change-me'
)

# 64 hex chars (256 bits).
function New-Secret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

# Replaces a missing, empty or known-placeholder JWT_SECRET / AGENT_SHARED_SECRET /
# OAUTH_SEED_KEY in the env file. Anything the user already set is left alone.
# Returns the names it generated.
function Set-LocalSecrets {
    param([string]$Path)
    $text = [System.IO.File]::ReadAllText((Resolve-Path $Path).Path)
    $generated = @()
    foreach ($name in $SecretVars) {
        $matches_ = [regex]::Matches($text, "(?m)^$name=(.*)$")
        if ($matches_.Count -gt 0) {
            $value = $matches_[$matches_.Count - 1].Groups[1].Value.Trim().Trim('"').Trim("'")
            if ($SecretPlaceholders -notcontains $value) { continue }
            $text = [regex]::Replace($text, "(?m)^$name=.*?(\r?)$", "$name=$(New-Secret)`$1")
        } else {
            if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $text += "`n" }
            $text += "$name=$(New-Secret)`n"
        }
        $generated += $name
    }
    if ($generated.Count -gt 0) {
        [System.IO.File]::WriteAllText((Resolve-Path $Path).Path, $text, (New-Object System.Text.UTF8Encoding($false)))
    }
    return , $generated
}

# ── Stack selection ──────────────────────────────────────────
# Mirrors dev.sh's COMPOSE / APP_PROFILES / RUN_PROFILES split. Both compose
# files gate agents, seeder and frontend behind profiles; only db, redis and
# aspire are unconditional.
#   AppProfiles — down/build: every profile-gated service, seed included
#   RunProfiles — final `up -d`: agents + frontend, seed excluded because it
#                 already ran as its own one-shot `run --rm` step

if ($Dotnet) {
    $ComposeArgs        = @('compose', '-f', 'docker-compose.dotnet.yml')
    $AppProfiles        = @('--profile', 'seed', '--profile', 'agents', '--profile', 'mcp', '--profile', 'frontend')
    $RunProfiles        = @('--profile', 'agents', '--profile', 'mcp', '--profile', 'frontend')
    $PgDataVolumeRegex  = 'pgdata-dotnet$'
    $StackName          = '.NET'
    $OtherStackProject  = 'e-commerce-agents'
    $OtherStackName     = 'Python'
    $OtherStackFile     = 'docker-compose.yml'
} else {
    $ComposeArgs        = @('compose')
    $AppProfiles        = @('--profile', 'seed', '--profile', 'agents', '--profile', 'frontend')
    $RunProfiles        = @('--profile', 'agents', '--profile', 'frontend')
    $PgDataVolumeRegex  = '_pgdata$'
    $StackName          = 'Python'
    $OtherStackProject  = 'e-commerce-agents-dotnet'
    $OtherStackName     = '.NET'
    $OtherStackFile     = 'docker-compose.dotnet.yml'
}

function Assert-SingleStack {
    <#
        .SYNOPSIS
        Refuses to start while the other stack is running.

        .DESCRIPTION
        The Python and .NET stacks publish the same host ports, so they cannot
        run at once. That is deliberate: running both would need a second
        published port set, and every host-published port here is already
        claimed by whichever stack came up first.

        What is worth fixing is the failure mode. Without this check the second
        stack fails partway through on a raw Docker port-binding error, after
        some containers have already started — a half-up mess that neither
        stack's `down` fully cleans.

        Compares compose project labels rather than probing ports: a busy port
        says nothing about what is holding it.
    #>
    $running = @(& docker ps --filter "label=com.docker.compose.project=$OtherStackProject" --format '{{.Names}}' 2>$null | Where-Object { $_ })
    if ($running.Count -eq 0) { return }

    if ($SwitchStack) {
        Write-Step "Switching stacks — tearing down the $OtherStackName stack"
        & docker compose -f $OtherStackFile --profile seed --profile agents --profile mcp --profile frontend down -v --remove-orphans *> $null
        Write-Ok "$OtherStackName stack removed, volumes included"
        return
    }

    Write-Host ''
    Write-Err "The $OtherStackName stack is already running ($($running.Count) containers)."
    Write-Host ''
    Write-Host '  Both stacks publish the same ports, so only one runs at a time.'
    Write-Host ''
    Write-Host '  Switch cleanly — brings the other down, volumes and all:'
    Write-Host ''
    Write-Host "    ./scripts/dev.ps1 -SwitchStack"
    Write-Host ''
    Write-Host '  Or do it by hand:'
    Write-Host ''
    Write-Host "    docker compose -f $OtherStackFile --profile agents --profile frontend down -v"
    Write-Host ''
    exit 1
}

function Invoke-Compose {
    <#
        Runs `docker compose ...` and throws on a non-zero exit unless -AllowFailure.
        PowerShell does not fail a pipeline on a native command's exit code, so
        without this check a failed build would silently continue to the next step.
    #>
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$AllowFailure,
        [switch]$Quiet
    )
    $all = $ComposeArgs + $Arguments
    if ($Quiet) {
        & docker @all *> $null
    } else {
        & docker @all
    }
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        throw "docker $($all -join ' ') failed with exit code $LASTEXITCODE"
    }
    # Deliberately returns nothing. An uncaptured value here would be written to
    # the success stream and print as a bare exit code between steps — caught by
    # running this for real, where a stray "0" appeared above "Seeder complete".
}

function Wait-ForHealth {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$Probe,
        [int]$TimeoutSeconds = 60
    )
    Write-Info "Waiting for $Name..."
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        & docker @($ComposeArgs + $Probe) *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "$Name is ready"
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "$Name did not become ready within ${TimeoutSeconds}s"
}

function Wait-ForHttp {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Url,
        [int]$TimeoutSeconds = 60
    )
    Write-Info "Waiting for $Name..."
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            # -UseBasicParsing keeps this working on Windows PowerShell 5.1,
            # where Invoke-WebRequest otherwise needs Internet Explorer's engine.
            $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            Write-Ok "$Name is ready"
            return
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    throw "$Name did not respond at $Url within ${TimeoutSeconds}s"
}

function Repair-StaleDatabaseVolume {
    <#
        A pgdata volume left over from an older run keeps its original password,
        so the container starts healthy but every connection is rejected. dev.sh
        carries the same recovery; without it the seeder fails with an
        authentication error that looks like a config problem.
    #>
    $probe = @('exec', '-T', 'db', 'sh', '-c',
               'PGPASSWORD=ecommerce_secret psql -h 127.0.0.1 -U ecommerce -d ecommerce_agents -c "SELECT 1"')
    & docker @($ComposeArgs + $probe) *> $null
    if ($LASTEXITCODE -eq 0) { return }

    Write-Warn 'Database auth failed — stale Docker volume detected. Reinitializing...'
    Invoke-Compose -Arguments @('stop', 'db') -AllowFailure -Quiet
    Invoke-Compose -Arguments @('rm', '-f', 'db') -AllowFailure -Quiet

    $volumes = (& docker volume ls -q) | Where-Object { $_ -match $PgDataVolumeRegex }
    foreach ($volume in $volumes) { & docker volume rm $volume *> $null }

    Invoke-Compose -Arguments @('up', '-d', 'db')
    Wait-ForHealth -Name 'PostgreSQL' -TimeoutSeconds 60 -Probe @(
        'exec', 'db', 'pg_isready', '-h', '127.0.0.1', '-U', 'ecommerce', '-d', 'ecommerce_agents')
    Write-Ok 'Database reinitialized with correct credentials'
}

function Invoke-Migrations {
    <#
        .SYNOPSIS
        Brings the SCHEMA up to date, not just the credentials.

        .DESCRIPTION
        A volume can authenticate perfectly and still carry a schema from before
        the last change; nothing errors, search just quietly returns nothing.
        Schema changes are numbered migrations now (docker/postgres/migrations,
        applied by scripts/migrate.py on top of the frozen init.sql baseline), so
        an old volume is upgraded in place, keeping its data. The seeder runs the
        same step again (idempotent) before it seeds.
    #>
    Write-Step 'Applying database migrations'
    try {
        Invoke-Compose -Arguments @('--profile', 'seed', 'run', '--rm', 'seeder',
                                    'uv', 'run', '--no-project', 'python', '-m', 'scripts.migrate')
    } catch {
        Write-Host ''
        Write-Warn 'Schema migration failed — see the output above.'
        Write-Host ''
        Write-Host '  If the volume is disposable, recreate it (loses local data, reseeds):'
        Write-Host '      ./scripts/dev.ps1 -Clean'
        Write-Host ''
        exit 1
    }
}

function Show-Summary {
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host "  E-Commerce Agents — $StackName stack" -ForegroundColor Cyan
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Web app          http://localhost:3000'
    Write-Host '  Orchestrator     http://localhost:8080'
    Write-Host '  Aspire (traces)  http://localhost:18888'
    Write-Host ''
    Write-Host '  Sign in as       alice.johnson@gmail.com / customer123'
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ''
}

# ── Run from the repository root, wherever the script was invoked from ──
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot
try {
    # ── Prerequisites ────────────────────────────────────────
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Err 'Docker is not installed or not on PATH.'
        Write-Err 'On Windows, install Docker Desktop and make sure it is running.'
        exit 1
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Err 'Docker Compose v2 is required (the `docker compose` subcommand, not `docker-compose`).'
        exit 1
    }

    # ── .env ─────────────────────────────────────────────────
    if (-not (Test-Path '.env')) {
        if (Test-Path '.env.example') {
            Copy-Item '.env.example' '.env'
            Write-Warn 'Created .env from .env.example — edit it and add your API key before the agents will answer.'
        } else {
            Write-Err 'No .env or .env.example found. Create a .env file first.'
            exit 1
        }
    }

    # A fresh .env carries the public placeholders from .env.example. Replace
    # those (and only those) with random values; secrets already set are left
    # alone. Mirrors ensure_local_secrets in scripts/lib/env-secrets.sh.
    $generated = Set-LocalSecrets -Path '.env'
    if ($generated.Count -gt 0) {
        Write-Info "Generated local secrets in .env: $($generated -join ' ')"
        Write-Info 'Tokens issued before this run are no longer valid; sign in again.'
    }

    # ── LLM key preflight ────────────────────────────────────
    # Mirrors dev.sh: openai/azure with no key means every answer fails, so stop
    # before building or touching volumes. -InfraOnly and -SeedOnly call no model.
    # The process environment wins, then the last NAME= line of .env.
    function Get-EnvValue {
        param([string]$Name)
        $value = [Environment]::GetEnvironmentVariable($Name)
        if (-not $value) {
            $line = Get-Content '.env' | Where-Object { $_ -match "^$Name=" } | Select-Object -Last 1
            if ($line) { $value = $line.Substring($Name.Length + 1) }
        }
        if (-not $value) { return '' }
        return $value.Trim().Trim('"').Trim("'")
    }
    function Test-LlmKey {
        param([string]$Value)
        return ($Value -ne '') -and ($Value -ne 'sk-your-openai-api-key-here')
    }
    if (-not $InfraOnly -and -not $SeedOnly) {
        $provider = (Get-EnvValue 'LLM_PROVIDER').ToLower()
        if (-not $provider) { $provider = 'openai' }
        $keyMissing = $false
        if ($provider -eq 'openai') {
            $keyMissing = -not (Test-LlmKey (Get-EnvValue 'OPENAI_API_KEY'))
        } elseif ($provider -eq 'azure') {
            $keyMissing = -not ((Test-LlmKey (Get-EnvValue 'AZURE_OPENAI_KEY')) -or (Test-LlmKey (Get-EnvValue 'AZURE_OPENAI_API_KEY')))
        }
        if ($keyMissing) {
            Write-Err "LLM_PROVIDER=$provider but no API key is set, so every answer would fail."
            Write-Host ''
            Write-Host '  Two ways forward:'
            Write-Host '    1. Add your key to .env (OPENAI_API_KEY, or AZURE_OPENAI_KEY for LLM_PROVIDER=azure),'
            Write-Host '       then run this again.'
            Write-Host '    2. Set LLM_PROVIDER=replay in .env to play back recorded model responses with no key.'
            Write-Host '       Only the recorded demo prompts answer; anything else says it was not recorded.'
            Write-Host ''
            exit 1
        }
    }

    # ── Clean ────────────────────────────────────────────────
    if ($Clean) {
        Write-Step 'Cleaning up (removing containers, volumes, orphans)'
        Invoke-Compose -Arguments ($AppProfiles + @('down', '-v', '--remove-orphans')) -AllowFailure
        # Aspire holds telemetry in memory only; removing the container clears it.
        Invoke-Compose -Arguments @('rm', '-f', 'aspire') -AllowFailure -Quiet
        Write-Ok 'Clean complete — containers, volumes and Aspire telemetry cleared'
    }

    # ── Demo mode ────────────────────────────────────────────
    # Mirrors dev.sh's --demo: a self-contained fast path with its own exit
    # rather than conditionals threaded through the build/seed/up flow. There
    # is no build step, docker-compose.demo.yml carries no profiles, and its
    # depends_on: service_completed_successfully lets compose sequence the
    # seeder itself.
    if ($Demo) {
        $tag = if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { 'latest' }
        $env:IMAGE_TAG = $tag
        Write-Step "Demo mode — pulling prebuilt images (tag: $tag)"
        Write-Info 'No local build. Images come from ghcr.io/nitin27may/e-commerce-agents'

        & docker compose -f docker-compose.demo.yml pull
        if ($LASTEXITCODE -ne 0) {
            Write-Err 'Could not pull one or more images.'
            Write-Host ''
            Write-Host '  Most likely causes:'
            Write-Host '    - The tag does not exist yet.'
            Write-Host '    - A package is still private. Anonymous pulls fail with an auth'
            Write-Host '      error that reads like a network problem — see docs/releasing.md.'
            Write-Host ''
            Write-Host '  To build from source instead, drop the -Demo switch:'
            Write-Host '    ./scripts/dev.ps1'
            exit 1
        }
        Write-Ok 'Images pulled'

        Write-Step 'Starting the stack'
        & docker compose -f docker-compose.demo.yml up -d
        if ($LASTEXITCODE -ne 0) { Write-Err 'Stack failed to start'; exit 1 }

        Wait-ForHttp -Name 'Orchestrator' -Url 'http://localhost:8080/health' -TimeoutSeconds 120
        Wait-ForHttp -Name 'Frontend' -Url 'http://localhost:3000' -TimeoutSeconds 120

        Show-Summary
        Write-Host '  Sign in as alice.johnson@gmail.com / customer123' -ForegroundColor White
        Write-Host ''
        exit 0
    }

    # ── Seed only ────────────────────────────────────────────
    if ($SeedOnly) {
        Write-Step 'Running seeder'
        Assert-SingleStack
        Invoke-Compose -Arguments @('up', '-d', 'db', 'redis', 'aspire')
        Wait-ForHealth -Name 'PostgreSQL' -TimeoutSeconds 60 -Probe @(
            'exec', 'db', 'pg_isready', '-h', '127.0.0.1', '-U', 'ecommerce', '-d', 'ecommerce_agents')
        Wait-ForHealth -Name 'Redis' -Probe @('exec', 'redis', 'redis-cli', 'ping')
        Repair-StaleDatabaseVolume
        Invoke-Migrations
        Invoke-Compose -Arguments @('--profile', 'seed', 'run', '--rm', 'seeder')
        Write-Ok 'Seeder complete'
        exit 0
    }

    Assert-SingleStack

    # ── Stop existing ────────────────────────────────────────
    Write-Step 'Stopping existing containers'
    Invoke-Compose -Arguments ($AppProfiles + @('down', '--remove-orphans')) -AllowFailure -Quiet

    # ── Build ────────────────────────────────────────────────
    Write-Step 'Building images'
    if ($Clean) {
        Invoke-Compose -Arguments ($AppProfiles + @('build', '--no-cache'))
    } else {
        Invoke-Compose -Arguments ($AppProfiles + @('build'))
    }

    # ── Infrastructure ───────────────────────────────────────
    Write-Step 'Starting infrastructure (db, redis, aspire)'
    Invoke-Compose -Arguments @('up', '-d', 'db', 'redis', 'aspire')
    Wait-ForHealth -Name 'PostgreSQL' -TimeoutSeconds 60 -Probe @(
        'exec', 'db', 'pg_isready', '-h', '127.0.0.1', '-U', 'ecommerce', '-d', 'ecommerce_agents')
    Wait-ForHealth -Name 'Redis' -Probe @('exec', 'redis', 'redis-cli', 'ping')
    Repair-StaleDatabaseVolume
    Invoke-Migrations
    Write-Ok 'Infrastructure is ready'

    # ── Seed ─────────────────────────────────────────────────
    Write-Step 'Running database seeder'
    Invoke-Compose -Arguments @('--profile', 'seed', 'run', '--rm', 'seeder')
    Write-Ok 'Database seeded'

    if ($InfraOnly) {
        Show-Summary
        Write-Ok 'Infrastructure-only mode — agents not started'
        exit 0
    }

    # ── Agents and frontend ──────────────────────────────────
    Write-Step 'Starting agents and frontend'
    Invoke-Compose -Arguments ($RunProfiles + @('up', '-d'))

    Wait-ForHttp -Name 'Orchestrator'      -Url 'http://localhost:8080/ready'
    Wait-ForHttp -Name 'Product Discovery' -Url 'http://localhost:8081/ready'
    Wait-ForHttp -Name 'Order Management'  -Url 'http://localhost:8082/ready'
    Wait-ForHttp -Name 'Pricing & Promos'  -Url 'http://localhost:8083/ready'
    Wait-ForHttp -Name 'Review & Sentiment' -Url 'http://localhost:8084/ready'
    Wait-ForHttp -Name 'Inventory & Fulfilment' -Url 'http://localhost:8085/ready'
    Write-Ok 'All agents are running'

    Wait-ForHttp -Name 'Frontend' -Url 'http://localhost:3000' -TimeoutSeconds 90
    Write-Ok 'Frontend is running'

    Show-Summary
    Write-Ok 'E-Commerce Agents is ready!'
} catch {
    Write-Err $_.Exception.Message
    exit 1
} finally {
    Pop-Location
}
