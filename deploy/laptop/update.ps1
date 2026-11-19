<#
.SYNOPSIS
    Updates the Mars Rover API on the laptop: pull, build, backup, migrate, restart, health check.

.DESCRIPTION
    Windows PowerShell 5.1 compatible. Steps, each must succeed before the next one:
      1. git pull --ff-only in the repository
      2. docker build -t mars-rover-api:local <repo>
      3. start db if needed, fresh database backup (backup.ps1 next to this script)
      4. docker compose run --rm app alembic upgrade head   (the old version keeps serving)
      5. docker compose up -d app                            (recreate with the new image)
      6. wait until /health answers 200
    On failure it stops with a message naming the failed step. Nothing is rolled back and the
    database is never restored automatically (see deploy/README.md, "Restore").

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\update.ps1 -RepoDir C:\Users\<user>\PycharmProjects\mars-rover-api
#>
[CmdletBinding()]
param(
    # Git checkout of the project. Default: the repository this script lives in, if any.
    [string]$RepoDir = '',
    # Directory with compose.yml and .env.
    [string]$ComposeDir = (Join-Path $env:USERPROFILE 'mars-server'),
    [string]$ProjectName = 'mars',
    [string]$Image = 'mars-rover-api:local',
    [string]$HealthUrl = 'http://127.0.0.1:8000/health',
    [int]$HealthTimeoutSeconds = 120,
    # Build what is checked out now, without git pull.
    [switch]$SkipPull
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host ('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message) -ForegroundColor Cyan
}

function Stop-Update([string]$Step, [string]$Detail) {
    Write-Host ''
    Write-Host "UPDATE FAILED at step: $Step" -ForegroundColor Red
    if ($Detail) { Write-Host $Detail -ForegroundColor Red }
    Write-Host "Nothing was rolled back. Logs: docker compose -p $ProjectName logs --tail 200 app" -ForegroundColor Yellow
    exit 1
}

function Invoke-Native([string]$Step, [string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        Stop-Update $Step ("'{0} {1}' exited with code {2}" -f $Exe, ($Arguments -join ' '), $LASTEXITCODE)
    }
}

if (-not $RepoDir) {
    $candidate = Join-Path $PSScriptRoot '..\..'
    if (Test-Path -LiteralPath (Join-Path $candidate 'Dockerfile')) {
        $RepoDir = (Resolve-Path -LiteralPath $candidate).Path
    }
    else {
        Stop-Update 'parameters' 'Pass -RepoDir <path to the git checkout of mars-rover-api>.'
    }
}
$ComposeFile = Join-Path $ComposeDir 'compose.yml'
$BackupScript = Join-Path $PSScriptRoot 'backup.ps1'
$required = @((Join-Path $RepoDir 'Dockerfile'), $ComposeFile, (Join-Path $ComposeDir '.env'), $BackupScript)
foreach ($p in $required) {
    if (-not (Test-Path -LiteralPath $p)) { Stop-Update 'parameters' "Not found: $p" }
}
$compose = @('compose', '-f', $ComposeFile, '-p', $ProjectName)

if ($SkipPull) {
    Write-Step '1/6 git pull skipped (-SkipPull)'
}
else {
    Write-Step "1/6 git pull --ff-only in $RepoDir"
    Invoke-Native 'git pull' 'git' @('-C', $RepoDir, 'pull', '--ff-only')
}
$commit = (& git -C $RepoDir rev-parse --short HEAD)
Write-Host "Commit: $commit"

Write-Step "2/6 docker build -t $Image"
Invoke-Native 'docker build' 'docker' @('build', '-t', $Image, $RepoDir)

Write-Step '3/6 database backup'
# Make sure the database is up (no-op when it already runs).
Invoke-Native 'start db' 'docker' ($compose + @('up', '-d', '--wait', 'db'))
& $BackupScript -ComposeDir $ComposeDir -ProjectName $ProjectName
if ($LASTEXITCODE -ne 0) {
    Stop-Update 'backup' "backup.ps1 exited with code $LASTEXITCODE; see $ComposeDir\backups\backup.log"
}

Write-Step '4/6 migrations: alembic upgrade head (one-off container, new image)'
Invoke-Native 'migrations' 'docker' ($compose + @('run', '--rm', 'app', 'alembic', 'upgrade', 'head'))

Write-Step '5/6 restart app with the new image'
Invoke-Native 'docker compose up' 'docker' ($compose + @('up', '-d', 'app'))

Write-Step "6/6 waiting for $HealthUrl (up to $HealthTimeoutSeconds s)"
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
$lastError = ''
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) {
            Write-Host "Health: $($r.Content)"
            Write-Host ''
            Write-Host "UPDATE OK: $Image at commit $commit is serving." -ForegroundColor Green
            exit 0
        }
        $lastError = "HTTP $($r.StatusCode)"
    }
    catch {
        $lastError = $_.Exception.Message
    }
    Start-Sleep -Seconds 3
}
Stop-Update 'health check' "No 200 from $HealthUrl within $HealthTimeoutSeconds s. Last error: $lastError"
