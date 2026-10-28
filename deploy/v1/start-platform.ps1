#Requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('assistant-20261006', 'data-20261026')]
    [string]$Profile = 'assistant-20261006',
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action = 'start',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$managedEntry = Join-Path $PSScriptRoot 'run-local.ps1'
$checkEntry = Join-Path $PSScriptRoot 'startup-check.py'
$runtimeDirectory = if ($Profile -eq 'data-20261026') { 'data-accept-20261026' } else { 'assistant-accept-20261006' }
$runtimeRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) ('ChromatographyPlatform\' + $runtimeDirectory)
$pythonExe = 'D:\Anaconda\envs\SFC\python.exe'
$pwshExe = Join-Path $PSHOME 'pwsh.exe'
$urls = @{frontend='http://127.0.0.1:5174/'; api='http://127.0.0.1:9012/api/v1/health/ready'}
$capabilitiesUrl = 'http://127.0.0.1:9012/api/v1/system/capabilities'
$ports = @{frontend=5174; api=9012}
$profiles = @('assistant-20261006', 'data-20261026')
$owned = $false
$mutex = [Threading.Mutex]::new($false, ('Local\ChromatographyPlatform.Acceptance.Start.' + [Security.Principal.WindowsIdentity]::GetCurrent().User.Value))

function Invoke-Managed([string]$SelectedProfile, [string]$ManagedAction, [string]$Service) {
    # A child shell keeps the managed entry's process environment out of this launcher.
    # Return only the exception message, without PowerShell's source/stack dump.
    $entryLiteral = $managedEntry.Replace("'", "''")
    $command = "`$ProgressPreference = 'SilentlyContinue'; try { & '$entryLiteral' -AcceptanceProfile '$SelectedProfile' -LauncherManaged -Action '$ManagedAction' -Service '$Service'; if (`$LASTEXITCODE) { exit `$LASTEXITCODE } } catch { Write-Output ('MANAGED_FAILURE: ' + `$_.Exception.Message); exit 1 }"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    # Wait for the managed shell only; inherited pipe handles in a long-running
    # service must not keep the launcher waiting for that service to exit.
    if (!(Test-Path -LiteralPath $runtimeRoot -PathType Container)) { throw "PLATFORM: selected profile runtime is not provisioned: $SelectedProfile" }
    $captureRoot = Join-Path $runtimeRoot 'logs'
    if (!(Test-Path -LiteralPath $captureRoot -PathType Container)) { throw "PLATFORM: selected profile log directory is not provisioned: $SelectedProfile" }
    $captureId = [Guid]::NewGuid().ToString('N')
    $stdout = Join-Path $captureRoot "launcher-$captureId.stdout.log"
    $stderr = Join-Path $captureRoot "launcher-$captureId.stderr.log"
    $child = Start-Process -FilePath $pwshExe -ArgumentList @('-NoProfile', '-NonInteractive', '-EncodedCommand', $encoded) -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $child.WaitForExit()
    $exitCode = $child.ExitCode
    $lines = @(Get-Content -LiteralPath $stdout -Encoding UTF8) + @(Get-Content -LiteralPath $stderr -Encoding UTF8)
    Remove-Item -LiteralPath $stdout, $stderr -ErrorAction SilentlyContinue
    $child.Dispose()
    if ($exitCode -ne 0) {
        $failure = @($lines | ForEach-Object { [string]$_ } | Where-Object { $_.StartsWith('MANAGED_FAILURE: ') }) | Select-Object -Last 1
        $detail = if ($failure) { $failure.Substring('MANAGED_FAILURE: '.Length) } else { '子进程异常退出，请检查运行日志。' }
        throw "PLATFORM: 受管入口失败（$SelectedProfile/$ManagedAction/$Service）：$detail"
    }
    return ($lines -join "`n")
}

function Get-ManagedStatus([string]$SelectedProfile, [string]$Name) {
    return ((Invoke-Managed $SelectedProfile 'status' $Name) | ConvertFrom-Json)
}

function Assert-StartResources([string]$Name) {
    $memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
    $remaining = ($memory.CommitLimit - $memory.CommittedBytes) / 1GB
    $required = if ($Name -eq 'frontend') { 0.25 } else { 3.5 }
    if ($remaining -lt $required) {
        throw ('PLATFORM: 启动 {0} 前提交内存余量不足：{1:N2} GiB，需要至少 {2:N2} GiB。请关闭闲置程序后再次双击；已启动服务会保留。' -f $Name, $remaining, $required)
    }
}

function Assert-NoUnmanagedProcess([string]$Name) {
    $pattern = switch ($Name) {
        'worker' { 'chrom_platform\.worker_cli' }
        'api' { 'uvicorn.*chrom_platform\.app:create_app_from_environment.*--port\s+9012(?:\s|$)' }
        'frontend' { [regex]::Escape((Join-Path $repoRoot 'frontend')) + '.*vite.*--port\s+5174(?:\s|$)' }
    }
    $other = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|node)\.exe$' -and $_.CommandLine -match $pattern })
    if ($other.Count) { throw "PLATFORM: 发现未受管的 $Name 进程，已避免重复启动；请先核查现有进程。" }
}

function Wait-ServiceReady([string]$Name, $Status, [string]$SelectedProfile) {
    $expectedProcess = Get-Process -Id $Status.pid -ErrorAction Stop
    $expectedStart = $expectedProcess.StartTime.ToUniversalTime()
    $seconds = if ($Name -eq 'frontend') { 60 } else { 300 }
    $deadline = [DateTime]::UtcNow.AddSeconds($seconds)
    Write-Host "等待 $Name 就绪（PID $($Status.pid)）..."
    while ([DateTime]::UtcNow -lt $deadline) {
        $process = Get-Process -Id $Status.pid -ErrorAction SilentlyContinue
        if (!$process -or $process.StartTime.ToUniversalTime() -ne $expectedStart) { throw "PLATFORM: $Name 进程已退出或身份改变；未自动重试。请检查运行日志。" }
        if ($Name -eq 'worker') {
            try {
                $marker = Get-Content -LiteralPath $script:readyFile -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($marker.ready -eq $true -and [int]$marker.pid -eq [int]$Status.pid) {
                    $health = & $pythonExe $checkEntry --profile $SelectedProfile --worker-healthcheck 2>&1
                    if ($LASTEXITCODE -ne 0) { throw 'PLATFORM: Worker 身份健康检查未通过；未重启或删除标记。' }
                    return
                }
            } catch {
                if ($_.Exception.Message.StartsWith('PLATFORM:')) { throw }
            }
        } else {
            $httpReady = $false
            try {
                if ($Name -eq 'api') {
                    $readiness = Invoke-RestMethod $urls[$Name] -TimeoutSec 2
                    $httpReady = $readiness.data.core_ready -eq $true
                    if ($httpReady -and $SelectedProfile -eq 'data-20261026') {
                        $capabilities = (Invoke-RestMethod $capabilitiesUrl -TimeoutSec 2).data
                        $httpReady = $capabilities.build_id -ceq 'data-center-invalid-window-20261026' -and
                            @($capabilities.modules).Count -eq 1 -and $capabilities.modules[0] -ceq 'datasets' -and
                            @($capabilities.chromatography_types).Count -eq 0 -and @($capabilities.assistant_modes).Count -eq 0
                    }
                }
                else { $httpReady = (Invoke-WebRequest $urls[$Name] -TimeoutSec 2).StatusCode -eq 200 }
            } catch {}
            if ($httpReady) {
                $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $ports[$Name] -ErrorAction SilentlyContinue)
                if (!$listeners.Count -or @($listeners | Where-Object { $_.OwningProcess -ne [int]$Status.pid }).Count) { throw "PLATFORM: $Name 监听进程与受管身份不符。" }
                return
            }
        }
        Write-Host '.' -NoNewline
        Start-Sleep -Seconds 3
    }
    throw "PLATFORM: $Name 在 $seconds 秒内未就绪；保留当前进程，请检查日志，不会循环启动。"
}

function Get-ProfileStatuses([string]$SelectedProfile) {
    $profileDirectory = if ($SelectedProfile -eq 'data-20261026') { 'data-accept-20261026' } else { 'assistant-accept-20261006' }
    $profileRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) ('ChromatographyPlatform\' + $profileDirectory)
    if (!(Test-Path -LiteralPath $profileRoot -PathType Container)) {
        $stopped = foreach ($service in @('api', 'worker', 'frontend')) { [pscustomobject]@{ service=$service; status='STOPPED'; pid=$null; created_at=$null; config=$null } }
        return @($stopped)
    }
    if (!(Test-Path -LiteralPath (Join-Path $profileRoot 'logs') -PathType Container)) { throw "PLATFORM: selected profile runtime has no protected log directory: $SelectedProfile" }
    return @((Invoke-Managed $SelectedProfile 'status' 'all') | ConvertFrom-Json)
}

function Assert-OtherProfileStopped([string]$SelectedProfile) {
    $other = $profiles | Where-Object { $_ -cne $SelectedProfile } | Select-Object -First 1
    $running = @(Get-ProfileStatuses $other | Where-Object { $_.status -eq 'RUNNING' })
    if ($running.Count) { throw "PLATFORM: profile $other still owns managed services; stop it through this wrapper after its idle gate before switching profiles." }
    if ($SelectedProfile -eq 'data-20261026') {
        Assert-ProfileIdle $other
    } elseif ($other -eq 'data-20261026') {
        $otherRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform\data-accept-20261026'
        $receipt = Join-Path $otherRoot 'state\last-platform-start.json'
        if (Test-Path -LiteralPath $receipt -PathType Leaf) { Assert-ProfileIdle $other }
    }
}

function Assert-PortsOwnedBySelectedProfile($Statuses) {
    foreach ($name in @('api', 'frontend')) {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $ports[$name] -ErrorAction SilentlyContinue)
        if (!$listeners.Count) { continue }
        $status = @($Statuses | Where-Object { $_.service -eq $name -and $_.status -eq 'RUNNING' }) | Select-Object -First 1
        if ($null -eq $status -or @($listeners | Where-Object { $_.OwningProcess -ne [int]$status.pid }).Count) {
            throw "PLATFORM: shared port $($ports[$name]) is owned by an unmanaged or different-profile process; nothing was stopped."
        }
    }
}

function Assert-ProfileIdle([string]$SelectedProfile) {
    Write-Host "只读检查 $SelectedProfile 的任务、Outbox 和专属队列..."
    $idle = & $pythonExe $checkEntry --profile $SelectedProfile --idle 2>&1
    if ($LASTEXITCODE -ne 0) { throw "PLATFORM: $SelectedProfile idle gate failed; no task was cancelled, replayed, or modified." }
}

function Wait-ProfileStopped([string]$SelectedProfile) {
    foreach ($name in @('worker', 'api', 'frontend')) {
        $status = Get-ManagedStatus $SelectedProfile $name
        if ($status.status -eq 'RUNNING') { throw "PLATFORM: $SelectedProfile $name remains running after managed stop." }
    }
}

try {
    try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
    if (!$owned) { Write-Host '已有启动窗口正在工作，请等待该窗口完成；没有重复启动。'; exit 0 }
    foreach ($path in @($managedEntry, $checkEntry, $pythonExe, $pwshExe)) { if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw 'PLATFORM: 启动所需的现有文件或运行环境缺失；未安装或修改依赖。' } }
    if ($Action -eq 'status') {
        $current = Get-ProfileStatuses $Profile
        $otherProfile = $profiles | Where-Object { $_ -cne $Profile } | Select-Object -First 1
        $other = Get-ProfileStatuses $otherProfile
        @{selected_profile=$Profile; selected=$current; other_profile=$otherProfile; other=$other} | ConvertTo-Json -Depth 6
        exit 0
    }
    $null = Invoke-Managed $Profile 'validate' 'all'
    $config = Get-Content -LiteralPath (Join-Path $runtimeRoot 'runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $script:readyFile = [string]$config.WORKER_READY_FILE
    $config = $null
    $statuses = Get-ProfileStatuses $Profile
    Assert-PortsOwnedBySelectedProfile $statuses
    if ($Action -eq 'stop') {
        Assert-ProfileIdle $Profile
        foreach ($name in @('worker', 'api', 'frontend')) {
            $status = Get-ManagedStatus $Profile $name
            if ($status.status -eq 'RUNNING') { Write-Host (Invoke-Managed $Profile 'stop' $name) }
        }
        Wait-ProfileStopped $Profile
        Write-Host "已停止 $Profile；没有操作另一个 profile。"
        exit 0
    }
    Assert-OtherProfileStopped $Profile
    Write-Host "启动隔离 profile：$Profile。共享端口 9012/5174；不会自动停止其他进程。"
    if ($Profile -eq 'data-20261026') { Write-Host '数据窗口只装配 datasets；预测、科学模型和助手能力为 omitted，不报告其 readiness。' }
    foreach ($name in @('frontend', 'api', 'worker')) {
        $status = Get-ManagedStatus $Profile $name
        if ($status.status -ne 'RUNNING') {
            Assert-NoUnmanagedProcess $name
            Assert-StartResources $name
            if ($name -eq 'worker') { Assert-ProfileIdle $Profile }
            Write-Host (Invoke-Managed $Profile 'start' $name)
            $status = Get-ManagedStatus $Profile $name
            if ($status.status -ne 'RUNNING') { throw "PLATFORM: $Profile $name 未留下有效受管进程；未重复启动。" }
        } else { Write-Host "$Profile $name 已运行，复用 PID $($status.pid)，不重启。" }
        Wait-ServiceReady $name $status $Profile
        Write-Host "`n$Profile/$name 已就绪。"
        if ($name -eq 'frontend') { Write-Host "页面入口：$($urls.frontend)" }
    }
    $finalStatus = Get-ProfileStatuses $Profile
    $receipt = @{at_utc=[DateTime]::UtcNow.ToString('o'); profile=$Profile; status='READY'; services=$finalStatus; url=$urls.frontend}
    $receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runtimeRoot 'state\last-platform-start.json') -Encoding UTF8
    Write-Host "$Profile 已就绪：$($urls.frontend)"
    if (!$NoBrowser) { Start-Process $urls.frontend }
} catch {
    $message = if ($_.Exception.Message.StartsWith('PLATFORM:')) { $_.Exception.Message } else { '启动检查失败：' + $_.Exception.GetType().Name }
    Write-Host "`n$message" -ForegroundColor Red
    Write-Host "日志目录：$(Join-Path $runtimeRoot 'logs')"
    exit 1
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
