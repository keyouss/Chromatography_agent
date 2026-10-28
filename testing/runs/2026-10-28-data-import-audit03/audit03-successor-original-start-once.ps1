#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-f0-9]{64}$')]
    [string]$BindingSha256
)

$ErrorActionPreference = 'Stop'
$repo = 'D:\Jupyter\Chromatography_agent'
$run = Join-Path $repo 'testing\runs\2026-10-28-data-import-audit03'
$isolated = Join-Path $run 'audit03-successor-original-restore-01'
$entry = Join-Path $isolated 'start-platform.ps1'
$manifestPath = Join-Path $isolated 'manifest.json'
$templatePath = Join-Path $isolated 'authorization.template.json'
$authPath = Join-Path $isolated 'authorization.json'
$receiptPath = Join-Path $isolated 'audit03-successor-original-start-01.json'
$bindingPath = Join-Path $run 'audit03-successor-original-start-binding.json'
$helperPath = Join-Path $run 'audit03-successor-config-restore.py'
$python = 'D:\Anaconda\envs\SFC\python.exe'
$pwsh = Join-Path $PSHOME 'pwsh.exe'
$manifestSha256 = 'c68792eb808d0885106e9384fa6a0ff8ff5fb1d4acb51e8ad4ac1b8d7a8fbb20'
$templateSha256 = '969966a3b8733b2bc90078b15d096069d864d3542645c34c6c72201a4e81078c'
$helperSha256 = '2268c3369eec3ba0d3a1c9a5e9f7e8630bf07b2256a7b7994e3a6844c56856d8'
$windowId = 'audit03-successor-binding-01'
$authOwned = $false
$receiptOwned = $false
$startChild = $null
$r = [ordered]@{
    status = 'PRECHECK'
    window_id = $windowId
    profile = 'assistant-20261006'
    binding_path = 'testing/runs/2026-10-28-data-import-audit03/audit03-successor-original-start-binding.json'
    binding_sha256 = $BindingSha256
    start_invocations = 0
    assistant_stop_invocations = 0
    data_start_invocations = 0
    data_stop_invocations = 0
    business_posts = 0
    native_scans = 0
    repeat_start = $false
    started_at_utc = [DateTime]::UtcNow.ToString('o')
}

function Get-Sha256([string]$File) {
    if (!(Test-Path -LiteralPath $File -PathType Leaf)) { throw 'A fixed input file is missing' }
    return (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Save-Receipt {
    $json = ($r | ConvertTo-Json -Depth 12) + "`n"
    [IO.File]::WriteAllText($receiptPath, $json, [Text.UTF8Encoding]::new($false))
}
function Write-ExclusiveJson([string]$File, $Value) {
    $json = ($Value | ConvertTo-Json -Depth 12) + "`n"
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
    $stream = [IO.File]::Open($File, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}
function Assert-StaticInputs {
    if ((Get-Sha256 $manifestPath) -cne $manifestSha256) { throw 'Static manifest hash mismatch' }
    if ((Get-Sha256 $templatePath) -cne $templateSha256) { throw 'Inactive authorization template hash mismatch' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.status -cne 'PREPARED_INACTIVE_SINGLE_ASSISTANT_ORIGINAL_START' -or
        $manifest.window_id -cne $windowId -or $manifest.profile -cne 'assistant-20261006' -or
        $manifest.minimum_commit_headroom_bytes_decimal_str -cne '3758096384') { throw 'Static manifest binding mismatch' }
    if (@($manifest.files).Count -ne 4 -or @($manifest.shared_source_pins).Count -ne 4 -or
        $manifest.helper_source.path -cne 'audit03-successor-config-restore.py') { throw 'Static manifest file set mismatch' }
    if ($null -eq $helperSha256 -or $manifest.helper_source.sha256 -cne $helperSha256 -or
        $helperSha256 -notmatch '^[a-f0-9]{64}$') { throw 'Config-restore helper source pin is not frozen' }
    if ((Get-Sha256 $helperPath) -cne $helperSha256) { throw 'Config-restore helper source drift' }
    foreach ($file in $manifest.files) {
        if ($file.path -notin @('start-platform.ps1', 'run-local.ps1', 'dpapi-secret-source.ps1', 'startup-check.py')) { throw 'Unexpected local static file' }
        if ((Get-Sha256 (Join-Path $isolated $file.path)) -cne $file.sha256) { throw 'Isolated static file drift' }
    }
    if (@($manifest.source_files).Count -ne 4) { throw 'Static source lineage set mismatch' }
    foreach ($file in $manifest.source_files) {
        $source = Join-Path $repo ($file.path.Replace('/', '\'))
        if ((Get-Sha256 $source) -cne $file.sha256) { throw 'Original static source drift' }
    }
    foreach ($file in $manifest.shared_source_pins) {
        $source = Join-Path $repo ($file.path.Replace('/', '\'))
        if ((Get-Sha256 $source) -cne $file.sha256) { throw 'Canonical shared source drift' }
    }
    $template = Get-Content -LiteralPath $templatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($template.status -cne 'TEMPLATE_NOT_ACTIVE' -or $template.window_id -cne $windowId -or
        $template.binding_sha256 -ne $null -or $template.allowed_action -cne 'start') { throw 'Authorization template is not inert' }
}
function Assert-HelperResult {
    if ((Get-Sha256 $bindingPath) -cne $BindingSha256) { throw 'Original-start binding hash mismatch' }
    foreach ($file in @($python, $pwsh, $entry, $helperPath)) {
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw 'A fixed original-start input is missing' }
    }
    $raw = @(& $python -B -I $helperPath --check-start --binding-sha256 $BindingSha256 2>&1)
    $code = $LASTEXITCODE
    if ($code -ne 0 -or $raw.Count -ne 1) { throw 'Read-only original-start gate did not return one result' }
    try { $result = ([string]$raw[0]) | ConvertFrom-Json } catch { throw 'Read-only original-start gate output was invalid' }
    $required = @('binding_hash', 'approval_scope_plan_qa', 'stop_receipt', 'assistant_config', 'data_config_baseline', 'config_identity', 'backup_bytes_identity', 'both_profiles_stopped', 'ports_free', 'queue_and_leases', 'preserved_unknown_exception', 'resources')
    if ($result.status -cne 'READY_FOR_ORIGINAL_START' -or $result.binding_sha256 -cne $BindingSha256 -or
        @($result.checks.PSObject.Properties).Count -ne $required.Count) { throw 'Original-start gate binding mismatch' }
    foreach ($name in $required) {
        if ($result.checks.PSObject.Properties.Name -cnotcontains $name -or $result.checks.$name -ne $true) { throw 'An original-start gate check failed' }
    }
    if ((Get-Sha256 $bindingPath) -cne $BindingSha256 -or (Get-Sha256 $helperPath) -cne $helperSha256) { throw 'Original-start input changed during preflight' }
    $r.readonly_gate = $result
}

if (Test-Path -LiteralPath $receiptPath) { throw 'Single-use original-start receipt already exists' }
if (Test-Path -LiteralPath $authPath) { throw 'Single-use original-start authorization already exists' }
try {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Normal MSIX token required' }
    Assert-StaticInputs
    if ((Get-Sha256 $bindingPath) -cne $BindingSha256) { throw 'Original-start binding hash mismatch' }
    Assert-HelperResult
    $r.status = 'PRECHECK_PASSED'
    Write-ExclusiveJson $receiptPath $r
    $receiptOwned = $true
    $template = Get-Content -LiteralPath $templatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-ExclusiveJson $authPath ([ordered]@{
        schema_version = $template.schema_version
        status = 'ACTIVE_ORIGINAL_START'
        window_id = $windowId
        profile = 'assistant-20261006'
        allowed_action = 'start'
        binding_path = $template.binding_path
        binding_sha256 = $BindingSha256
        created_at_utc = [DateTime]::UtcNow.ToString('o')
        wrapper_receipt = 'audit03-successor-original-start-01.json'
    })
    $authOwned = $true
    $r.start_invocations = 1
    $r.status = 'ASSISTANT_START_IN_PROGRESS'
    $logId = [Guid]::NewGuid().ToString('N')
    $stdout = Join-Path $isolated "assistant-start-$logId.stdout.log"
    $stderr = Join-Path $isolated "assistant-start-$logId.stderr.log"
    $r.stdout_log = [IO.Path]::GetFileName($stdout)
    $r.stderr_log = [IO.Path]::GetFileName($stderr)
    Save-Receipt
    $startChild = Start-Process -FilePath $pwsh -ArgumentList @('-NoProfile', '-File', $entry, '-Profile', 'assistant-20261006', '-Action', 'start', '-NoBrowser') -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $null = $startChild.Handle
    $r.child_pid = [string]$startChild.Id
    $r.child_creation_utc = $startChild.StartTime.ToUniversalTime().ToString('o')
    Save-Receipt
    $startChild.WaitForExit()
    $startChild.Refresh()
    $r.start_exit_code = $startChild.ExitCode
    if ($null -eq $startChild.ExitCode -or $startChild.ExitCode -ne 0) { throw 'Assistant start failed; no repeat start' }
    $r.status = 'ASSISTANT_START_EXIT_ZERO_PENDING_INDEPENDENT_HEALTH'
} catch {
    $r.status = 'STOPPED_ON_FAILURE_NO_REPEAT_START'
    $r.error_type = $_.Exception.GetType().Name
    $r.error_reason = switch ($r.error_type) {
        'FileNotFoundException' { 'FIXED_INPUT_MISSING' }
        'DirectoryNotFoundException' { 'FIXED_INPUT_DIRECTORY_MISSING' }
        'UnauthorizedAccessException' { 'FIXED_INPUT_ACCESS_DENIED' }
        'IOException' { 'EXCLUSIVE_FILE_CONFLICT' }
        'InvalidDataException' { 'GATE_OUTPUT_INVALID' }
        default { 'UNCLASSIFIED' }
    }
} finally {
    if ($authOwned -and (Test-Path -LiteralPath $authPath -PathType Leaf)) {
        $closedOk = $false
        try {
            $closed = Get-Content -LiteralPath $authPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($closed.window_id -ceq $windowId -and $closed.binding_sha256 -ceq $BindingSha256 -and $closed.status -ceq 'ACTIVE_ORIGINAL_START') {
                $closed.status = 'CLOSED'
                $null = $closed | Add-Member -NotePropertyName closed_at_utc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
                [IO.File]::WriteAllText($authPath, (($closed | ConvertTo-Json -Depth 8) + "`n"), [Text.UTF8Encoding]::new($false))
                $verified = Get-Content -LiteralPath $authPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $closedOk = $verified.status -ceq 'CLOSED' -and $verified.window_id -ceq $windowId -and $verified.binding_sha256 -ceq $BindingSha256
            }
        } catch { $r.authorization_close_error = $_.Exception.GetType().Name }
        if (!$closedOk) { $r.status = 'STOPPED_ON_FAILURE_AUTH_NOT_CLOSED' }
    }
    if ($receiptOwned) {
        $r.finished_at_utc = [DateTime]::UtcNow.ToString('o')
        Save-Receipt
    }
    if ($null -ne $startChild) { $startChild.Dispose() }
}
if ($r.status -cne 'ASSISTANT_START_EXIT_ZERO_PENDING_INDEPENDENT_HEALTH') { exit 1 }
exit 0
