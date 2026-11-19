<#
.SYNOPSIS
    Backs up the Mars Rover API database (pg_dump custom format) and keeps the newest N copies.

.DESCRIPTION
    Windows PowerShell 5.1 compatible. Runs pg_dump inside the db container
    (docker compose exec) straight into the backups directory, which compose.yml bind-mounts
    into the db container (./backups -> /backups). No temporary copy is made inside the
    container, so the WSL2 disk image does not grow by the dump size, and the dump never passes
    through a PowerShell pipeline (5.1 pipelines corrupt binary data).
    The archive is verified with pg_restore --list; older copies beyond -Keep are deleted.
    Appends to <BackupDir>\backup.log. Exit code 0 on success, 1 on any failure.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\<user>\mars-server\backup.ps1

.EXAMPLE
    .\backup.ps1 -ComposeDir C:\Users\<user>\mars-server -ProjectName mars-server -Keep 7
#>
[CmdletBinding()]
param(
    # Directory with compose.yml and .env.
    [string]$ComposeDir = $PSScriptRoot,
    # Compose project name (`name:` in compose.yml).
    [string]$ProjectName = 'mars-server',
    # Compose service of the database.
    [string]$Service = 'db',
    [string]$DbUser = 'mars',
    [string]$DbName = 'mars',
    # Host directory mounted into the db container. Default: <ComposeDir>\backups
    [string]$BackupDir = '',
    # The same directory as seen inside the db container.
    [string]$ContainerBackupDir = '/backups',
    [ValidateRange(1, 1000)]
    [int]$Keep = 7
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

if (-not $BackupDir) { $BackupDir = Join-Path $ComposeDir 'backups' }
if (-not (Test-Path -LiteralPath $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
}
$LogFile = Join-Path $BackupDir 'backup.log'
$ComposeFile = Join-Path $ComposeDir 'compose.yml'

function Write-Log([string]$Message) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Invoke-Compose([string[]]$Arguments) {
    $all = @('compose', '-f', $ComposeFile, '-p', $ProjectName) + $Arguments
    & docker @all
    if ($LASTEXITCODE -ne 0) {
        throw ("'docker compose {0}' failed with exit code {1}" -f ($Arguments -join ' '), $LASTEXITCODE)
    }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$name = "mars-$stamp.dump"
$remote = "$ContainerBackupDir/$name"
$local = Join-Path $BackupDir $name
$exitCode = 0

Write-Log "Backup started: project=$ProjectName service=$Service -> $local"
try {
    if (-not (Test-Path -LiteralPath $ComposeFile)) { throw "compose.yml not found: $ComposeFile" }

    Invoke-Compose @('exec', '-T', $Service, 'pg_dump', '-U', $DbUser, '-d', $DbName, '-Fc', '-f', $remote)
    # A readable table of contents means the archive is complete and restorable by pg_restore.
    Invoke-Compose @('exec', '-T', $Service, 'pg_restore', '--list', $remote) | Out-Null

    if (-not (Test-Path -LiteralPath $local)) {
        throw ("Dump written to {0} in the container is not in {1}: is BackupDir the directory " +
            "compose.yml mounts at {2}?") -f $remote, $BackupDir, $ContainerBackupDir
    }
    $file = Get-Item -LiteralPath $local
    if ($file.Length -le 0) { throw "Backup file is empty: $local" }
    Write-Log ('Backup OK: {0} ({1:N1} MB)' -f $local, ($file.Length / 1MB))

    $old = @(Get-ChildItem -LiteralPath $BackupDir -Filter 'mars-*.dump' |
        Sort-Object Name -Descending | Select-Object -Skip $Keep)
    foreach ($f in $old) {
        Remove-Item -LiteralPath $f.FullName
        Write-Log "Rotated out: $($f.Name)"
    }
}
catch {
    Write-Log "BACKUP FAILED: $($_.Exception.Message)"
    if (Test-Path -LiteralPath $local) { Remove-Item -LiteralPath $local -ErrorAction SilentlyContinue }
    # Best effort, also covers a BackupDir that does not match the mount.
    & docker compose -f $ComposeFile -p $ProjectName exec -T $Service rm -f $remote | Out-Null
    $exitCode = 1
}
exit $exitCode
