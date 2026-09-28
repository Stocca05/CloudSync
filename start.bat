@echo off
REM ==============================================================================
REM CloudSync - 1-Click Launcher for Windows (Double-Clickable)
REM ==============================================================================
title CloudSync Orchestrator
cd /d "%~dp0"

echo ================================================================================
echo  CloudSync - Avvio Progetto con Link Cloudflare Temporaneo
echo ================================================================================

if not exist "config" mkdir "config"
if not exist "data" mkdir "data"

if not exist "config\rclone.conf" (
    if exist "config\rclone.conf.example" (
        copy "config\rclone.conf.example" "config\rclone.conf" >nul
    ) else (
        type nul > "config\rclone.conf"
    )
)

echo [1/3] Avvio del container Docker via docker-compose...
docker-compose up -d --build
if %errorlevel% neq 0 (
    docker compose up -d --build
)

echo [2/3] Attesa generazione del link Cloudflare...
set TUNNEL_URL=
set RETRIES=0

:poll_tunnel
timeout /t 1 /nobreak >nul
set /a RETRIES+=1

for /f "usebackq tokens=*" %%i in (`powershell -Command "Get-Content data\tunnel_url.txt -ErrorAction SilentlyContinue"`) do set TUNNEL_URL=%%i

if "%TUNNEL_URL%"=="" (
    for /f "usebackq tokens=*" %%j in (`powershell -Command "docker logs cloudsync 2>&1 | Select-String -Pattern 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | Select-Object -Last 1 | ForEach-Object { $_.Matches[0].Value }"`) do set TUNNEL_URL=%%j
)

if "%TUNNEL_URL%"=="" (
    if %RETRIES% lss 25 goto poll_tunnel
)

echo.
echo ================================================================================
echo  CLOUDSYNC E' ATTIVO E PRONTO ALL'USO!
echo ================================================================================
echo  Accesso Locale:    http://localhost:8000
if not "%TUNNEL_URL%"=="" (
    echo  Link Cloudflare:   %TUNNEL_URL%
    echo  (Accessibile ovunque da browser mobile e desktop)
    start "" "%TUNNEL_URL%"
) else (
    echo  Apertura browser su interfaccia locale...
    start "" "http://localhost:8000"
)
echo ================================================================================
echo.
pause
