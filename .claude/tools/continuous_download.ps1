# Continuous mode for the long downloads (REMS weather raw files + RAD history), by the owner's
# request: runs until both finish or until the file data/nightly/STOP exists.
# Restarts Docker Desktop / a downloader that died with an error; a downloader that exits 0 is done.
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

$done = @{ weather = $false; rad = $false }
$procs = @{ weather = $null; rad = $null }
$args_ = @{
    weather = @('run', '--frozen', '--no-sync', 'python', '-u', 'scripts/weather_download.py', '--mission', 'rems', '--interval', '3')
    rad     = @('run', '--frozen', '--no-sync', 'python', '-m', 'app.cli', 'rad', 'sync')
}
$patterns = @{ weather = 'weather_download\.py'; rad = 'app\.cli rad sync' }
Write-Note 'start'

while (-not (Test-Path $stop) -and -not ($done.weather -and $done.rad)) {
    Ensure-Docker
    foreach ($name in @('weather', 'rad')) {
        if ($done[$name]) { continue }
        $p = $procs[$name]
        if ($p -and $p.HasExited) {
            Write-Note "$name exited $($p.ExitCode)"
            if ($p.ExitCode -eq 0) { $done[$name] = $true; continue }
            $procs[$name] = $null
        }
        if (-not $procs[$name] -and -not (Find-Proc $patterns[$name])) {
            $stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
            $procs[$name] = Start-Process $uv -WindowStyle Hidden -PassThru -ArgumentList $args_[$name] `
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
    foreach ($name in @('weather', 'rad')) {
        $p = Find-Proc $patterns[$name]
        if ($p) { taskkill /PID $p.ProcessId /T /F *> $null; Write-Note "$name stopped (STOP file)" }
    }
    Remove-Item $stop
}
if ($done.weather) {
    & $uv run --frozen --no-sync python -m app.cli weather parse --rover curiosity *>> (Join-Path $logDir 'continuous-weather-parse.log')
    Write-Note "weather parse exit $LASTEXITCODE"
}
Write-Note 'end'
