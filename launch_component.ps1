param(
    [Parameter(Mandatory = $true)]
    [string]$ComponentName,

    [Parameter(Mandatory = $true)]
    [string]$PythonExe,

    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,

    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory = $true)]
    [string]$StdoutLog,

    [Parameter(Mandatory = $true)]
    [string]$StderrLog
)

$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $ProjectRoot
$logDir = Split-Path -Parent $StdoutLog
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

"" | Out-File -LiteralPath $StderrLog -Encoding utf8

Start-Transcript -LiteralPath $StdoutLog -Force | Out-Null
try {
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Starting $ComponentName"
    Write-Host "Python: $PythonExe"
    Write-Host "Script: $ScriptPath"

    & $PythonExe $ScriptPath
    $exitCode = $LASTEXITCODE

    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $ComponentName exited with code $exitCode"
} catch {
    $_ | Out-File -LiteralPath $StderrLog -Encoding utf8 -Append
    $exitCode = 1
} finally {
    Stop-Transcript | Out-Null
}

exit $exitCode
