@echo off
rem FinSense AI launcher for Windows (Docker Desktop).
rem
rem   run.bat                 build (first time) and start everything, then open the browser
rem   run.bat stop            stop the containers (data is kept)
rem   run.bat status          show container status
rem   run.bat logs            follow the logs (Ctrl+C to stop following)
rem   run.bat create-admin you@example.com   create an administrator (asks for a password)
rem   run.bat promote you@example.com        make an already registered user an administrator
rem   run.bat reset           stop and DELETE all data (database, uploads, models)
rem   run.bat native          run WITHOUT Docker (needs Python 3.12/3.13, Node 20.19+,
rem                           PostgreSQL + pgvector prepared with db\manual-setup.sql)
rem
rem On first start it creates .env from .env.example with random database
rem passwords. No credentials are stored in this script.
setlocal EnableExtensions
cd /d "%~dp0"

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=start"

if /i "%ACTION%"=="help" goto help
if /i "%ACTION%"=="-h" goto help
if /i "%ACTION%"=="--help" goto help
if /i "%ACTION%"=="native" goto native

where docker >nul 2>nul
if errorlevel 1 (
  echo Error: Docker is not installed. Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/
  exit /b 1
)
docker compose version >nul 2>nul
if errorlevel 1 (
  echo Error: Docker Compose v2 is required. Update Docker Desktop.
  exit /b 1
)
docker info >nul 2>nul
if errorlevel 1 (
  echo Error: Docker Desktop is not running. Start it, wait until it says "Engine running", then try again.
  exit /b 1
)

if /i "%ACTION%"=="start" goto start
if /i "%ACTION%"=="up" goto start
if /i "%ACTION%"=="stop" goto stop
if /i "%ACTION%"=="down" goto stop
if /i "%ACTION%"=="restart" goto restart
if /i "%ACTION%"=="status" goto status
if /i "%ACTION%"=="ps" goto status
if /i "%ACTION%"=="logs" goto logs
if /i "%ACTION%"=="create-admin" goto createadmin
if /i "%ACTION%"=="promote" goto promote
if /i "%ACTION%"=="reset" goto reset
echo Error: unknown command "%ACTION%". Try: run.bat help
exit /b 1

:start
call :ensure_env
if errorlevel 1 exit /b 1
set "PORT=8080"
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
  if /i "%%A"=="WEB_PORT" if not "%%B"=="" set "PORT=%%B"
)
set "URL=http://localhost:%PORT%"
echo Building and starting containers...
docker compose up --build -d
if errorlevel 1 (
  echo Error: docker compose could not start the stack ^(see the messages above^).
  exit /b 1
)
echo Waiting for FinSense AI to become ready ^(the first build can take 10-20 minutes^)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline = (Get-Date).AddMinutes(30); while ((Get-Date) -lt $deadline) { try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Uri '%URL%/api/v1/health/ready'; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; $failed = docker compose ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' | Select-String -Pattern '^bootstrap exited [1-9]'; if ($failed) { exit 2 }; Start-Sleep -Seconds 5 }; exit 1"
set "WAIT=%errorlevel%"
if "%WAIT%"=="2" (
  docker compose logs --tail=60 bootstrap
  echo Error: database setup failed ^(see the bootstrap log above^).
  exit /b 1
)
if not "%WAIT%"=="0" (
  docker compose ps
  echo Error: the app did not become ready in time. Check: run.bat logs
  exit /b 1
)
echo.
echo FinSense AI is running at %URL%
echo Register an account in the browser, or create an administrator with:
echo   run.bat create-admin you@example.com
echo All bundled funds and documents are SYNTHETIC demonstration data.
if not "%NO_BROWSER%"=="1" start "" "%URL%"
exit /b 0

:stop
docker compose down
exit /b %errorlevel%

:restart
docker compose down
goto start

:status
docker compose ps
exit /b %errorlevel%

:logs
docker compose logs -f --tail=200
exit /b %errorlevel%

:createadmin
if "%~2"=="" (
  echo Usage: run.bat create-admin you@example.com ["Your Name"]
  exit /b 1
)
set "ADMIN_NAME=%~3"
if "%ADMIN_NAME%"=="" set "ADMIN_NAME=Administrator"
docker compose exec api python -m app.cli create-admin --email "%~2" --name "%ADMIN_NAME%"
exit /b %errorlevel%

:promote
if "%~2"=="" (
  echo Usage: run.bat promote you@example.com
  exit /b 1
)
docker compose exec api python -m app.cli create-admin --email "%~2" --promote
exit /b %errorlevel%

:reset
set /p "ANSWER=This permanently deletes the database, uploads and trained models. Type delete to continue: "
if /i not "%ANSWER%"=="delete" (
  echo Cancelled.
  exit /b 1
)
docker compose down -v
echo All FinSense AI data was deleted. Your .env file was kept.
exit /b 0

:native
set "PYEXE="
py -3.13 -c "import sys" >nul 2>nul && set "PYEXE=py -3.13"
if not defined PYEXE (
  py -3.12 -c "import sys" >nul 2>nul && set "PYEXE=py -3.12"
)
if not defined PYEXE (
  python -c "import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo Error: Python 3.12 or 3.13 is required for the native mode: https://www.python.org/downloads/
  exit /b 1
)
%PYEXE% scripts\run_native.py %2 %3
exit /b %errorlevel%

:ensure_env
if exist ".env" exit /b 0
if not exist ".env.example" (
  echo Error: .env.example is missing; re-extract the project.
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; function New-Secret { $bytes = New-Object byte[] 24; $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($bytes); -join ($bytes | ForEach-Object { $_.ToString('x2') }) }; $text = [System.IO.File]::ReadAllText((Join-Path (Get-Location) '.env.example')); $text = $text.Replace('change-me-owner-password', (New-Secret)).Replace('change-me-app-password', (New-Secret)); [System.IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $text)"
if errorlevel 1 (
  echo Error: could not create .env.
  exit /b 1
)
echo Created .env with newly generated database passwords ^(keep this file private^).
exit /b 0

:help
echo FinSense AI launcher for Windows (Docker Desktop)
echo.
echo   run.bat                 build (first time) and start, then open the browser
echo   run.bat stop            stop the containers (data is kept)
echo   run.bat status          show container status
echo   run.bat logs            follow the logs (Ctrl+C to stop following)
echo   run.bat create-admin you@example.com   create an administrator
echo   run.bat promote you@example.com        make a registered user an administrator
echo   run.bat reset           stop and DELETE all data
echo   run.bat native          run without Docker (see README)
exit /b 0
