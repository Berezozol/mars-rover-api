# Nightly window for the long downloads (weather raw files + RAD history).
# Started by the Windows scheduled task "MarsRover nightly download" at 00:00;
# everything is stopped by 05:00. Both downloaders resume where they stopped.
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
$end = (Get-Date).Date.AddHours(5)
if ((Get-Date) -ge $end) { $end = $end.AddDays(1) }  # started in the evening (by hand): run until the next 05:00

$logDir = Join-Path $repo 'data\nightly'
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format 'yyyy-MM-dd'
$main = Join-Path $logDir "$stamp.log"
function Write-Note($text) { "$(Get-Date -Format 's') $text" | Out-File -Append -Encoding utf8 $main }

Write-Note 'start'
# Docker Desktop may be closed during the day to free memory: start it and wait for the engine.
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Start-Process (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe')
    $deadline = (Get-Date).AddMinutes(5)
    do { Start-Sleep 10; docker info *> $null } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)
    Write-Note "docker started, info exit $LASTEXITCODE"
}
docker compose up -d --wait db *>> $main
Write-Note "db exit $LASTEXITCODE"

$weather = Start-Process $uv -NoNewWindow -PassThru `
    -ArgumentList 'run', '--frozen', '--no-sync', 'python', '-u', 'scripts/weather_download.py', '--mission', 'rems', '--interval', '3', '--stop-at', '04:58' `
    -RedirectStandardOutput (Join-Path $logDir "$stamp-weather.log") -RedirectStandardError (Join-Path $logDir "$stamp-weather.err")
$rad = Start-Process $uv -NoNewWindow -PassThru `
    -ArgumentList 'run', '--frozen', '--no-sync', 'python', '-m', 'app.cli', 'rad', 'sync' `
    -RedirectStandardOutput (Join-Path $logDir "$stamp-rad.log") -RedirectStandardError (Join-Path $logDir "$stamp-rad.err")
Write-Note "weather pid $($weather.Id), rad pid $($rad.Id)"

while ((Get-Date) -lt $end -and (-not $weather.HasExited -or -not $rad.HasExited)) { Start-Sleep 30 }

foreach ($p in @($weather, $rad)) {
    if ($p.HasExited) { Write-Note "pid $($p.Id) exited $($p.ExitCode)" }
    else { taskkill /PID $p.Id /T /F *>> $main; Write-Note "pid $($p.Id) stopped at 05:00" }
}
if ($weather.HasExited) {
    # New REMS files -> daily/hourly summaries in the dev DB (local, no network).
    & $uv run --frozen --no-sync python -m app.cli weather parse --rover curiosity *>> (Join-Path $logDir "$stamp-weather-parse.log")
    Write-Note "weather parse exit $LASTEXITCODE"
}
Write-Note 'end'
