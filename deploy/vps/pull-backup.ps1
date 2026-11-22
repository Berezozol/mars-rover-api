<#
.SYNOPSIS
    Downloads the newest verified database backup from the VPS to this computer.

.DESCRIPTION
    Windows PowerShell 5.1 compatible. Meant for the owner's Windows 11 computer: the computer
    pulls, the server never connects out to it. Uses the system OpenSSH client
    (C:\Windows\System32\OpenSSH\ssh.exe and scp.exe) with the owner's key from the Windows
    ssh-agent; no password prompt (BatchMode), the host key must already be known (connect once
    by hand: ssh deploy@<host>).

    Steps:
      1. ask the server for the newest mars-<stamp>.dump.sha256 (backup.sh writes it only after
         pg_restore --list read the archive, so a dump with a checksum is a verified dump);
      2. if that dump is already in the local directory, stop (nothing to do);
      3. scp the dump and its checksum to *.partial files, compare size with the server and
         SHA-256 with the checksum, then rename;
      4. keep the newest -Keep dumps (default 12, about three months of weekly copies), delete older ones;
      5. touch .last-pulled on the server (host-check.sh warns when nobody has pulled for 9 days).
    Everything is appended to <LocalDir>\pull-backup.log. Exit code 0 on success (also when the
    newest dump was already local), 1 on any failure.

.EXAMPLE
    .\pull-backup.ps1 -HostName <VPS_IP>

.EXAMPLE
    $env:MARS_VPS_HOST = '<VPS_IP>'; .\pull-backup.ps1 -Keep 24
#>
[CmdletBinding()]
param(
    # Server address (name or IP). Not stored in the repository: pass it or set MARS_VPS_HOST.
    [string]$HostName = $env:MARS_VPS_HOST,
    [string]$User = 'deploy',
    # Directory with the dumps on the server.
    [string]$RemoteDir = '/srv/mars/backups',
    [string]$LocalDir = (Join-Path $env:USERPROFILE 'mars-backups'),
    [ValidateRange(1, 1000)]
    [int]$Keep = 12,
    # Optional private key file; by default the ssh-agent / default keys are used.
    [string]$IdentityFile = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$ssh = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$scp = Join-Path $env:SystemRoot 'System32\OpenSSH\scp.exe'

if (-not (Test-Path -LiteralPath $LocalDir)) {
    New-Item -ItemType Directory -Path $LocalDir | Out-Null
}
$LogFile = Join-Path $LocalDir 'pull-backup.log'

function Write-Log([string]$Message) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

# Native stderr is deliberately not redirected: with $ErrorActionPreference = 'Stop' Windows
# PowerShell 5.1 turns redirected native stderr into terminating errors.
function Invoke-Ssh([string]$RemoteCommand) {
    $out = & $ssh @script:BaseOptions "$User@$HostName" $RemoteCommand
    if ($LASTEXITCODE -ne 0) {
        throw "ssh failed with exit code $LASTEXITCODE (command: $RemoteCommand)"
    }
    return $out
}

function Invoke-Scp([string]$RemoteFile, [string]$LocalName) {
    # Local name is relative to the current directory (set to LocalDir): "C:\..." would be
    # read as host "C" by some scp builds.
    & $scp @script:BaseOptions -q "$User@${HostName}:$RemoteDir/$RemoteFile" $LocalName
    if ($LASTEXITCODE -ne 0) { throw "scp of $RemoteFile failed with exit code $LASTEXITCODE" }
}

$exitCode = 0
$partials = @()
Push-Location -LiteralPath $LocalDir
try {
    if (-not $HostName) { throw 'No server address: pass -HostName or set the MARS_VPS_HOST variable.' }
    foreach ($exe in @($ssh, $scp)) {
        if (-not (Test-Path -LiteralPath $exe)) { throw "Not found: $exe (Windows optional feature 'OpenSSH Client')" }
    }
    $script:BaseOptions = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', '-o', 'StrictHostKeyChecking=yes')
    if ($IdentityFile) { $script:BaseOptions += @('-i', $IdentityFile) }

    Write-Log "Pull started: $User@$HostName`:$RemoteDir -> $LocalDir"

    $listing = Invoke-Ssh "ls -1 $RemoteDir | grep -E '^mars-[0-9]{8}-[0-9]{6}\.dump\.sha256$' | sort | tail -n 1"
    $sumName = ("$listing").Trim()
    if (-not $sumName) { throw 'The server has no verified backup (no mars-*.dump.sha256).' }
    # The name comes from the server: accept only the exact expected shape.
    if ($sumName -notmatch '^mars-\d{8}-\d{6}\.dump\.sha256$') { throw "Unexpected file name from the server: $sumName" }
    $dumpName = $sumName -replace '\.sha256$', ''

    if ((Test-Path -LiteralPath $dumpName) -and (Test-Path -LiteralPath $sumName)) {
        Write-Log "Already up to date: $dumpName"
    }
    else {
        $remoteSize = [int64]((Invoke-Ssh "stat -c %s $RemoteDir/$dumpName") | Select-Object -First 1)
        $partDump = "$dumpName.partial"
        $partSum = "$sumName.partial"
        $partials = @($partDump, $partSum)

        Write-Log ("Downloading {0} ({1:N1} MB)" -f $dumpName, ($remoteSize / 1MB))
        Invoke-Scp $dumpName $partDump
        Invoke-Scp $sumName $partSum

        $local = Get-Item -LiteralPath $partDump
        if ($local.Length -ne $remoteSize) {
            throw "Size mismatch: server $remoteSize bytes, local $($local.Length) bytes"
        }
        $expected = ((Get-Content -LiteralPath $partSum -TotalCount 1) -split '\s+')[0].ToLower()
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $partDump).Hash.ToLower()
        if ($expected -notmatch '^[0-9a-f]{64}$') { throw "Unreadable checksum file: $sumName" }
        if ($actual -ne $expected) { throw "SHA-256 mismatch for $dumpName (expected $expected, got $actual)" }

        Move-Item -LiteralPath $partDump -Destination $dumpName
        Move-Item -LiteralPath $partSum -Destination $sumName
        $partials = @()
        Write-Log "Backup OK: $dumpName (size and SHA-256 match)"
    }

    # Retention: newest $Keep dumps (names sort by timestamp).
    $old = @(Get-ChildItem -LiteralPath $LocalDir -Filter 'mars-*.dump' |
        Sort-Object Name -Descending | Select-Object -Skip $Keep)
    foreach ($f in $old) {
        Remove-Item -LiteralPath $f.FullName
        Remove-Item -LiteralPath ($f.FullName + '.sha256') -ErrorAction SilentlyContinue
        Write-Log "Rotated out: $($f.Name)"
    }

    # Tell the server this computer has the newest backup (host-check.sh watches the age).
    Invoke-Ssh "touch $RemoteDir/.last-pulled" | Out-Null
}
catch {
    Write-Log "PULL FAILED: $($_.Exception.Message)"
    foreach ($p in $partials) { Remove-Item -LiteralPath $p -ErrorAction SilentlyContinue }
    $exitCode = 1
}
finally {
    Pop-Location
}
exit $exitCode
