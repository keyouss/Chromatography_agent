@echo off
setlocal
set "CHROM_START_PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not exist "%CHROM_START_PWSH%" set "CHROM_START_PWSH=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe"
if not exist "%CHROM_START_PWSH%" (
    echo PowerShell 7 was not found. Windows PowerShell 5.1 is not supported.
    if not "%CHROM_LAUNCH_NONINTERACTIVE%"=="1" pause
    exit /b 1
)
"%CHROM_START_PWSH%" -NoProfile -File "%~dp0deploy\v1\start-platform.ps1" %*
set "CHROM_START_EXIT=%ERRORLEVEL%"
if not "%CHROM_START_EXIT%"=="0" if not "%CHROM_LAUNCH_NONINTERACTIVE%"=="1" pause
exit /b %CHROM_START_EXIT%
