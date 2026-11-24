# Continuous mode for the long downloads (REMS weather raw files + RAD history), by the owner's
# request: runs until every job finishes or until the file data/nightly/STOP exists.
# Restarts Docker Desktop / a downloader that died with an error; a downloader that exits 0 is done.
# The REMS download runs as $shards parallel workers (scripts/weather_download.py --shard i/N);
# when all of them are done, the weather files are parsed into the database.
param(
    [int]$shards = 4,
    [double]$interval = 4,
    [string]$fromRange = 'SOL_03069',
    [switch]$skipRad
)
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
$logDir = Join-Path $repo 'data\nightly'
New-Item -ItemType Directory -Force $logDir | Out-Null
$main = Join-Path $logDir 'continuous.log'
$stop = Join-Path $logDir 'STOP'
function Write-Note($text) { "$(Get-Date -Format 's') $text" | Out-File -Append -Encoding utf8 $main }

function Find-Proc($pattern) {
    Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match $pattern } | Select-Object -First 1
}
function Ensure-Docker {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Note 'docker is down, starting Docker Desktop'
        Start-Process (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe')
        $deadline = (Get-Date).AddMinutes(5)
        do { Start-Sleep 10; docker info *> $null } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)
    }
    docker compose up -d --wait db *> $null
}

$jobs = [ordered]@{}
foreach ($i in 1..$shards) {
    $jobs["weather$i"] = @{
        args    = @('run', '--frozen', '--no-sync', 'python', '-u', 'scripts/weather_download.py',
                    '--mission', 'rems', '--interval', "$interval", '--from-range', $fromRange, '--shard', "$i/$shards")
        pattern = "weather_download\.py.*--shard $i/$shards"
    }
}
if (-not $skipRad) {
    $jobs['rad'] = @{ args = @('run', '--frozen', '--no-sync', 'python', '-m', 'app.cli', 'rad', 'sync'); pattern = 'app\.cli rad sync' }
}
$done = @{}; $procs = @{}
foreach ($name in $jobs.Keys) { $done[$name] = $false; $procs[$name] = $null }
Write-Note "start ($($jobs.Keys -join ', '))"

while (-not (Test-Path $stop) -and ($done.Values -contains $false)) {
    Ensure-Docker
    foreach ($name in @($jobs.Keys)) {
        if ($done[$name]) { continue }
        $p = $procs[$name]
        if ($p -and $p.HasExited) {
            Write-Note "$name exited $($p.ExitCode)"
            if ($p.ExitCode -eq 0) { $done[$name] = $true; continue }
            $procs[$name] = $null
        }
        if (-not $procs[$name] -and -not (Find-Proc $jobs[$name].pattern)) {
            $stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
            $procs[$name] = Start-Process $uv -WindowStyle Hidden -PassThru -ArgumentList $jobs[$name].args `
                -RedirectStandardOutput (Join-Path $logDir "$stamp-$name.log") -RedirectStandardError (Join-Path $logDir "$stamp-$name.err")
            # Touch the handle now: without it Start-Process leaves ExitCode empty after exit,
            # and a finished download would be restarted forever.
            $null = $procs[$name].Handle
            Write-Note "$name started pid $($procs[$name].Id)"
        }
    }
    Start-Sleep 60
}

if (Test-Path $stop) {
    foreach ($name in @($jobs.Keys)) {
        $p = Find-Proc $jobs[$name].pattern
        if ($p) { taskkill /PID $p.ProcessId /T /F *> $null; Write-Note "$name stopped (STOP file)" }
    }
    Remove-Item $stop
}
$weatherDone = -not (@($jobs.Keys | Where-Object { $_ -like 'weather*' -and -not $done[$_] }).Count)
if ($weatherDone) {
    & $uv run --frozen --no-sync python -m app.cli weather parse --rover curiosity *>> (Join-Path $logDir 'continuous-weather-parse.log')
    Write-Note "weather parse exit $LASTEXITCODE"
}
Write-Note 'end'
