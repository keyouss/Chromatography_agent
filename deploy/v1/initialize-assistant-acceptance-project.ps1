$ErrorActionPreference = 'Stop'
$localRoot = [Environment]::GetFolderPath('LocalApplicationData')
$formalRoot = [IO.Path]::GetFullPath((Join-Path $localRoot 'ChromatographyPlatform\priority-local'))
$formalRuntimePath = [IO.Path]::GetFullPath((Join-Path $formalRoot 'runtime.json'))
$acceptanceRoot = [IO.Path]::GetFullPath((Join-Path $localRoot 'ChromatographyPlatform\assistant-accept-20261006'))
$acceptanceRuntimePath = [IO.Path]::GetFullPath((Join-Path $acceptanceRoot 'runtime.json'))
$repoRoot = 'D:\Jupyter\Chromatography_agent'
$dpapiHelper = Join-Path $repoRoot 'deploy\v1\dpapi-secret-source.ps1'
$bootstrapScript = Join-Path $repoRoot 'deploy\v1\initialize_local_project.py'
$python = 'D:\Anaconda\envs\SFC\python.exe'
$urlEnvironmentName = 'CHROM_ACCEPTANCE_FORMAL_SYNC_URL'

foreach ($path in @($formalRuntimePath, $acceptanceRuntimePath, $dpapiHelper, $bootstrapScript, $python)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'A fixed bootstrap input is missing' }
}

. $dpapiHelper
Assert-DpapiNoReparse $formalRoot 'Formal runtime root' | Out-Null
Assert-DpapiProtectedAcl $formalRoot $true 'Formal runtime root'
Assert-DpapiNoReparse $acceptanceRoot 'Acceptance runtime root' | Out-Null
Assert-DpapiProtectedAcl $acceptanceRoot $true 'Acceptance runtime root'
Assert-DpapiNoReparse $formalRuntimePath 'Formal runtime file' | Out-Null
Assert-DpapiProtectedAcl $formalRuntimePath $false 'Formal runtime file'
Assert-DpapiNoReparse $acceptanceRuntimePath 'Acceptance runtime file' | Out-Null
Assert-DpapiProtectedAcl $acceptanceRuntimePath $false 'Acceptance runtime file'

$formal = Get-Content -LiteralPath $formalRuntimePath -Raw -Encoding UTF8 | ConvertFrom-Json
$acceptance = Get-Content -LiteralPath $acceptanceRuntimePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$acceptance.CHROM_ACCEPTANCE_DATABASE_SCHEMA -cne 'chrom_assistant_accept_20261006') {
    throw 'Acceptance schema binding is not the fixed isolated target'
}
if ([string]$acceptance.CHROM_DEFAULT_PROJECT_ID -cne 'priority-local') {
    throw 'Acceptance project binding is not the expected project'
}
$configuredProjects = @(([string]$acceptance.CHROM_PROJECT_IDS -split ',') | ForEach-Object { $_.Trim() })
if ($configuredProjects -notcontains 'priority-local') { throw 'Acceptance actor scope does not contain the fixed project' }
$formalSyncUrl = [string]$formal.SYNC_DATABASE_URL
if ([string]::IsNullOrWhiteSpace($formalSyncUrl)) { throw 'Protected formal sync database binding is unavailable' }

try {
    [Environment]::SetEnvironmentVariable($urlEnvironmentName, $formalSyncUrl, 'Process')
    & $python -B $bootstrapScript --acceptance
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw "Acceptance project bootstrap failed with exit code $exitCode" }
} finally {
    [Environment]::SetEnvironmentVariable($urlEnvironmentName, $null, 'Process')
    Remove-Item -LiteralPath "Env:$urlEnvironmentName" -ErrorAction SilentlyContinue
    $formalSyncUrl = $null
    $formal = $null
    $acceptance = $null
}
