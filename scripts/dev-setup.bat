@echo off
rem One-shot local setup: uv, .env, Postgres in Docker, dependencies, migrations, tests.
rem Output goes to setup-log.txt in the project root.
setlocal
cd /d "%~dp0.."
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
set "LOG=setup-log.txt"
echo === dev-setup %date% %time% > "%LOG%"

where uv >nul 2>&1
if errorlevel 1 (
  echo [setup] uv not found, installing...
  echo [setup] installing uv >> "%LOG%"
  powershell -ExecutionPolicy ByPass -NoProfile -c "irm https://astral.sh/uv/install.ps1 | iex" >> "%LOG%" 2>&1
)

if not exist .env copy .env.example .env >> "%LOG%" 2>&1

call :step "uv version" uv --version
call :step "docker engine" docker version --format "{{.Server.Version}}"
call :step "postgres up" docker compose up -d --wait db
call :step "dependencies" uv sync
call :step "migrations" uv run alembic upgrade head
call :step "tests" uv run pytest -q
call :step "lint" uv run ruff check .

echo.
echo Done. Results are in %LOG%
echo === finished %date% %time% >> "%LOG%"
pause
exit /b 0

:step
echo [setup] %~1 ...
echo. >> "%LOG%"
echo ===== %~1 ===== >> "%LOG%"
%2 %3 %4 %5 %6 %7 %8 %9 >> "%LOG%" 2>&1
if errorlevel 1 (echo ===== FAILED: %~1 >> "%LOG%" & echo [setup] FAILED: %~1) else (echo ===== OK: %~1 >> "%LOG%")
exit /b 0
