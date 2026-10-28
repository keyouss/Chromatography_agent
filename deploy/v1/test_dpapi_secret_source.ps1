[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'dpapi-secret-source.ps1')

$root = Join-Path ([IO.Path]::GetTempPath()) "chrom-dpapi-test-$PID"
$secret = 'synthetic-secret-value-for-dpapi-test'
$secretPath = Join-Path $root 'secret-source.dpapi'
$childOutput = @{}
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
$initialProcessApiKey = $null

function Assert-ParentApiKeyRestored {
    $current = [Environment]::GetEnvironmentVariable('API_KEY', 'Process')
    $initialHad = $null -ne $initialProcessApiKey
    $currentHad = $null -ne $current
    if ($currentHad -ne $initialHad -or ($initialHad -and $current -cne $initialProcessApiKey)) {
        throw ("parent API_KEY was not restored initial_present={0} current_present={1} initial_length={2} current_length={3}" -f $initialHad, $currentHad, $(if ($null -eq $initialProcessApiKey) { 0 } else { $initialProcessApiKey.Length }), $(if ($null -eq $current) { 0 } else { $current.Length }))
    }
}

function Invoke-PresenceChild([bool]$NeedsSecret, [string]$SecretValue) {
    $old = [Environment]::GetEnvironmentVariable('API_KEY', 'Process')
    $had = $null -ne $old
    $outName = if ($NeedsSecret) { 'api-worker-child.txt' } else { 'frontend-child.txt' }
    $out = Join-Path $root $outName
    try {
        if ($NeedsSecret) { [Environment]::SetEnvironmentVariable('API_KEY', $SecretValue, 'Process') }
        else { Remove-DpapiProcessApiKey }
        $code = '$present = -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable(''API_KEY'',''Process'')); Write-Output (''PRESENT='' + $present)'
        $p = Start-Process -FilePath $pwsh -ArgumentList @('-NoProfile', '-Command', $code) -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError "$out.err" -PassThru
        if (!$p.WaitForExit(15000)) { throw 'child timeout' }
        if ($p.ExitCode -ne 0) { throw 'child failed' }
        return (Get-Content -LiteralPath $out -Raw -Encoding UTF8).Trim()
    } finally {
        if ($had) { [Environment]::SetEnvironmentVariable('API_KEY', $old, 'Process') }
        else { Remove-DpapiProcessApiKey }
    }
}

try {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    Set-DpapiProtectedAcl $root $true 'Synthetic runtime directory'
    try {
        Protect-DpapiSecretToFile $secret $secretPath $root | Out-Null
    } catch {
        if ($_.Exception.Message -like 'DPAPI_PROTECT_FAILED*') {
            $blockedMessage = "DPAPI_TEST_BLOCKED $($_.Exception.Message)"
            Write-Output $blockedMessage
            throw $blockedMessage
        }
        throw
    }
    $roundtrip = Unprotect-DpapiSecretFromFile $secretPath $root
    if ($roundtrip -cne $secret) { throw 'DPAPI round-trip mismatch' }
    $initialProcessApiKey = [Environment]::GetEnvironmentVariable('API_KEY', 'Process')
    $childOutput.api = Invoke-PresenceChild $true $roundtrip
    Assert-ParentApiKeyRestored
    $childOutput.worker = Invoke-PresenceChild $true $roundtrip
    Assert-ParentApiKeyRestored
    $childOutput.frontend = Invoke-PresenceChild $false $roundtrip
    Assert-ParentApiKeyRestored
    if ($childOutput.api -ne 'PRESENT=True' -or $childOutput.worker -ne 'PRESENT=True' -or $childOutput.frontend -ne 'PRESENT=False') { throw 'child secret presence contract mismatch' }
    try { Assert-DpapiUnderRoot (Join-Path $root '..\outside.dpapi') $root 'Traversal probe' | Out-Null; throw 'traversal accepted' } catch { if ($_.Exception.Message -eq 'traversal accepted') { throw } }
    $bad = Join-Path $root 'bad.dpapi'
    [IO.File]::WriteAllBytes($bad, [byte[]](1, 2, 3, 4))
    Set-DpapiProtectedAcl $bad $false 'Corrupt synthetic secret'
    try { Unprotect-DpapiSecretFromFile $bad $root | Out-Null; throw 'corrupt ciphertext accepted' } catch { if ($_.Exception.Message -eq 'corrupt ciphertext accepted') { throw } }
    $leaked = @(Get-ChildItem -LiteralPath $root -Recurse -File | Select-String -SimpleMatch $secret -List)
    if ($leaked.Count -ne 0) { throw 'synthetic secret leaked to test files' }
    Write-Output ('DPAPI_TEST_OK api={0} worker={1} frontend={2} ciphertext_bytes={3}' -f $childOutput.api, $childOutput.worker, $childOutput.frontend, (Get-Item -LiteralPath $secretPath).Length)
} finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue }
}
