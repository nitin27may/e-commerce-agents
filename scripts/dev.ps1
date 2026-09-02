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

function Assert-DatabaseSchema {
    <#
        .SYNOPSIS
        Verifies the SCHEMA matches, not just the credentials.

        .DESCRIPTION
        A volume can authenticate perfectly and still carry a schema from before
        the last init.sql change. That is the more common way to land here —
        pulling new commits and restarting the same stack, rather than switching
        stacks — and it is nastier than an auth failure, because nothing errors.
        Search quietly returns nothing and the agent says "I couldn't find any",
        which reads like an empty catalogue rather than a broken index.

        products.search_vector is the canary: it arrived with the v1.2.0
        full-text search work, so any volume predating that lacks it. Hardcoded
        on purpose — deriving the check from init.sql would be clever and would
        drift.
    #>
    $query = "SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'search_vector'"
    $probe = @('exec', '-T', 'db', 'sh', '-c',
               "PGPASSWORD=ecommerce_secret psql -h 127.0.0.1 -U ecommerce -d ecommerce_agents -tAc `"$query`"")
    $result = & docker @($ComposeArgs + $probe) 2>$null
    if ($result -match '1') { return }

    Write-Host ''
    Write-Warn 'Database schema is out of date — products.search_vector is missing.'
    Write-Host ''
    Write-Host '  Your Postgres volume predates the full-text search change. The stack will'
    Write-Host '  start and look healthy, and every product search will silently return'
    Write-Host '  nothing. Refusing to start into that.'
    Write-Host ''
    Write-Host '  Two ways forward:'
    Write-Host ''
    Write-Host '    Recreate the volume (loses local data, reseeds from scratch):'
    Write-Host '      ./scripts/dev.ps1 -Clean'
    Write-Host ''
    Write-Host '    Or apply the schema in place, keeping your data:'
    Write-Host '      docker compose exec -T db psql -U ecommerce -d ecommerce_agents < docker/postgres/init.sql'
    Write-Host ''
    exit 1
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
        Assert-DatabaseSchema
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
    Assert-DatabaseSchema
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

    Wait-ForHttp -Name 'Orchestrator'      -Url 'http://localhost:8080/health'
    Wait-ForHttp -Name 'Product Discovery' -Url 'http://localhost:8081/health'
    Wait-ForHttp -Name 'Order Management'  -Url 'http://localhost:8082/health'
    Wait-ForHttp -Name 'Pricing & Promos'  -Url 'http://localhost:8083/health'
    Wait-ForHttp -Name 'Review & Sentiment' -Url 'http://localhost:8084/health'
    Wait-ForHttp -Name 'Inventory & Fulfilment' -Url 'http://localhost:8085/health'
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
