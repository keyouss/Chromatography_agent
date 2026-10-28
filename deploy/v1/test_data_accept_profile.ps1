[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$runLocalPath = Join-Path $PSScriptRoot 'run-local.ps1'
$launcherPath = Join-Path $PSScriptRoot 'start-platform.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($runLocalPath, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'run-local.ps1 has PowerShell parse errors' }
$launcherTokens = $null
$launcherErrors = $null
$launcherAst = [System.Management.Automation.Language.Parser]::ParseFile($launcherPath, [ref]$launcherTokens, [ref]$launcherErrors)
if ($launcherErrors.Count) { throw 'start-platform.ps1 has PowerShell parse errors' }

foreach ($name in @('Fail', 'Get-Sha256', 'Test-DataAcceptanceManifest', 'Test-DataAcceptanceDatabaseUrl', 'Test-DataRuntimePackage', 'Set-PythonBytecodePolicy', 'Test-DataLauncherParentIdentity')) {
    $function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if ($null -eq $function) { throw "Pure binding function missing: $name" }
    Invoke-Expression $function.Extent.Text
}
$secretSourcePath = Join-Path $PSScriptRoot 'dpapi-secret-source.ps1'
$secretTokens = $null
$secretErrors = $null
$secretAst = [System.Management.Automation.Language.Parser]::ParseFile($secretSourcePath, [ref]$secretTokens, [ref]$secretErrors)
if ($secretErrors.Count) { throw 'dpapi-secret-source.ps1 has PowerShell parse errors' }
$noReparse = $secretAst.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-DpapiNoReparse' }, $true)
if ($null -eq $noReparse) { throw 'No-reparse guard is missing' }
Invoke-Expression $noReparse.Extent.Text

function Assert-True([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
}

$parentShell = Join-Path $PSHOME 'pwsh.exe'
$parentWrapper = Join-Path $PSScriptRoot 'start-platform.ps1'
$parentTime = [datetime]::UtcNow.AddSeconds(-5)
$parentFixture = [pscustomobject]@{ProcessId=100; CreationDate=$parentTime; ExecutablePath=$parentShell; CommandLine=''}
$childFixture = [pscustomobject]@{ProcessId=101; ParentProcessId=100; CreationDate=$parentTime.AddSeconds(1)}
foreach ($validAction in @('start','stop')) {
    $parentFixture.CommandLine = '"' + $parentShell + '" -NoProfile -File "' + $parentWrapper + '" -Profile data-20261026 -Action ' + $validAction + ' -NoBrowser'
    Assert-True (Test-DataLauncherParentIdentity $parentFixture $childFixture $validAction $parentWrapper $parentShell) 'Valid live canonical wrapper parent rejected'
}
$validParentCommand = '"' + $parentShell + '" -NoProfile -File "' + $parentWrapper + '" -Profile data-20261026 -Action start'
$badParentCommands = @(
    ('"' + $parentShell + '" -NoProfile -Command "& ''' + $parentWrapper + ''' -Profile data-20261026 -Action start"'),
    $validParentCommand.Replace('-File', '-EncodedCommand'),
    $validParentCommand.Replace('start-platform.ps1','start-platform.ps1.evil.ps1'),
    $validParentCommand.Replace('data-20261026','assistant-20261006'),
    ($validParentCommand + ' -Action stop'),
    ($validParentCommand + '; Write-Output injected')
)
foreach ($badParent in $badParentCommands) {
    $parentFixture.CommandLine = $badParent
    Assert-True (!(Test-DataLauncherParentIdentity $parentFixture $childFixture 'start' $parentWrapper $parentShell)) 'Forged parent command passed the mutation gate'
}
$parentFixture.CommandLine = $validParentCommand
Assert-True (!(Test-DataLauncherParentIdentity $parentFixture $childFixture 'stop' $parentWrapper $parentShell)) 'Wrong parent action passed'
Assert-True (!(Test-DataLauncherParentIdentity $parentFixture $childFixture 'restart' $parentWrapper $parentShell)) 'Unsupported wrapper restart passed'
Assert-True (!(Test-DataLauncherParentIdentity $null $childFixture 'start' $parentWrapper $parentShell)) 'A caller switch without a real parent passed'

function Assert-Rejected([scriptblock]$Action, [string]$Name) {
    try { & $Action } catch { return }
    throw "$Name was accepted"
}

$manifestPath = Join-Path $repo 'builds\candidates\data-center-invalid-window-20261026\profile.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-True (Test-DataAcceptanceManifest $manifest) 'Fixed dataset-only manifest was rejected'
Assert-True (Test-DataAcceptanceDatabaseUrl 'DATABASE_URL' 'mysql+asyncmy://chrom_data_accept_runtime:protected%2Fsecret@127.0.0.1:3306/chrom_data_accept_20261026') 'Dedicated async runtime URL was rejected'
Assert-True (Test-DataAcceptanceDatabaseUrl 'SYNC_DATABASE_URL' 'mysql+pymysql://chrom_data_accept_runtime:protected%2Fsecret@127.0.0.1:3306/chrom_data_accept_20261026') 'Dedicated sync runtime URL was rejected'
Assert-True (!(Test-DataAcceptanceDatabaseUrl 'DATABASE_URL' 'mysql+asyncmy://root:protected@127.0.0.1:3306/chrom_data_accept_20261026')) 'Root database principal was accepted'
Assert-True (!(Test-DataAcceptanceDatabaseUrl 'SYNC_DATABASE_URL' 'mysql+pymysql://chrom_data_accept_runtime:protected@127.0.0.1:3306/chrom_platform')) 'Wrong database schema was accepted'
Assert-True (!(Test-DataAcceptanceDatabaseUrl 'DATABASE_URL' 'mysql+pymysql://chrom_data_accept_runtime:protected@127.0.0.1:3306/chrom_data_accept_20261026')) 'Wrong worker driver was accepted'
$oldBytecodePolicy = [Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process')
try {
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', '0', 'Process')
    Set-PythonBytecodePolicy $true
    Assert-True ([Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process') -eq '1') 'Data profile can write Python bytecode'
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', '0', 'Process')
    Set-PythonBytecodePolicy $false
    Assert-True ([Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process') -eq '0') 'Assistant/default bytecode environment was changed'
} finally {
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', $oldBytecodePolicy, 'Process')
}

$wrongModule = $manifest | ConvertTo-Json -Depth 8 | ConvertFrom-Json
$wrongModule.modules = @('datasets', 'assistant')
Assert-Rejected { Test-DataAcceptanceManifest $wrongModule } 'assistant module'

$wrongAdapter = $manifest | ConvertTo-Json -Depth 8 | ConvertFrom-Json
$wrongAdapter.chromatography_types = @('sfc')
Assert-Rejected { Test-DataAcceptanceManifest $wrongAdapter } 'science adapter'

$assistantFeature = $manifest | ConvertTo-Json -Depth 8 | ConvertFrom-Json
$assistantFeature | Add-Member -NotePropertyName assistant_features -NotePropertyValue @('business')
Assert-Rejected { Test-DataAcceptanceManifest $assistantFeature } 'assistant feature'

$packageRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ('.data-package-fixture-' + [Guid]::NewGuid().ToString('N'))))
if (Test-Path -LiteralPath $packageRoot) { throw 'Fixture directory already exists; refusing reuse' }
try {
    $modulesRoot = Join-Path $packageRoot 'chrom_platform\modules\data_center'
    New-Item -ItemType Directory -Path $modulesRoot -Force | Out-Null
    $workerPath = Join-Path $packageRoot 'chrom_platform\worker_cli.py'
    $importPath = Join-Path $modulesRoot 'import_worker.py'
    $buildPath = Join-Path $packageRoot 'build-manifest.json'
    [IO.File]::WriteAllText($workerPath, 'def worker_healthcheck(path): return 0', [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($importPath, 'class DataImportWorker: pass', [Text.UTF8Encoding]::new($false))
    $packageManifest = [ordered]@{
        manifest_version='1.0'; build_id='data-center-invalid-window-20261026'; modules=@('datasets')
        chromatography_types=@(); prediction_tools=[ordered]@{}; utilities=@(); export_formats=@()
        auth_provider='deployment_default'; assistant_features=$null
    }
    $packageManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $buildPath -Encoding UTF8
    $packageFiles = @(Get-ChildItem -LiteralPath $packageRoot -File -Recurse | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring([IO.Path]::GetFullPath($packageRoot).TrimEnd('\').Length + 1).Replace('\', '/')
            sha256 = (Get-Sha256 $_.FullName)
        }
    })
    $sourceManifest = [ordered]@{
        build_id='data-center-invalid-window-20261026'; modules=@('datasets'); chromatography_types=@()
        files=$packageFiles; workers=@('chrom_platform/modules/data_center/import_worker.py')
        worker_entrypoint='chrom_platform/worker_cli.py'
    }
    $sourcePath = Join-Path $packageRoot 'SOURCE_MANIFEST.json'
    $sourceManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $sourcePath -Encoding UTF8
    $reviewedSourceSha = Get-Sha256 $sourcePath
    Assert-Rejected { Test-DataRuntimePackage $packageRoot } 'Missing protected source pin'
    Assert-Rejected { Test-DataRuntimePackage $packageRoot ('0' * 64) } 'Wrong protected source pin'
    Assert-True (Test-DataRuntimePackage $packageRoot $reviewedSourceSha) 'Valid frozen dataset-only backend package was rejected'
    New-Item -ItemType Directory -Path (Join-Path $packageRoot 'chrom_platform\modules\prediction') -Force | Out-Null
    Assert-Rejected { Test-DataRuntimePackage $packageRoot $reviewedSourceSha } 'Unselected physical backend module'
} finally {
    $fixtureParent = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\.data-package-fixture-'
    if (![IO.Path]::GetFullPath($packageRoot).StartsWith($fixtureParent, [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture cleanup target escaped its intended workspace directory' }
    if (Test-Path -LiteralPath $packageRoot) { Remove-Item -LiteralPath $packageRoot -Recurse -Force }
}

$launcherText = Get-Content -LiteralPath $launcherPath -Raw -Encoding UTF8
$runLocalText = Get-Content -LiteralPath $runLocalPath -Raw -Encoding UTF8
Assert-True ($launcherText.Contains("[string]`$Profile = 'assistant-20261006'")) 'Assistant profile default changed'
Assert-True ($launcherText.Contains("'data-20261026'")) 'Data profile is not explicitly supported'
Assert-True ($launcherText.Contains('--profile $SelectedProfile --idle')) 'Launcher does not pass profile to the idle checker'
Assert-True ($launcherText.Contains('--profile $SelectedProfile --worker-healthcheck')) 'Launcher does not bind Worker health to selected profile'
Assert-True ($launcherText.Contains('-LauncherManaged')) 'Launcher-managed data gate is not wired'
Assert-True ($runLocalText.Contains("'data-accept-20261026'")) 'Fixed data runtime root changed'
Assert-True ($runLocalText.Contains("'chrom-platform-data-accept-20261026'")) 'Fixed data queue changed'
Assert-True ($runLocalText.Contains("'chrom_data_accept_20261026'")) 'Fixed data schema changed'
Assert-True ($runLocalText.Contains("'project_data_accept_20261026'")) 'Fixed data project scope changed'
Assert-True ($runLocalText.Contains('Data acceptance start/stop is available only through start-platform.ps1')) 'Bare data run-local start/stop remains available'
Assert-True ($runLocalText.Contains("deploy\local-packages\data-center-invalid-window-20261026")) 'Data profile is not pinned to its local candidate package'
Assert-True ($runLocalText.Contains('chrom_data_accept_runtime')) 'Data DB URL does not require the dedicated runtime principal'
Assert-True ($runLocalText.Contains('Set-PythonBytecodePolicy $DataAcceptanceMode')) 'Data-only no-bytecode process policy is not wired'
Assert-True ($runLocalText.Contains('Data acceptance backend package contains science adapters')) 'Science adapters are not excluded from the package'
Assert-True ($runLocalText.Contains('Data acceptance backend package contains model weights')) 'Model weights are not excluded from the package'
Write-Output 'PASS fixed dataset-only manifest, fail-closed capability mutations, profile bindings, and launcher-only data start/stop gate'
