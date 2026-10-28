[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$scriptPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'run-local.ps1'))
if (!(Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw "run-local.ps1 is missing: $scriptPath" }

$tokens = $null
$parseErrors = $null
$scriptAst = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw 'run-local.ps1 has PowerShell parse errors' }

foreach ($name in @('Get-StatePath', 'Read-State', 'Convert-StateCreatedAtToUtc', 'Test-ManagedProcess', 'Get-Status')) {
    $functionAst = $scriptAst.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if ($null -eq $functionAst) { throw "Function not found: $name" }
    Invoke-Expression $functionAst.Extent.Text
}

$formalConfig = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform\priority-local\runtime.json'))
$runtimeRoot = [IO.Path]::GetDirectoryName($formalConfig)
$pidRoot = Join-Path (Join-Path $runtimeRoot 'state') 'pids'
if (!(Test-Path -LiteralPath $formalConfig -PathType Leaf)) { throw "Protected runtime configuration is missing: $formalConfig" }
if (!(Test-Path -LiteralPath $pidRoot -PathType Container)) { throw "PID state directory is missing: $pidRoot" }

function Assert-True([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
}

try {
    $states = @{}
    foreach ($service in @('api', 'worker')) {
        $state = Read-State $service
        Assert-True ($null -ne $state) "$service state is missing"
        $process = Get-Process -Id ([int]$state.pid) -ErrorAction Stop
        $states[$service] = [pscustomobject]@{ pid = $process.Id; start = $process.StartTime }
        Assert-True (Test-ManagedProcess $state) "$service current state is not managed"

        # Reproduce the legacy writer output: UTC value without a timezone suffix.
        $legacyCreatedAt = $process.StartTime.ToUniversalTime().ToString('o')
        if ($legacyCreatedAt.EndsWith('Z')) { $legacyCreatedAt = $legacyCreatedAt.Substring(0, $legacyCreatedAt.Length - 1) }
        $legacyState = [pscustomobject]@{ pid = $process.Id; created_at = $legacyCreatedAt }
        Assert-True (Test-ManagedProcess $legacyState) "$service legacy UTC timestamp without offset was rejected"

        $utcState = [pscustomobject]@{ pid = $process.Id; created_at = $process.StartTime.ToUniversalTime() }
        Assert-True (Test-ManagedProcess $utcState) "$service DateTime UTC value was rejected"
        $localState = [pscustomobject]@{ pid = $process.Id; created_at = $process.StartTime }
        Assert-True (Test-ManagedProcess $localState) "$service DateTime Local value was rejected"
        $unspecifiedState = [pscustomobject]@{ pid = $process.Id; created_at = [DateTime]::SpecifyKind($process.StartTime.ToUniversalTime(), [DateTimeKind]::Unspecified) }
        Assert-True (Test-ManagedProcess $unspecifiedState) "$service state-contract Unspecified UTC value was rejected"
        $zState = [pscustomobject]@{ pid = $process.Id; created_at = $process.StartTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffffffZ') }
        Assert-True (Test-ManagedProcess $zState) "$service Z timestamp was rejected"
        $offsetState = [pscustomobject]@{ pid = $process.Id; created_at = $process.StartTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffffff+00:00') }
        Assert-True (Test-ManagedProcess $offsetState) "$service offset-bearing UTC value was rejected"

        $forgedState = [pscustomobject]@{ pid = $process.Id; created_at = $process.StartTime.ToUniversalTime().AddHours(1).ToString('o') }
        Assert-True (!(Test-ManagedProcess $forgedState)) "$service forged creation time was accepted"
        $pidReuseState = [pscustomobject]@{ pid = $process.Id; created_at = $process.StartTime.ToUniversalTime().AddMilliseconds(2500).ToString('o') }
        Assert-True (!(Test-ManagedProcess $pidReuseState)) "$service PID reuse guard accepted a different creation time"
    }

    $testRoot = Join-Path ([IO.Path]::GetTempPath()) "chrom-run-local-test-$PID"
    New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
    $pidRoot = $testRoot
    $fakeState = [ordered]@{
        service = 'fake'
        pid = $PID
        created_at = (Get-Process -Id $PID).StartTime.ToUniversalTime().AddHours(1).ToString('o')
        stdout = ''
        stderr = ''
    }
    $fakeState | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $testRoot 'fake.json') -Encoding UTF8 -NoNewline
    $fakeStatus = Get-Status 'fake'
    Assert-True ($fakeStatus.status -eq 'STOPPED') 'status accepted a forged PID creation time'

    foreach ($service in @('api', 'worker')) {
        $statusOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath -Action status -Service $service -ConfigFile $formalConfig
        if ($LASTEXITCODE -ne 0) { throw "run-local status failed for $service with exit code $LASTEXITCODE" }
        $item = ConvertFrom-Json -InputObject ($statusOutput -join [Environment]::NewLine)
        Assert-True ($item.status -eq 'RUNNING') "run-local status did not report $service RUNNING"
        Assert-True ("$($item.pid)" -eq "$($states[$service].pid)") "run-local status changed $service PID"
    }

    foreach ($service in @('api', 'worker')) {
        $after = Get-Process -Id $states[$service].pid -ErrorAction Stop
        Assert-True ($after.StartTime -eq $states[$service].start) "$service PID/start time changed during status test"
    }
    Write-Output ('RUN_LOCAL_TEST_OK api_pid={0} worker_pid={1}' -f $states.api.pid, $states.worker.pid)
} finally {
    if ($null -ne $testRoot -and (Test-Path -LiteralPath $testRoot)) { Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
