@echo off
setlocal

set "ROOT=D:\Jupyter\Chromatography_agent"
set "DE_PY=D:\Anaconda\envs\DE\python.exe"
set "LR_PY=D:\Anaconda\envs\LR\python.exe"
set "LOG_DIR=%ROOT%\Log"
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "LAUNCHER=%ROOT%\launch_component.ps1"

if not exist "%ROOT%\start_services.py" (
    echo [ERROR] Project path not found: "%ROOT%"
    pause
    exit /b 1
)

if not exist "%DE_PY%" (
    echo [ERROR] DE Python not found: "%DE_PY%"
    pause
    exit /b 1
)

if not exist "%LR_PY%" (
    echo [ERROR] LR Python not found: "%LR_PY%"
    pause
    exit /b 1
)

if not exist "%PS_EXE%" (
    echo [ERROR] PowerShell not found: "%PS_EXE%"
    pause
    exit /b 1
)

if not exist "%LAUNCHER%" (
    echo [ERROR] Launcher not found: "%LAUNCHER%"
    pause
    exit /b 1
)

cd /d "%ROOT%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo Starting chromatography services with DE environment...
start "Chromatography Services (DE)" "%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -NoExit -File "%LAUNCHER%" -ComponentName "Chromatography Services (DE)" -PythonExe "%DE_PY%" -ScriptPath "%ROOT%\start_services.py" -ProjectRoot "%ROOT%" -StdoutLog "%LOG_DIR%\services.log" -StderrLog "%LOG_DIR%\services.err.log"

echo Waiting for services on ports 8010, 8011, and 8012...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$urls=@('http://127.0.0.1:8010/health','http://127.0.0.1:8011/health','http://127.0.0.1:8012/health'); $deadline=(Get-Date).AddSeconds(180); do { $allOk=$true; foreach ($url in $urls) { try { $r=Invoke-RestMethod -Uri $url -TimeoutSec 3; if (-not $r.ok) { $allOk=$false } } catch { $allOk=$false } }; if ($allOk) { exit 0 }; Start-Sleep -Seconds 2 } while ((Get-Date) -lt $deadline); exit 1"

if errorlevel 1 (
    echo [ERROR] Services did not become healthy within 180 seconds.
    pause
    exit /b 1
)

echo Starting UI with LR environment...
start "Chromatography UI (LR)" "%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -NoExit -File "%LAUNCHER%" -ComponentName "Chromatography UI (LR)" -PythonExe "%LR_PY%" -ScriptPath "%ROOT%\UI_app.py" -ProjectRoot "%ROOT%" -StdoutLog "%LOG_DIR%\ui.log" -StderrLog "%LOG_DIR%\ui.err.log"

echo Waiting for UI on port 7860...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(180); do { try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:7860/' -TimeoutSec 3; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { exit 0 } } catch { }; Start-Sleep -Seconds 2 } while ((Get-Date) -lt $deadline); exit 1"

if errorlevel 1 (
    echo [ERROR] UI did not become available within 180 seconds.
    pause
    exit /b 1
)

echo Done. Services and UI are ready.
start "" "http://127.0.0.1:7860"
endlocal
