[CmdletBinding()]
param(
    [ValidateSet('start', 'stop', 'restart', 'status', 'validate')]
    [string]$Action = 'start',
    [ValidateSet('api', 'worker', 'frontend', 'all')]
    [string]$Service = 'all',
    [string]$ConfigFile = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform\priority-local\runtime.json'),
    [switch]$Acceptance,
    [ValidateSet('assistant-20261006', 'data-20261026')]
    [string]$AcceptanceProfile,
    [Parameter(DontShow = $true)]
    [switch]$LauncherManaged,
    [string]$Python = 'D:\Anaconda\envs\SFC\python.exe',
    [string]$Node = 'C:\Program Files\nodejs\node.exe'
)

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$localRuntimeBase = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform'
$formalConfig = [IO.Path]::GetFullPath((Join-Path $localRuntimeBase 'priority-local\runtime.json'))
$acceptanceProfileSelected = if ($Acceptance) { if ($AcceptanceProfile) { $AcceptanceProfile } else { 'assistant-20261006' } } else { $AcceptanceProfile }
if ($Acceptance -and $AcceptanceProfile -and $AcceptanceProfile -cne 'assistant-20261006') { throw 'Legacy -Acceptance is bound to assistant-20261006; select data profile without -Acceptance' }
$AcceptanceMode = [bool]($Acceptance -or $AcceptanceProfile)
$DataAcceptanceMode = $AcceptanceProfileSelected -ceq 'data-20261026'
$acceptanceDirectory = if ($DataAcceptanceMode) { 'data-accept-20261026' } else { 'assistant-accept-20261006' }
$acceptanceRoot = [IO.Path]::GetFullPath((Join-Path $localRuntimeBase $acceptanceDirectory))
$acceptanceConfig = [IO.Path]::GetFullPath((Join-Path $acceptanceRoot 'runtime.json'))
$dataManifest = [IO.Path]::GetFullPath((Join-Path $repo 'builds\candidates\data-center-invalid-window-20261026\profile.json'))
$dataFrontendRoot = [IO.Path]::GetFullPath((Join-Path $repo 'frontend\build-output\data-import-audit-20261027\frontend'))
$dataBackendPackageRoot = 'D:\Jupyter\Chromatography_agent\testing\runs\2026-10-27-data-import-audit\backend-runtime-package-data-window-08\data-center-invalid-window-20261026'
if (!$DataAcceptanceMode) {
    $script:AcceptanceSfcFullBindingPolicy = [pscustomobject]@{
    model_id = 'rs12-extra-trees-s80-seed42'
    model_version = '1'
    scope = 'all_column'
    target = 'label_success_rs12'
    threshold = '1.2'
    fixture_version = 'sfc-rs12-full-column-reference-v2'
    fixture_relative_path = 'testing\fixtures\priority-launch\sfc-rs12-full-column-reference-coupled-20261020-v2.json'
    # Fixed pin from the controlled AUTHOR_REFERENCE receipt; verified by the
    # source-binding generator; this revision still needs runtime verification.
    # Runtime uses automatic integrity checks, not manual rehashing.
    reference_sha256 = 'ea41f8db47e3abefc29cd1a4570374f0199eb05f2d91b46f297a04834e25b1b4'
    model_root = 'D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\models'
    registry_sha256 = '42f04a4ceb63024e685d0d8a4a8bd1ca57b12216f4f7f1050216f7ec04228429'
    metadata_sha256 = 'cf54c1dfe850f0d1a0c2e1a75e6665e729d86ce1172e6742e0220f5f7a4c1c84'
    artifact_sha256 = 'f25d39ec61d0b8d4198d4d2bc2993b76f2fc33ba60f13176d475ceb6d654e860'
    platform_catalog_sha256 = '4a76421324aca6865d5dbd7df1e57d5df5362d231d7e3ee3dd34cf4db48c9ab1'
    expected_nan_features = @('mdec_modi', 'mdec_smiles_pair_value', 'mdec_smiles_mean', 'mdec_smiles_median', 'mdec_smiles_abs_diff', 'mdec_smiles_range')
    # Current input/output schema hashes are rechecked by the Python startup gate.
    author_catalog_path = 'D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\data\result_with_advanced_descriptors.sqlite'
    author_catalog_sha256 = '266a6d00df80166c3faf64be9dc21748d9a39a9f7106a24b8741fdd628eb9c1d'
    }
}
if ($AcceptanceMode) {
    if ($PSBoundParameters.ContainsKey('ConfigFile') -and [IO.Path]::GetFullPath($ConfigFile) -ne $acceptanceConfig) { throw 'Acceptance mode accepts only its fixed protected runtime.json' }
    $ConfigFile = $acceptanceConfig
} elseif ([IO.Path]::GetFullPath($ConfigFile) -ne $formalConfig) {
    throw 'Only the protected priority-local runtime.json is accepted; .env and arbitrary runtime files are rejected'
}
if ($DataAcceptanceMode -and $Action -in @('start', 'stop', 'restart') -and !$LauncherManaged) {
    throw 'Data acceptance start/stop is available only through start-platform.ps1 so its cross-profile identity and idle gates cannot be skipped'
}
$runtimeRoot = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($ConfigFile))
$stateRoot = Join-Path $runtimeRoot 'state'
$logRoot = Join-Path $runtimeRoot 'logs'
$pidRoot = Join-Path $stateRoot 'pids'
. (Join-Path $PSScriptRoot 'dpapi-secret-source.ps1')

function Fail([string]$Message) { throw $Message }

function Test-DataLauncherParentIdentity($ParentProcess, $ChildProcess, [string]$RequestedAction, [string]$WrapperPath, [string]$PowerShellPath) {
    if ($RequestedAction -cnotin @('start', 'stop') -or $null -eq $ParentProcess -or $null -eq $ChildProcess) { return $false }
    try {
        if ([int]$ParentProcess.ProcessId -le 0 -or [int]$ChildProcess.ProcessId -le 0 -or
            [int]$ChildProcess.ParentProcessId -ne [int]$ParentProcess.ProcessId -or
            [int]$ChildProcess.ProcessId -eq [int]$ParentProcess.ProcessId -or
            !$ParentProcess.CreationDate -or !$ChildProcess.CreationDate -or
            ([datetime]$ParentProcess.CreationDate).ToUniversalTime() -ge ([datetime]$ChildProcess.CreationDate).ToUniversalTime() -or
            ![string]::Equals([IO.Path]::GetFullPath([string]$ParentProcess.ExecutablePath), [IO.Path]::GetFullPath($PowerShellPath), [StringComparison]::OrdinalIgnoreCase)) { return $false }
        $exe = [regex]::Escape([IO.Path]::GetFullPath($PowerShellPath))
        $wrapper = [regex]::Escape([IO.Path]::GetFullPath($WrapperPath))
        # Match every token of the sole canonical native -File invocation.
        # Extra/duplicate flags, commands, scripts and path prefixes cannot match.
        $pattern = '\A(?:"' + $exe + '"|' + $exe + ')[ \t]+-NoProfile[ \t]+-File[ \t]+(?:"' + $wrapper + '"|' + $wrapper + ')[ \t]+-Profile[ \t]+data-20261026[ \t]+-Action[ \t]+' + $RequestedAction + '(?:[ \t]+-NoBrowser)?[ \t]*\z'
        return [regex]::IsMatch([string]$ParentProcess.CommandLine, $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)
    } catch { return $false }
}

function Assert-DataLauncherParent([string]$RequestedAction) {
    try {
        $childIdentity = CimCmdlets\Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $PID" -ErrorAction Stop
        $parentIdentity = CimCmdlets\Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($childIdentity.ParentProcessId)" -ErrorAction Stop
        $wrapperPath = Join-Path $PSScriptRoot 'start-platform.ps1'
        $shellPath = Join-Path $PSHOME 'pwsh.exe'
        if (!(Test-DataLauncherParentIdentity $parentIdentity $childIdentity $RequestedAction $wrapperPath $shellPath)) { Fail 'Data mutation requires the live canonical start-platform -File parent; a caller switch is insufficient' }
        $parentAgain = CimCmdlets\Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($parentIdentity.ProcessId)" -ErrorAction Stop
        if ($null -eq $parentAgain -or $parentAgain.CreationDate -ne $parentIdentity.CreationDate -or
            $parentAgain.ExecutablePath -cne $parentIdentity.ExecutablePath -or $parentAgain.CommandLine -cne $parentIdentity.CommandLine) { Fail 'Data launcher parent identity changed; no mutation was performed' }
    } catch { Fail 'Data launcher parent identity is unavailable or invalid; no mutation was performed' }
}

function Assert-SelectedConfigPath {
    $expectedConfig = if ($AcceptanceMode) { $acceptanceConfig } else { $formalConfig }
    if ([IO.Path]::GetFullPath($ConfigFile) -ne $expectedConfig) {
        Fail 'Only the fixed protected runtime.json for the selected mode is accepted'
    }
    if ([IO.Path]::GetExtension($ConfigFile) -ne '.json') {
        Fail 'Runtime configuration must be the protected JSON file'
    }
    if (!(Test-Path -LiteralPath $ConfigFile -PathType Leaf)) { Fail 'Protected runtime configuration file is missing' }
    Assert-DpapiNoReparse $localRuntimeBase 'Protected runtime parent' | Out-Null
    Assert-DpapiNoReparse $runtimeRoot 'Protected runtime directory' | Out-Null
    Assert-DpapiNoReparse $ConfigFile 'Protected runtime configuration' | Out-Null
    Assert-DpapiProtectedAcl $runtimeRoot $true 'Protected runtime directory'
    Assert-DpapiProtectedAcl $ConfigFile $false 'Protected runtime configuration'
}

function Read-Config {
    Assert-SelectedConfigPath
    try { $config = Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Fail 'Protected runtime configuration must be valid UTF-8 JSON' }
    if ($config -isnot [pscustomobject]) { Fail 'Runtime configuration must be a JSON object' }
    $controlledSecretReferences = @('LLM_SECRET_SOURCE_PATH', 'CHROM_BATCH_CURSOR_KEY_SOURCE_PATH')
    foreach ($property in $config.PSObject.Properties) {
        if ($property.Name -cnotmatch '^[A-Z][A-Z0-9_]*$' -or $property.Value -isnot [string]) {
            Fail 'Runtime configuration requires uppercase keys and string values'
        }
        if ($property.Name -in @('HOME', 'USERPROFILE', 'CODEX_HOME', 'PATH', 'PSMODULEPATH', 'COMSPEC')) {
            Fail 'Runtime configuration contains a reserved environment key'
        }
        if ($property.Name -ceq 'CHROM_BATCH_CURSOR_SIGNING_KEY') {
            Fail 'Runtime configuration must contain a protected cursor-key reference, never the key value'
        }
        if ($property.Name -ceq 'CHROM_BATCH_CURSOR_KEY_SOURCE_PATH' -and $property.Name -notin $controlledSecretReferences) {
            Fail 'Cursor-key reference is not an approved protected secret reference'
        }
        if ($property.Name -match '(?i)(^|_)(API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)(_|$)' -and $property.Name -notin @('LLM_SECRET_SOURCE_PATH', 'CHROM_ASSISTANT_SECRET_ROOT')) {
            Fail 'Runtime configuration must not contain raw secret keys'
        }
    }
    return $config
}

function Set-ProcessEnvironment([pscustomobject]$Config) {
    foreach ($property in $Config.PSObject.Properties) {
        if ($property.Name -ceq 'CHROM_BATCH_CURSOR_KEY_SOURCE_PATH') { continue }
        [Environment]::SetEnvironmentVariable($property.Name, $property.Value, 'Process')
    }
    # Bind the secret-store anchor to the validated config location, including
    # Windows Store filesystem virtualization, rather than to secret input.
    [Environment]::SetEnvironmentVariable('CHROM_ASSISTANT_RUNTIME_ROOT', $runtimeRoot, 'Process')
    [Environment]::SetEnvironmentVariable('CHROM_BATCH_CURSOR_SIGNING_KEY', $null, 'Process')
    if ($AcceptanceMode) {
        [Environment]::SetEnvironmentVariable('DATABASE_URL', $script:AcceptanceDatabaseUrl, 'Process')
        [Environment]::SetEnvironmentVariable('SYNC_DATABASE_URL', $script:AcceptanceSyncDatabaseUrl, 'Process')
    }
    $pythonPath = if ($DataAcceptanceMode) { $dataBackendPackageRoot } else { Join-Path $repo 'backend\src' }
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $pythonPath, 'Process')
    Set-PythonBytecodePolicy $DataAcceptanceMode
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
}

function Set-PythonBytecodePolicy([bool]$DataOnly) {
    if ($DataOnly) { [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', '1', 'Process') }
}

function Get-ConfigValue([pscustomobject]$Config, [string[]]$Names) {
    foreach ($name in $Names) {
        $property = $Config.PSObject.Properties[$name]
        if ($null -ne $property -and $property.Value.Trim()) { return $property.Value.Trim() }
    }
    return $null
}

function Get-Port([pscustomobject]$Config, [string]$Key) {
    $value = Get-ConfigValue $Config @($Key)
    $number = 0
    if (![int]::TryParse($value, [ref]$number) -or $number -lt 1 -or $number -gt 65535) {
        Fail "Invalid or missing port: $Key"
    }
    return $number
}

function Assert-UnderRoot([string]$Path, [string]$Root, [string]$Name) {
    $full = [IO.Path]::GetFullPath($Path)
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    if (!$full.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        Fail "$Name must remain inside the protected runtime directory"
    }
    return $full
}

function Get-Sha256([string]$Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { Fail "Required file is missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-Hash([string]$Path, [string]$Expected, [string]$Name) {
    if ($Expected -notmatch '^[0-9a-fA-F]{64}$') { Fail "$Name expected SHA-256 is invalid" }
    $actual = Get-Sha256 $Path
    if ($actual -ne $Expected.ToLowerInvariant()) { Fail "$Name SHA-256 mismatch" }
}

function Assert-File([string]$Path, [string]$Name) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { Fail "$Name file is missing" }
    return [IO.Path]::GetFullPath($Path)
}

function Assert-Directory([string]$Path, [string]$Name) {
    if (!(Test-Path -LiteralPath $Path -PathType Container)) { Fail "$Name must be a directory" }
    return [IO.Path]::GetFullPath($Path)
}

function Assert-DeploymentBindings([pscustomobject]$Config) {
    $required = @(
        'API_PORT', 'FRONTEND_PORT', 'CHROM_BUILD_MANIFEST', 'REDIS_URL',
        'ARTIFACT_ROOT', 'CHROM_ACTOR_ID', 'CHROM_DEFAULT_PROJECT_ID', 'CHROM_PROJECT_IDS', 'WORKER_READY_FILE'
    )
    if (!$DataAcceptanceMode) {
        $required += @(
            'CHROM_TLC_REGISTRY_PATH', 'CHROM_TLC_MODEL_ROOT', 'CHROM_TLC_ARTIFACT_MANIFEST_SHA256', 'CHROM_TLC_MODEL_ID',
            'CHROM_TLC_MODEL_VERSION', 'CHROM_TLC_BUSINESS_SCOPE_ID', 'CHROM_TLC_GOLDEN_FIXTURE',
            'CHROM_SFC_REGISTRY_PATH', 'CHROM_SFC_MODEL_ROOT', 'CHROM_SFC_REGISTRY_SHA256', 'CHROM_SFC_CANDIDATE_DB_PATH',
            'CHROM_SFC_MODEL_ID', 'CHROM_SFC_MODEL_VERSION', 'CHROM_SFC_ARTIFACT_SHA256', 'CHROM_SFC_BUSINESS_SCOPE_ID',
            'CHROM_SFC_GOLDEN_FIXTURE', 'CHROM_SFC_SCOPE_STORAGE_PATH'
        )
    }
    if ($AcceptanceMode) { $required += 'CHROM_ACCEPTANCE_DATABASE_SCHEMA' } else { $required += @('DATABASE_URL', 'SYNC_DATABASE_URL') }
    foreach ($key in $required) {
        if (!(Get-ConfigValue $Config @($key))) { Fail "Missing required runtime key: $key" }
    }
    $null = Get-Port $Config 'API_PORT'
    $null = Get-Port $Config 'FRONTEND_PORT'
    $buildManifest = Assert-File (Get-ConfigValue $Config @('CHROM_BUILD_MANIFEST')) 'Build manifest'
    if ($DataAcceptanceMode) {
        Assert-DataAcceptanceBuild $buildManifest
        Assert-DataAcceptanceBindings $Config
        Test-DataRuntimePackage $dataBackendPackageRoot (Get-ConfigValue $Config @('CHROM_BACKEND_SOURCE_MANIFEST_SHA256')) | Out-Null
        return $true
    }
    $script:BatchCursorRequired = Test-BatchBuild $buildManifest
    $cursorKeyPath = Get-ConfigValue $Config @('CHROM_BATCH_CURSOR_KEY_SOURCE_PATH')
    if ($script:BatchCursorRequired -and !$cursorKeyPath) { Fail 'Batch build requires a protected cursor-key reference' }
    if ($cursorKeyPath) {
        $null = Assert-DpapiSecretFile $cursorKeyPath $runtimeRoot 'Protected batch cursor signing key'
        $null = Get-BatchCursorSecret $Config
    }
    $databaseUrl = if ($AcceptanceMode) { $script:AcceptanceDatabaseUrl } else { Get-ConfigValue $Config @('DATABASE_URL') }
    $syncDatabaseUrl = if ($AcceptanceMode) { $script:AcceptanceSyncDatabaseUrl } else { Get-ConfigValue $Config @('SYNC_DATABASE_URL') }
    if (!$databaseUrl -or !$databaseUrl.StartsWith('mysql+asyncmy://')) { Fail 'DATABASE_URL must use asyncmy MySQL' }
    if (!$syncDatabaseUrl -or !$syncDatabaseUrl.StartsWith('mysql+pymysql://')) { Fail 'SYNC_DATABASE_URL must use pymysql MySQL' }
    if (!(Get-ConfigValue $Config @('REDIS_URL')).StartsWith('redis://')) { Fail 'REDIS_URL must use redis:// without a secret query' }
    $null = Assert-UnderRoot (Get-ConfigValue $Config @('WORKER_READY_FILE')) $runtimeRoot 'WORKER_READY_FILE'
    if (!$DataAcceptanceMode) {
        $scope = Assert-UnderRoot (Get-ConfigValue $Config @('CHROM_SFC_SCOPE_STORAGE_PATH')) $runtimeRoot 'CHROM_SFC_SCOPE_STORAGE_PATH'
        $scopeParent = [IO.Path]::GetDirectoryName($scope)
        if (!(Test-Path -LiteralPath $scopeParent -PathType Container)) { New-Item -ItemType Directory -Path $scopeParent -Force | Out-Null }
    }

    # TLC is registered as a directory and every audited asset is checked before launch.
    $tlcRegistry = Assert-Directory (Get-ConfigValue $Config @('CHROM_TLC_REGISTRY_PATH')) 'TLC registry'
    $tlcRoot = Assert-Directory (Get-ConfigValue $Config @('CHROM_TLC_MODEL_ROOT')) 'TLC model root'
    $tlcManifest = Join-Path $tlcRegistry 'MODEL_SHA256.txt'
    Assert-Hash $tlcManifest (Get-ConfigValue $Config @('CHROM_TLC_ARTIFACT_MANIFEST_SHA256')) 'TLC artifact manifest'
    if ((Get-ConfigValue $Config @('CHROM_TLC_MODEL_ID')) -ne 'tlc-author-ensemble-lgb-xgb-rf-ann' -or (Get-ConfigValue $Config @('CHROM_TLC_MODEL_VERSION')) -ne 'v1.1-author-ensemble' -or (Get-ConfigValue $Config @('CHROM_TLC_BUSINESS_SCOPE_ID')) -ne 'tlc-default') { Fail 'TLC provider binding does not match the audited artifact' }
    Assert-Hash (Join-Path $tlcRoot 'predict.py') '978c690b4ddc3bc12087269af0b81b24c7103080a0e66678041757b2d7d6c3ff' 'TLC source'
    $tlcAssets = @{
        'model_LGB\model_ANN (3).pkl.gz' = '05119d440f0c224671beb4faec470700598214dbd3fdc56119214efa0da3d234'
        'model_LGB\model_LGB (3).pkl.gz' = '4174408259cbb1605a4b57c0cacc0026c265427f22224d887f9e17164c24b69d'
        'model_LGB\model_XGB (3).pkl.gz' = 'ca24624473fd617c462d1ad687a9b3c082aa2110796cc5c244b265078877d396'
        'model_LGB\model_RF (3).pkl.gz' = '10dbad5457e8961418f28b42dd5c4350e55dc367574d38dba421b16a481d7559'
    }
    foreach ($asset in $tlcAssets.Keys) { Assert-Hash (Join-Path $tlcRoot $asset) $tlcAssets[$asset] "TLC asset $asset" }
    Assert-Hash (Get-ConfigValue $Config @('CHROM_TLC_GOLDEN_FIXTURE')) 'b047ca229f61fbf57a660c49d6b91e4fd77cf608970034897375f0c27db37eb8' 'TLC golden fixture'

    if ($AcceptanceMode) {
        Assert-AcceptanceSfcBindings $Config
    } else {
        # Formal priority-local SFC remains bound to the audited RS15 model and inputs.
        $sfcRegistry = Assert-File (Get-ConfigValue $Config @('CHROM_SFC_REGISTRY_PATH')) 'SFC registry'
        $sfcRoot = Assert-Directory (Get-ConfigValue $Config @('CHROM_SFC_MODEL_ROOT')) 'SFC model root'
        $sfcRegistryFull = [IO.Path]::GetFullPath($sfcRegistry)
        $sfcRootFull = [IO.Path]::GetFullPath($sfcRoot).TrimEnd('\') + '\'
        if (!$sfcRegistryFull.StartsWith($sfcRootFull, [StringComparison]::OrdinalIgnoreCase)) { Fail 'SFC registry must be inside the model root' }
        Assert-Hash $sfcRegistry (Get-ConfigValue $Config @('CHROM_SFC_REGISTRY_SHA256')) 'SFC registry'
        $sfcModelId = Get-ConfigValue $Config @('CHROM_SFC_MODEL_ID')
        $sfcVersion = Get-ConfigValue $Config @('CHROM_SFC_MODEL_VERSION')
        Assert-SfcModelBinding $Config $false
        try { $registryJson = Get-Content -LiteralPath $sfcRegistry -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { Fail 'SFC registry JSON is invalid' }
        $entry = @($registryJson.models | Where-Object { $_.id -eq $sfcModelId })
        if ($entry.Count -ne 1 -or $entry[0].model_sha256.ToLowerInvariant() -ne (Get-ConfigValue $Config @('CHROM_SFC_ARTIFACT_SHA256')).ToLowerInvariant()) { Fail 'SFC model registry entry does not match the configured artifact' }
        Assert-Hash (Join-Path (Join-Path $sfcRoot $sfcModelId) 'model.joblib') (Get-ConfigValue $Config @('CHROM_SFC_ARTIFACT_SHA256')) 'SFC model'
        $candidate = Assert-File (Get-ConfigValue $Config @('CHROM_SFC_CANDIDATE_DB_PATH')) 'SFC candidate database'
        Assert-Hash $candidate '266a6d00df80166c3faf64be9dc21748d9a39a9f7106a24b8741fdd628eb9c1d' 'SFC candidate database'
        Assert-Hash (Get-ConfigValue $Config @('CHROM_SFC_GOLDEN_FIXTURE')) '335f027e3483dc36e3f7f1be4a8d00e00471880ce436b93b5b67c85dd5bd2905' 'SFC golden fixture'
    }

    if ($AcceptanceMode) {
        Assert-AssistantAcceptanceBindings $Config
    } else {
        # Legacy priority-local provider input is read only from protected JSON.
        $providerUrl = Get-ConfigValue $Config @('LLM_PROVIDER_URL')
        $providerModel = Get-ConfigValue $Config @('LLM_MODEL_ID')
        $providerKey = Get-ConfigValue $Config @('LLM_KEY_REFERENCE')
        $secretSource = Get-ConfigValue $Config @('LLM_SECRET_SOURCE_PATH')
        if (!$providerUrl -or !$providerModel -or !$providerKey -or !$secretSource) { Fail 'Provider binding is incomplete in the protected runtime JSON' }
        if ($providerKey -cne 'API_KEY') { Fail 'Provider key reference must be API_KEY' }
        $null = Assert-DpapiSecretFile $secretSource $runtimeRoot
        try { $uri = [Uri]$providerUrl } catch { Fail 'Provider URL is invalid' }
        if (!$uri.IsAbsoluteUri -or $uri.UserInfo -or $uri.Fragment -or $uri.Query -match '(?i)(api[_-]?key|token|secret|password)=') { Fail 'Provider URL must not contain credentials or secret query parameters' }
        if ($providerModel.Length -gt 128) { Fail 'Provider binding length is invalid' }
    }
    return $true
}

function Test-BatchBuild([string]$ManifestPath) {
    try { $build = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Fail 'Build manifest must be valid UTF-8 JSON' }
    $toolsProperty = if ($build -is [pscustomobject]) { $build.PSObject.Properties['prediction_tools'] } else { $null }
    if ($null -eq $toolsProperty -or $toolsProperty.Value -isnot [pscustomobject]) { Fail 'Build manifest prediction_tools is invalid' }
    foreach ($type in $toolsProperty.Value.PSObject.Properties) {
        if (@($type.Value) -ccontains 'batch') { return $true }
    }
    return $false
}

function Assert-DataAcceptanceBuild([string]$ManifestPath) {
    if ([IO.Path]::GetFullPath($ManifestPath) -cne $dataManifest) { Fail 'Data acceptance requires its fixed build manifest' }
    try { $manifest = Get-Content -LiteralPath $dataManifest -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Fail 'Data acceptance build manifest is invalid' }
    Test-DataAcceptanceManifest $manifest | Out-Null
    if (!(Test-Path -LiteralPath $dataFrontendRoot -PathType Container)) { Fail 'Data acceptance frontend assembly is missing; generate it through the approved profile assembler' }
    Assert-DpapiNoReparse $dataFrontendRoot 'Data acceptance frontend assembly' | Out-Null
    foreach ($relative in @('ASSEMBLED_PROFILE.json', 'src\app\generated\build-manifest.json', 'src\app\generated\registry.ts', 'src\app\generated\profile.ts', 'src\modules\datasets\module.ts')) {
        if (!(Test-Path -LiteralPath (Join-Path $dataFrontendRoot $relative) -PathType Leaf)) { Fail 'Data acceptance frontend assembly is incomplete' }
    }
    foreach ($relative in @('src\modules\prediction', 'src\modules\assistant')) {
        if (Test-Path -LiteralPath (Join-Path $dataFrontendRoot $relative)) { Fail 'Data acceptance frontend contains an unselected module' }
    }
    try {
        $assembled = Get-Content -LiteralPath (Join-Path $dataFrontendRoot 'ASSEMBLED_PROFILE.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $generated = Get-Content -LiteralPath (Join-Path $dataFrontendRoot 'src\app\generated\build-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $registry = Get-Content -LiteralPath (Join-Path $dataFrontendRoot 'src\app\generated\registry.ts') -Raw -Encoding UTF8
        $profile = Get-Content -LiteralPath (Join-Path $dataFrontendRoot 'src\app\generated\profile.ts') -Raw -Encoding UTF8
    } catch { Fail 'Data acceptance frontend assembly metadata is invalid' }
    $moduleDirectories = @(Get-ChildItem -LiteralPath (Join-Path $dataFrontendRoot 'src\modules') -Directory | ForEach-Object { $_.Name })
    $manifestText = ConvertTo-Json -InputObject $manifest -Depth 10 -Compress
    $generatedText = ConvertTo-Json -InputObject $generated -Depth 10 -Compress
    if (($moduleDirectories -join ',') -cne 'datasets' -or $manifestText -cne $generatedText -or
        $assembled.build_id -cne $manifest.build_id -or $assembled.package_kind -cne 'static-assembly' -or
        @($assembled.modules).Count -ne 1 -or $assembled.modules[0] -cne 'datasets' -or
        @($assembled.chromatography_types).Count -ne 0 -or
        @($generated.modules).Count -ne 1 -or $generated.modules[0] -cne 'datasets' -or
        @($generated.chromatography_types).Count -ne 0 -or
        !$registry.Contains("@/modules/datasets/module") -or $registry -match '@/modules/(prediction|assistant)/' -or
        $profile -notmatch 'modules:\s*\["datasets"\]' -or $profile -match 'assistantModes:\s*\["[^]]+') {
        Fail 'Data acceptance frontend output does not match the dataset-only manifest'
    }
}

function Test-DataRuntimePackage([string]$PackageRoot, [string]$ExpectedSourceManifestSha256) {
    if ($ExpectedSourceManifestSha256 -cnotmatch '^[0-9a-f]{64}$') { Fail 'Data acceptance requires its protected reviewed source manifest pin' }
    if (!(Test-Path -LiteralPath $PackageRoot -PathType Container)) { Fail 'Data acceptance backend package is missing; assemble it from the approved frozen source before launch' }
    Assert-DpapiNoReparse $PackageRoot 'Data acceptance backend package' | Out-Null
    $buildPath = Join-Path $PackageRoot 'build-manifest.json'
    $sourcePath = Join-Path $PackageRoot 'SOURCE_MANIFEST.json'
    if (!(Test-Path -LiteralPath $sourcePath -PathType Leaf) -or (Get-Sha256 $sourcePath) -cne $ExpectedSourceManifestSha256) { Fail 'Data acceptance source manifest differs from its protected reviewed pin' }
    if (!(Test-Path -LiteralPath $buildPath -PathType Leaf) -or !(Test-Path -LiteralPath $sourcePath -PathType Leaf) -or
        !(Test-Path -LiteralPath (Join-Path $PackageRoot 'chrom_platform\worker_cli.py') -PathType Leaf) -or
        !(Test-Path -LiteralPath (Join-Path $PackageRoot 'chrom_platform\modules\data_center\import_worker.py') -PathType Leaf)) {
        Fail 'Data acceptance backend package identity files are incomplete'
    }
    try {
        $build = Get-Content -LiteralPath $buildPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $source = Get-Content -LiteralPath $sourcePath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch { Fail 'Data acceptance backend source manifest is invalid' }
    if ($build.manifest_version -cne '1.0' -or $build.build_id -cne 'data-center-invalid-window-20261026' -or
        @($build.modules).Count -ne 1 -or $build.modules[0] -cne 'datasets' -or
        @($build.chromatography_types).Count -ne 0 -or @($build.utilities).Count -ne 0 -or
        @($build.export_formats).Count -ne 0 -or $null -eq $build.prediction_tools -or
        @($build.prediction_tools.PSObject.Properties).Count -ne 0 -or $build.auth_provider -cne 'deployment_default' -or
        ($build.PSObject.Properties['assistant_features'] -and $null -ne $build.assistant_features) -or
        $source.build_id -cne 'data-center-invalid-window-20261026' -or
        @($source.modules).Count -ne 1 -or $source.modules[0] -cne 'datasets' -or
        @($source.chromatography_types).Count -ne 0 -or $source.worker_entrypoint -cne 'chrom_platform/worker_cli.py') {
        Fail 'Data acceptance backend source identity does not match its fixed dataset-only build'
    }
    $rows = @($source.files)
    if (!$rows.Count) { Fail 'Data acceptance backend source manifest has no file inventory' }
    $expectedFiles = [Collections.Generic.List[string]]::new()
    foreach ($row in $rows) {
        if (!$row.path -or [IO.Path]::IsPathRooted([string]$row.path) -or ([string]$row.path -split '[\\/]' -contains '..') -or
            $row.sha256 -cnotmatch '^[0-9a-f]{64}$') { Fail 'Data acceptance backend source file identity is invalid' }
        $relative = ([string]$row.path).Replace('/', '\')
        $path = [IO.Path]::GetFullPath((Join-Path $PackageRoot $relative))
        $prefix = [IO.Path]::GetFullPath($PackageRoot).TrimEnd('\') + '\'
        if (!$path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or !(Test-Path -LiteralPath $path -PathType Leaf)) { Fail 'Data acceptance backend source file escaped its fixed package' }
        if ((Get-Sha256 $path) -cne ([string]$row.sha256).ToLowerInvariant()) { Fail 'Data acceptance backend source file digest mismatch' }
        $expectedFiles.Add(([string]$row.path).Replace('\', '/'))
    }
    if (($expectedFiles | Sort-Object -Unique).Count -ne $expectedFiles.Count -or -not ($expectedFiles -ccontains 'build-manifest.json')) {
        Fail 'Data acceptance backend source manifest has duplicate files or omits its build manifest'
    }
    $actualFiles = [Collections.Generic.List[string]]::new()
    foreach ($entry in Get-ChildItem -LiteralPath $PackageRoot -Force -Recurse) {
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Fail 'Data acceptance backend package contains a reparse point' }
        if ($entry.PSIsContainer) { continue }
        $relative = $entry.FullName.Substring([IO.Path]::GetFullPath($PackageRoot).TrimEnd('\').Length + 1).Replace('\', '/')
        if ($relative -cne 'SOURCE_MANIFEST.json') { $actualFiles.Add($relative) }
        if ($entry.Name -match '(?i)\.(pt|pth|joblib|pkl|pkl\.gz|onnx|safetensors|h5|keras)$') { Fail 'Data acceptance backend package contains model weights' }
    }
    $actualInventory = (@($actualFiles | Sort-Object -Unique) -join "`n")
    $expectedInventory = (@($expectedFiles | Sort-Object -Unique) -join "`n")
    if ($actualInventory -cne $expectedInventory) { Fail 'Data acceptance backend package files do not match its source inventory' }
    $moduleRoot = Join-Path $PackageRoot 'chrom_platform\modules'
    $moduleDirs = @(Get-ChildItem -LiteralPath $moduleRoot -Directory | ForEach-Object { $_.Name })
    if (($moduleDirs -join ',') -cne 'data_center') { Fail 'Data acceptance backend package contains an optional non-dataset module' }
    $adapterRoot = Join-Path $PackageRoot 'chrom_platform\adapters'
    if (Test-Path -LiteralPath $adapterRoot) { Fail 'Data acceptance backend package contains science adapters' }
    return $true
}

function Test-DataAcceptanceManifest([pscustomobject]$Manifest) {
    if ($Manifest.manifest_version -cne '1.0' -or $Manifest.build_id -cne 'data-center-invalid-window-20261026' -or
        @($Manifest.modules).Count -ne 1 -or $Manifest.modules[0] -cne 'datasets' -or
        @($Manifest.chromatography_types).Count -ne 0 -or @($Manifest.utilities).Count -ne 0 -or
        @($Manifest.export_formats).Count -ne 0 -or $Manifest.auth_provider -cne 'deployment_default' -or
        $Manifest.PSObject.Properties['assistant_features'] -or $null -eq $Manifest.prediction_tools -or
        @($Manifest.prediction_tools.PSObject.Properties).Count -ne 0) {
        Fail 'Data acceptance manifest must select only datasets with no science or assistant capability'
    }
    return $true
}

function Assert-DataAcceptanceBindings([pscustomobject]$Config) {
    if ((Get-Port $Config 'API_PORT') -ne 9012 -or (Get-Port $Config 'FRONTEND_PORT') -ne 5174) { Fail 'Data acceptance ports do not match the fixed isolated ports' }
    if ((Get-ConfigValue $Config @('WORKER_QUEUE')) -cne 'chrom-platform-data-accept-20261026') { Fail 'Data acceptance worker namespace is invalid' }
    if ((Get-ConfigValue $Config @('CHROM_ACCEPTANCE_DATABASE_SCHEMA')) -cne 'chrom_data_accept_20261026') { Fail 'Data acceptance schema binding is invalid' }
    if ((Get-ConfigValue $Config @('CHROM_DEFAULT_PROJECT_ID')) -cne 'project_data_accept_20261026' -or
        (Get-ConfigValue $Config @('CHROM_PROJECT_IDS')) -cne 'project_data_accept_20261026') { Fail 'Data acceptance actor scope must contain only its fixed project' }
    foreach ($property in $Config.PSObject.Properties) {
        if ($property.Name -cmatch '^(LLM_|CHROM_ASSISTANT_|CHROM_TLC_|CHROM_SFC_|CHROM_BATCH_CURSOR)') {
            Fail 'Data acceptance runtime must not contain assistant, science, or model secret bindings'
        }
    }
    $expectedArtifacts = [IO.Path]::GetFullPath((Join-Path $runtimeRoot 'artifacts'))
    if ([IO.Path]::GetFullPath((Get-ConfigValue $Config @('ARTIFACT_ROOT'))) -cne $expectedArtifacts) { Fail 'Data acceptance artifact root must remain isolated' }
    $null = Assert-UnderRoot (Get-ConfigValue $Config @('WORKER_READY_FILE')) $stateRoot 'Data acceptance worker ready file'
    foreach ($key in @('DATABASE_URL', 'SYNC_DATABASE_URL')) {
        $value = Get-ConfigValue $Config @($key)
        if (!(Test-DataAcceptanceDatabaseUrl $key $value)) { Fail 'Data acceptance database URL must directly target its fixed local schema and runtime principal' }
    }
    try { $redis = [Uri](Get-ConfigValue $Config @('REDIS_URL')) } catch { Fail 'Data acceptance Redis URL is invalid' }
    if ($redis.Scheme -cne 'redis' -or $redis.Host -cne '127.0.0.1' -or $redis.Port -ne 6379 -or $redis.UserInfo -or $redis.Query) { Fail 'Data acceptance Redis endpoint must use the fixed local binding without embedded credentials' }
    foreach ($path in @($runtimeRoot, $stateRoot, $pidRoot, $logRoot, $expectedArtifacts)) {
        if (!(Test-Path -LiteralPath $path -PathType Container)) { Fail 'Data acceptance protected directory is missing' }
        Assert-DpapiNoReparse $path 'Data acceptance protected directory' | Out-Null
        Assert-DpapiProtectedAcl $path $true 'Data acceptance protected directory'
    }
    foreach ($name in @('.env', '.env.local', '.env.development', '.env.development.local')) {
        if (Test-Path -LiteralPath (Join-Path $dataFrontendRoot $name) -PathType Leaf) { Fail 'Data acceptance frontend assembly must not contain an independent dotenv file' }
    }
}

function Test-DataAcceptanceDatabaseUrl([string]$Key, [string]$Value) {
    $driver = switch -CaseSensitive ($Key) {
        'DATABASE_URL' { 'asyncmy' }
        'SYNC_DATABASE_URL' { 'pymysql' }
        default { return $false }
    }
    $pattern = '^(mysql\+' + $driver + '://chrom_data_accept_runtime:[^\s/@?#]+@127\.0\.0\.1:3306/)chrom_data_accept_20261026(?:\?.*)?$'
    return ($Value -cmatch $pattern)
}

function Assert-SfcModelBinding([pscustomobject]$Config, [bool]$IsAcceptance) {
    $modelId = Get-ConfigValue $Config @('CHROM_SFC_MODEL_ID')
    $modelVersion = Get-ConfigValue $Config @('CHROM_SFC_MODEL_VERSION')
    $businessScope = Get-ConfigValue $Config @('CHROM_SFC_BUSINESS_SCOPE_ID')
    if ($IsAcceptance) {
        if ($businessScope -cne 'priority-local' -or $modelId -cne 'sc-rs12-amylose-dmpc-coated' -or $modelVersion -cne '1') {
            Fail 'Acceptance SFC model binding is not the fixed RS12 profile'
        }
    } elseif ($modelId -ne 'rs15-extra-trees-s80-seed2024' -or $modelVersion -ne '1' -or $businessScope -ne 'priority-local') {
        Fail 'SFC provider binding does not match the audited priority-local artifact'
    }
}

function Assert-AcceptanceSfcBindings([pscustomobject]$Config) {
    Assert-SfcModelBinding $Config $true

    $expectedModelRoot = [IO.Path]::GetFullPath('D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\models')
    $expectedRegistry = [IO.Path]::GetFullPath((Join-Path $expectedModelRoot 'registry.json'))
    $expectedCatalog = [IO.Path]::GetFullPath((Join-Path $repo 'Services\sfc_assets\conditions.sqlite'))
    $expectedFixture = [IO.Path]::GetFullPath((Join-Path $repo 'testing\fixtures\priority-launch\sfc-single-reference-2p5-warning-v3.json'))
    $expectedFixtureSha256 = '2d7236cff2bb095fec358902ee71aef0a95121b170d57aaf940dae535d122538'

    try {
        $configuredModelRoot = [IO.Path]::GetFullPath((Get-ConfigValue $Config @('CHROM_SFC_MODEL_ROOT')))
        $configuredRegistry = [IO.Path]::GetFullPath((Get-ConfigValue $Config @('CHROM_SFC_REGISTRY_PATH')))
        $configuredCatalog = [IO.Path]::GetFullPath((Get-ConfigValue $Config @('CHROM_SFC_CANDIDATE_DB_PATH')))
        $configuredFixture = [IO.Path]::GetFullPath((Get-ConfigValue $Config @('CHROM_SFC_GOLDEN_FIXTURE')))
    } catch { Fail 'Acceptance SFC path binding is invalid' }
    if ($configuredModelRoot -cne $expectedModelRoot -or $configuredRegistry -cne $expectedRegistry -or $configuredCatalog -cne $expectedCatalog -or $configuredFixture -cne $expectedFixture) {
        Fail 'Acceptance SFC paths do not match the fixed RS12 registry, catalog, and fixture'
    }

    Assert-Hash $expectedFixture $expectedFixtureSha256 'Acceptance SFC fixture'
    try { $fixture = Get-Content -LiteralPath $expectedFixture -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Fail 'Acceptance SFC fixture is invalid' }
    if ($fixture.fixture_version -cne 'sfc-single-column-reference-v3' -or
        $fixture.model.id -cne 'sc-rs12-amylose-dmpc-coated' -or
        [string]$fixture.model.version -cne '1' -or
        $fixture.model.scope -cne 'single_column' -or
        $fixture.model.target -cne 'label_success_rs12' -or
        [double]$fixture.model.threshold -ne 1.2 -or
        $fixture.model.registry_sha256 -cne '42f04a4ceb63024e685d0d8a4a8bd1ca57b12216f4f7f1050216f7ec04228429' -or
        $fixture.model.artifact_sha256 -cne (Get-ConfigValue $Config @('CHROM_SFC_ARTIFACT_SHA256')) -or
        (Get-ConfigValue $Config @('CHROM_SFC_REGISTRY_SHA256')) -cne $fixture.model.registry_sha256 -or
        $fixture.candidate_source.runtime_catalog_sha256 -cne '4a76421324aca6865d5dbd7df1e57d5df5362d231d7e3ee3dd34cf4db48c9ab1' -or
        $fixture.input.conditions.flow_rate_ml_min -ne 2.5) {
        Fail 'Acceptance SFC fixture does not match the fixed RS12 v3 binding'
    }

    Assert-Hash $expectedRegistry $fixture.model.registry_sha256 'Acceptance SFC registry'
    try { $registryJson = Get-Content -LiteralPath $expectedRegistry -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Fail 'Acceptance SFC registry is invalid' }
    $entries = @($registryJson.models | Where-Object { $_.id -ceq $fixture.model.id })
    if ($entries.Count -ne 1 -or
        $entries[0].scope -cne $fixture.model.scope -or
        $entries[0].target -cne $fixture.model.target -or
        [double]$entries[0].threshold -ne [double]$fixture.model.threshold -or
        $entries[0].model_sha256 -cne $fixture.model.artifact_sha256) {
        Fail 'Acceptance SFC registry entry does not match the fixed RS12 binding'
    }

    $artifactPath = Join-Path (Join-Path $expectedModelRoot $fixture.model.id) 'model.joblib'
    $metadataPath = Join-Path (Join-Path $expectedModelRoot $fixture.model.id) 'metadata.json'
    Assert-Hash $artifactPath $fixture.model.artifact_sha256 'Acceptance SFC artifact'
    Assert-Hash $metadataPath $fixture.model.metadata_sha256 'Acceptance SFC metadata'
    Assert-Hash $expectedCatalog $fixture.candidate_source.runtime_catalog_sha256 'Acceptance SFC platform catalog'
    Assert-AcceptanceSfcFullBinding $Config $repo $script:AcceptanceSfcFullBindingPolicy
}

function Assert-AcceptanceSfcFullBinding(
    [pscustomobject]$Config,
    [string]$RepoRoot,
    [pscustomobject]$TrustedBinding
) {
    $keys = @(
        'CHROM_SFC_FULL_MODEL_ID', 'CHROM_SFC_FULL_MODEL_VERSION', 'CHROM_SFC_FULL_ARTIFACT_SHA256',
        'CHROM_SFC_FULL_REFERENCE_PATH', 'CHROM_SFC_FULL_REFERENCE_SHA256'
    )
    $values = @{}
    foreach ($key in $keys) { $values[$key] = Get-ConfigValue $Config @($key) }
    $present = @($keys | Where-Object { $values[$_] })
    if ($present.Count -eq 0) { return }
    if ($present.Count -ne $keys.Count) { Fail 'Acceptance SFC full binding must provide all five fixed fields' }

    if ($values['CHROM_SFC_FULL_MODEL_ID'] -cne $TrustedBinding.model_id -or
        $values['CHROM_SFC_FULL_MODEL_VERSION'] -cne $TrustedBinding.model_version -or
        $values['CHROM_SFC_FULL_ARTIFACT_SHA256'] -cne $TrustedBinding.artifact_sha256) {
        Fail 'Acceptance SFC full model binding does not match the fixed RS12 exact model'
    }
    $trustedReferenceSha256 = [string]$TrustedBinding.reference_sha256
    if ($trustedReferenceSha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        $values['CHROM_SFC_FULL_REFERENCE_SHA256'] -cne $trustedReferenceSha256) {
        Fail 'Acceptance SFC full reference has no matching reviewed runtime pin'
    }

    $expectedFixture = [IO.Path]::GetFullPath((Join-Path $RepoRoot $TrustedBinding.fixture_relative_path))
    try {
        if (![IO.Path]::IsPathFullyQualified($values['CHROM_SFC_FULL_REFERENCE_PATH'])) { Fail 'Acceptance SFC full reference path must be absolute' }
        $configuredFixture = [IO.Path]::GetFullPath($values['CHROM_SFC_FULL_REFERENCE_PATH'])
        $configuredRoot = [IO.Path]::GetFullPath((Get-ConfigValue $Config @('CHROM_SFC_MODEL_ROOT')))
        $configuredRegistry = [IO.Path]::GetFullPath((Get-ConfigValue $Config @('CHROM_SFC_REGISTRY_PATH')))
        $configuredCatalog = [IO.Path]::GetFullPath((Get-ConfigValue $Config @('CHROM_SFC_CANDIDATE_DB_PATH')))
        $trustedAuthorCatalog = [IO.Path]::GetFullPath($TrustedBinding.author_catalog_path)
    } catch { Fail 'Acceptance SFC full path binding is invalid' }
    $expectedRegistry = [IO.Path]::GetFullPath((Join-Path $configuredRoot 'registry.json'))
    $expectedArtifact = [IO.Path]::GetFullPath((Join-Path (Join-Path $configuredRoot $TrustedBinding.model_id) 'model.joblib'))
    $expectedMetadata = [IO.Path]::GetFullPath((Join-Path (Join-Path $configuredRoot $TrustedBinding.model_id) 'metadata.json'))
    $expectedCatalog = [IO.Path]::GetFullPath((Join-Path $RepoRoot 'Services\sfc_assets\conditions.sqlite'))
    if ($configuredRoot -cne [IO.Path]::GetFullPath($TrustedBinding.model_root) -or
        $configuredRegistry -cne $expectedRegistry -or $configuredRegistry -cne [IO.Path]::GetFullPath((Join-Path $TrustedBinding.model_root 'registry.json')) -or
        $configuredCatalog -cne $expectedCatalog -or $configuredFixture -cne $expectedFixture) {
        Fail 'Acceptance SFC full paths do not match the fixed RS12 registry, catalog, and reference'
    }
    if (!(Test-Path -LiteralPath $trustedAuthorCatalog -PathType Leaf)) { Fail 'Acceptance SFC full author catalog is missing' }

    Assert-Hash $configuredRegistry $TrustedBinding.registry_sha256 'Acceptance SFC full registry'
    Assert-Hash $expectedArtifact $TrustedBinding.artifact_sha256 'Acceptance SFC full artifact'
    Assert-Hash $expectedMetadata $TrustedBinding.metadata_sha256 'Acceptance SFC full metadata'
    Assert-Hash $expectedCatalog $TrustedBinding.platform_catalog_sha256 'Acceptance SFC full platform catalog'
    Assert-Hash $trustedAuthorCatalog $TrustedBinding.author_catalog_sha256 'Acceptance SFC full author catalog'
    Assert-Hash $configuredFixture $trustedReferenceSha256 'Acceptance SFC full author reference'
    try {
        $fixture = Get-Content -LiteralPath $configuredFixture -Raw -Encoding UTF8 | ConvertFrom-Json
        $registryJson = Get-Content -LiteralPath $configuredRegistry -Raw -Encoding UTF8 | ConvertFrom-Json
        $metadata = Get-Content -LiteralPath $expectedMetadata -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch { Fail 'Acceptance SFC full reference or model metadata is invalid JSON' }

    $registryEntry = @($registryJson.models | Where-Object { $_.id -ceq $TrustedBinding.model_id })
    $candidateSource = $fixture.candidate_source
    $model = $fixture.model
    if ($fixture.fixture_status -cne 'AUTHOR_REFERENCE' -or
        $fixture.fixture_version -cne $TrustedBinding.fixture_version -or
        $fixture.reference_kind -cne 'author_v1.1_implementation_software_reference' -or
        $registryEntry.Count -ne 1 -or
        $model.id -cne $TrustedBinding.model_id -or [string]$model.version -cne $TrustedBinding.model_version -or
        $model.scope -cne $TrustedBinding.scope -or $model.target -cne $TrustedBinding.target -or
        [string]$model.threshold -cne $TrustedBinding.threshold -or
        $model.score_semantics -cne 'positive_class_probability' -or
        $model.artifact_sha256 -cne $TrustedBinding.artifact_sha256 -or
        $model.registry_sha256 -cne $TrustedBinding.registry_sha256 -or
        $model.metadata_sha256 -cne $TrustedBinding.metadata_sha256 -or
        $metadata.id -cne $TrustedBinding.model_id -or $metadata.scope -cne $TrustedBinding.scope -or
        $metadata.target -cne $TrustedBinding.target -or [string]$metadata.threshold -cne $TrustedBinding.threshold -or
        $metadata.model_sha256 -cne $TrustedBinding.artifact_sha256 -or
        $registryEntry[0].scope -cne $TrustedBinding.scope -or
        $registryEntry[0].model_sha256 -cne $TrustedBinding.artifact_sha256 -or
        $model.feature_count -ne 66 -or @($model.feature_names).Count -ne 66 -or
        (@($model.feature_names) -join "`n") -cne (@($registryEntry[0].feature_names) -join "`n") -or
        (@($model.feature_names) -join "`n") -cne (@($metadata.feature_names) -join "`n") -or
        $model.feature_names_sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        $model.input_schema_sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        $model.output_schema_sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        $model.preprocessing -isnot [pscustomobject] -or
        $candidateSource.platform_catalog_path -cne $expectedCatalog -or
        $candidateSource.platform_catalog_sha256 -cne $TrustedBinding.platform_catalog_sha256 -or
        [IO.Path]::GetFullPath($candidateSource.author_catalog_path) -cne $trustedAuthorCatalog -or
        $candidateSource.author_catalog_sha256 -cne $TrustedBinding.author_catalog_sha256) {
        Fail 'Acceptance SFC full AUTHOR_REFERENCE does not match the fixed RS12 assets or contract'
    }
    if ($registryEntry[0].feature_names -isnot [array] -or
        $registryEntry[0].version -and [string]$registryEntry[0].version -cne $TrustedBinding.model_version) {
        Fail 'Acceptance SFC full registry model version or feature order is invalid'
    }
    $featureJson = '["' + (@($model.feature_names) -join '","') + '"]'
    $featureHasher = [Security.Cryptography.SHA256]::Create()
    try { $featureDigest = [Convert]::ToHexString($featureHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($featureJson))).ToLowerInvariant() }
    finally { $featureHasher.Dispose() }
    if ($featureDigest -cne $model.feature_names_sha256) { Fail 'Acceptance SFC full feature-order hash is invalid' }

    $inputData = $fixture.input
    if ($inputData.schema_version -cne '1.1' -or $inputData.tool -cne 'full_column' -or $inputData.top_k -ne 2 -or
        !$inputData.candidate_scope_id -or $inputData.modifier_total_fraction -ne 0.2 -or
        $inputData.structure_smiles.a -cne 'C[C@H](O)F' -or $inputData.structure_smiles.b -cne 'C[C@@H](O)F' -or
        $inputData.structure_smiles.modifier -cne 'CO' -or @($inputData.modifier_components).Count -ne 1 -or
        $inputData.modifier_components[0].structure_ref -cne 'modifier' -or $inputData.modifier_components[0].fraction -ne 1.0) {
        Fail 'Acceptance SFC full input does not match the fixed two-condition author request'
    }
    $expectedConditions = @(
        @{ candidate_id = 'candidate-cellulose-2'; column = 'Cellulose 2'; phase = 'cellulose tris (3-chloro-4-methylphenylcarbamate)' },
        @{ candidate_id = 'candidate-oz'; column = 'OZ'; phase = 'cellulose tris (3-chloro-4-methylphenylcarbamate)' }
    )
    $inputRows = @($inputData.candidates)
    $referenceRows = @($fixture.author_reference.candidate_results)
    if ($inputRows.Count -ne 2 -or $referenceRows.Count -ne 2 -or
        @($inputRows.candidate_id | Select-Object -Unique).Count -ne 2 -or
        @($referenceRows.candidate_id | Select-Object -Unique).Count -ne 2 -or
        (@($referenceRows.author_rank | Sort-Object) -join ',') -cne '1,2') {
        Fail 'Acceptance SFC full reference must contain exactly two unique candidate rows'
    }
    foreach ($expected in $expectedConditions) {
        $inputRow = @($inputRows | Where-Object { $_.candidate_id -ceq $expected.candidate_id })
        $referenceRow = @($referenceRows | Where-Object { $_.candidate_id -ceq $expected.candidate_id })
        if ($inputRow.Count -ne 1 -or $referenceRow.Count -ne 1 -or
            $inputRow[0].column -cne $expected.column -or $inputRow[0].stationary_phase -cne $expected.phase -or
            $inputRow[0].mode -cne 'coated' -or [double]$inputRow[0].length_mm -ne 150.0 -or
            [double]$inputRow[0].inner_diameter_mm -ne 3.0 -or [double]$inputRow[0].particle_size_um -ne 3.0 -or
            [double]$inputRow[0].column_temperature -ne 25.0 -or [double]$inputRow[0].flow_rate -ne 1.5) {
            Fail 'Acceptance SFC full candidate tuple is not one of the approved Cellulose 2/OZ rows'
        }
        $conditions = $referenceRow[0].conditions
        if ($referenceRow[0].author_rank -notin @(1, 2) -or
            $conditions.column -cne $inputRow[0].column -or $conditions.stationary_phase -cne $inputRow[0].stationary_phase -or
            $conditions.mode -cne $inputRow[0].mode -or [double]$conditions.length_mm -ne [double]$inputRow[0].length_mm -or
            [double]$conditions.inner_diameter_mm -ne [double]$inputRow[0].inner_diameter_mm -or
            [double]$conditions.particle_size_um -ne [double]$inputRow[0].particle_size_um -or
            [double]$conditions.column_temperature_c -ne [double]$inputRow[0].column_temperature -or
            [double]$conditions.flow_rate_ml_min -ne [double]$inputRow[0].flow_rate) {
            Fail 'Acceptance SFC full author result conditions do not match the exact candidate tuple'
        }
        $probability = 0.0
        if (![double]::TryParse([string]$referenceRow[0].expected_probability, [ref]$probability) -or
            [double]::IsNaN($probability) -or [double]::IsInfinity($probability) -or $probability -lt 0.0 -or $probability -gt 1.0) {
            Fail 'Acceptance SFC full author score is invalid'
        }
    }
    $rankOneScore = [double](@($referenceRows | Where-Object { $_.author_rank -eq 1 })[0].expected_probability)
    $rankTwoScore = [double](@($referenceRows | Where-Object { $_.author_rank -eq 2 })[0].expected_probability)
    if ($rankOneScore -le $rankTwoScore) { Fail 'Acceptance SFC full author ranks do not match descending scores' }

    $sourcePins = @{}
    foreach ($sourcePin in @($fixture.source_pins)) {
        if (!$sourcePin.role -or $sourcePins.ContainsKey([string]$sourcePin.role) -or
            $sourcePin.sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
            ![IO.Path]::IsPathFullyQualified([string]$sourcePin.path)) {
            Fail 'Acceptance SFC full source pin is malformed or duplicated'
        }
        Assert-Hash ([string]$sourcePin.path) ([string]$sourcePin.sha256) "Acceptance SFC full source $($sourcePin.role)"
        $sourcePins[[string]$sourcePin.role] = ([string]$sourcePin.sha256).ToLowerInvariant()
    }
    foreach ($pin in @(
        @{ role = 'registry'; sha256 = $TrustedBinding.registry_sha256 },
        @{ role = 'metadata'; sha256 = $TrustedBinding.metadata_sha256 },
        @{ role = 'artifact'; sha256 = $TrustedBinding.artifact_sha256 },
        @{ role = 'platform_catalog'; sha256 = $TrustedBinding.platform_catalog_sha256 },
        @{ role = 'author_catalog'; sha256 = $TrustedBinding.author_catalog_sha256 }
    )) {
        if ($sourcePins[$pin.role] -cne $pin.sha256) { Fail 'Acceptance SFC full source pins do not match the trusted asset hashes' }
    }
    foreach ($requiredRole in @('author_inference_source', 'author_reference_output')) {
        if (!$sourcePins.ContainsKey($requiredRole)) { Fail 'Acceptance SFC full warning evidence source pin is missing' }
    }
    $authorReference = $fixture.author_reference
    $warningBasis = $authorReference.warning_basis
    $nanColumns = @($authorReference.observed_author_feature_nan_columns)
    $expectedWarnings = @($nanColumns | ForEach-Object { "FEATURE_VALUE_MISSING:$_" })
    $actualWarnings = @($authorReference.expected_warnings)
    if ($nanColumns.Count -eq 0 -or ($nanColumns -join "`n") -cne (@($TrustedBinding.expected_nan_features) -join "`n") -or
        ($actualWarnings -join "`n") -cne ($expectedWarnings -join "`n") -or
        $warningBasis.source_role -cne 'author_inference_source' -or
        $warningBasis.source_sha256 -cne $sourcePins['author_inference_source'] -or
        $warningBasis.result_role -cne 'author_reference_output' -or
        $warningBasis.result_sha256 -cne $sourcePins['author_reference_output'] -or
        (@($warningBasis.observed_nan_features) -join "`n") -cne ($nanColumns -join "`n") -or
        !$warningBasis.explanation -or
        [double]$authorReference.tolerance.absolute -ne 1e-12 -or
        [double]$authorReference.tolerance.relative -ne 1e-12 -or
        $authorReference.tolerance.rule -cne 'Python math.isclose(actual, reference, abs_tol=1e-12, rel_tol=1e-12)') {
        Fail 'Acceptance SFC full author warning or tolerance evidence is incomplete'
    }
}

function Assert-AssistantAcceptanceBindings([pscustomobject]$Config) {
    if ((Get-Port $Config 'API_PORT') -ne 9012 -or (Get-Port $Config 'FRONTEND_PORT') -ne 5174) { Fail 'Acceptance ports do not match the fixed isolated ports' }
    if ((Get-ConfigValue $Config @('WORKER_QUEUE')) -cne 'chrom-platform-assistant-accept-20261006') { Fail 'Acceptance worker namespace is invalid' }
    $databaseName = 'chrom_assistant_accept_20261006'
    if ((Get-ConfigValue $Config @('CHROM_ACCEPTANCE_DATABASE_SCHEMA')) -cne $databaseName) { Fail 'Acceptance schema binding is invalid' }
    if ([string]::IsNullOrWhiteSpace($script:AcceptanceDatabaseUrl) -or [string]::IsNullOrWhiteSpace($script:AcceptanceSyncDatabaseUrl)) { Fail 'Acceptance in-memory database bindings are unavailable' }
    try { $redis = [Uri](Get-ConfigValue $Config @('REDIS_URL')) } catch { Fail 'Acceptance Redis URL is invalid' }
    if ($redis.Scheme -ne 'redis' -or $redis.Host -ne '127.0.0.1' -or $redis.Port -ne 6379) { Fail 'Acceptance Redis endpoint must use the approved local container binding' }
    $expectedArtifacts = [IO.Path]::GetFullPath((Join-Path $runtimeRoot 'artifacts'))
    if ([IO.Path]::GetFullPath((Get-ConfigValue $Config @('ARTIFACT_ROOT'))) -ne $expectedArtifacts) { Fail 'Acceptance artifact root must remain isolated' }
    $null = Assert-UnderRoot (Get-ConfigValue $Config @('WORKER_READY_FILE')) $stateRoot 'Acceptance worker ready file'
    $null = Assert-UnderRoot (Get-ConfigValue $Config @('CHROM_SFC_SCOPE_STORAGE_PATH')) $stateRoot 'Acceptance SFC scope state'
    $secretRoot = [IO.Path]::GetFullPath((Get-ConfigValue $Config @('CHROM_ASSISTANT_SECRET_ROOT')))
    $expectedSecretRoot = [IO.Path]::GetFullPath((Join-Path $runtimeRoot 'assistant-llm-secrets'))
    if ($secretRoot -ne $expectedSecretRoot -or !(Test-Path -LiteralPath $secretRoot -PathType Container)) { Fail 'Acceptance DPAPI secret root is missing or outside its fixed location' }
    Assert-DpapiNoReparse $secretRoot 'Acceptance DPAPI secret root' | Out-Null
    Assert-DpapiProtectedAcl $secretRoot $true 'Acceptance DPAPI secret root'
    $allowlist = Get-ConfigValue $Config @('LLM_PROVIDER_APPROVED_ENDPOINTS')
    $endpoints = @($allowlist -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($endpoints.Count -ne 1) { Fail 'Acceptance requires exactly one explicit approved provider endpoint' }
    $approvedEndpoint = Get-NormalizedProviderBase $endpoints[0]
    if (!$approvedEndpoint -or !$script:AcceptanceApprovedEndpoint -or $approvedEndpoint -cne $script:AcceptanceApprovedEndpoint) { Fail 'Acceptance endpoint must exactly match the protected approved provider base' }
    $acceptanceDirectories = @($runtimeRoot, $stateRoot, $pidRoot, $logRoot, $expectedArtifacts, $secretRoot)
    foreach ($path in $acceptanceDirectories) {
        if (!(Test-Path -LiteralPath $path -PathType Container)) { Fail 'Acceptance protected directory is missing' }
        Assert-DpapiNoReparse $path 'Acceptance protected directory' | Out-Null
        Assert-DpapiProtectedAcl $path $true 'Acceptance protected directory'
    }
    foreach ($name in @('.env', '.env.local', '.env.development', '.env.development.local')) {
        if (Test-Path -LiteralPath (Join-Path (Join-Path $repo 'frontend') $name) -PathType Leaf) { Fail 'Acceptance Vite working directory must not contain an independent dotenv file' }
    }
}

function Get-NormalizedProviderBase([string]$Value) {
    try { $uri = [Uri]$Value } catch { return $null }
    if (!$uri.IsAbsoluteUri -or $uri.Scheme -cne 'https' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) { return $null }
    $port = if ($uri.IsDefaultPort) { 443 } else { $uri.Port }
    $path = $uri.AbsolutePath.TrimEnd('/')
    return ('{0}://{1}:{2}{3}' -f $uri.Scheme.ToLowerInvariant(), $uri.Host.ToLowerInvariant(), $port, $path)
}

function Get-AcceptanceDatabaseBindings([pscustomobject]$Config) {
    if ($DataAcceptanceMode) {
        # These URLs are separately provisioned for the data-only schema. Never
        # read formal/assistant credentials and never rewrite their database name.
        $script:AcceptanceDatabaseUrl = Get-ConfigValue $Config @('DATABASE_URL')
        $script:AcceptanceSyncDatabaseUrl = Get-ConfigValue $Config @('SYNC_DATABASE_URL')
        if ([string]::IsNullOrWhiteSpace($script:AcceptanceDatabaseUrl) -or [string]::IsNullOrWhiteSpace($script:AcceptanceSyncDatabaseUrl)) {
            Fail 'Data acceptance database bindings are unavailable in its protected runtime'
        }
        return
    }
    $formalRoot = [IO.Path]::GetDirectoryName($formalConfig)
    Assert-DpapiNoReparse $formalRoot 'Formal database runtime directory' | Out-Null
    Assert-DpapiProtectedAcl $formalRoot $true 'Formal database runtime directory'
    Assert-DpapiNoReparse $formalConfig 'Formal database runtime configuration' | Out-Null
    Assert-DpapiProtectedAcl $formalConfig $false 'Formal database runtime configuration'
    try { $formal = Get-Content -LiteralPath $formalConfig -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Fail 'Formal protected database runtime is unreadable' }
    $script:AcceptanceApprovedEndpoint = Get-NormalizedProviderBase (Get-ConfigValue $formal @('LLM_PROVIDER_URL'))
    if (!$script:AcceptanceApprovedEndpoint) { Fail 'Formal protected runtime has no approved HTTPS provider base' }
    foreach ($spec in @(@{Key='DATABASE_URL';Driver='asyncmy'},@{Key='SYNC_DATABASE_URL';Driver='pymysql'})) {
        $value = Get-ConfigValue $formal @($spec.Key)
        $pattern = '^(mysql\+' + [regex]::Escape($spec.Driver) + '://[^/]+@127\.0\.0\.1:3306/)(?<schema>[^/?#]+)(?<query>\?.*)?$'
        if ($value -notmatch $pattern -or $Matches.schema -cne 'chrom_platform') { Fail 'Formal database runtime does not match the audited local schema binding' }
        $target = $Matches[1] + 'chrom_assistant_accept_20261006' + $Matches.query
        if ($spec.Key -eq 'DATABASE_URL') { $script:AcceptanceDatabaseUrl = $target } else { $script:AcceptanceSyncDatabaseUrl = $target }
    }
}

function Get-ProviderSecret([pscustomobject]$Config) {
    $path = Get-ConfigValue $Config @('LLM_SECRET_SOURCE_PATH')
    if (!$path) { Fail 'Protected provider secret reference is missing' }
    try { return Unprotect-DpapiSecretFromFile $path $runtimeRoot } catch { Fail 'Protected provider secret could not be decrypted for the current Windows user' }
}

function Get-BatchCursorSecret([pscustomobject]$Config) {
    $path = Get-ConfigValue $Config @('CHROM_BATCH_CURSOR_KEY_SOURCE_PATH')
    if (!$path) { Fail 'Protected batch cursor signing key reference is missing' }
    try { $secret = Unprotect-DpapiSecretFromFile $path $runtimeRoot } catch { Fail 'Protected batch cursor signing key could not be decrypted for the current Windows user' }
    if ($secret -notmatch '^[A-Za-z0-9_-]{43}$') { Fail 'Protected batch cursor signing key must be canonical base64url for 32 random bytes' }
    try { $raw = [Convert]::FromBase64String(($secret.Replace('-', '+').Replace('_', '/') + '=')) } catch { Fail 'Protected batch cursor signing key encoding is invalid' }
    try {
        $canonical = [Convert]::ToBase64String($raw).TrimEnd('=').Replace('+', '-').Replace('/', '_')
        if ($raw.Length -ne 32 -or $canonical -cne $secret) { Fail 'Protected batch cursor signing key must contain exactly 32 random bytes' }
    } finally { if ($raw) { [Array]::Clear($raw, 0, $raw.Length) } }
    return $secret
}

function Invoke-ChildStart([string]$File, [string[]]$Arguments, [string]$Stdout, [string]$Stderr, [bool]$NeedsProviderSecret, [bool]$NeedsBatchCursorKey, [pscustomobject]$Config, [string]$WorkingDirectory) {
    $oldApiKey = [Environment]::GetEnvironmentVariable('API_KEY', 'Process')
    $hadApiKey = $null -ne $oldApiKey
    $oldDatabaseUrl = [Environment]::GetEnvironmentVariable('DATABASE_URL', 'Process')
    $oldSyncDatabaseUrl = [Environment]::GetEnvironmentVariable('SYNC_DATABASE_URL', 'Process')
    $oldCursorSigningKey = [Environment]::GetEnvironmentVariable('CHROM_BATCH_CURSOR_SIGNING_KEY', 'Process')
    $stripDatabaseForFrontend = [IO.Path]::GetFullPath($WorkingDirectory) -eq [IO.Path]::GetFullPath((Join-Path $repo 'frontend')) -or
        ($DataAcceptanceMode -and [IO.Path]::GetFullPath($WorkingDirectory) -eq $dataFrontendRoot)
    $secret = $null
    $cursorSecret = $null
    try {
        [Environment]::SetEnvironmentVariable('CHROM_BATCH_CURSOR_SIGNING_KEY', $null, 'Process')
        if ($NeedsBatchCursorKey) {
            $cursorSecret = Get-BatchCursorSecret $Config
            [Environment]::SetEnvironmentVariable('CHROM_BATCH_CURSOR_SIGNING_KEY', $cursorSecret, 'Process')
        }
        if ($NeedsProviderSecret) {
            $secret = Get-ProviderSecret $Config
            [Environment]::SetEnvironmentVariable('API_KEY', $secret, 'Process')
        } else {
            # A frontend child must never inherit a caller-provided provider secret.
            Remove-DpapiProcessApiKey
        }
        if ($stripDatabaseForFrontend) {
            [Environment]::SetEnvironmentVariable('DATABASE_URL', $null, 'Process')
            [Environment]::SetEnvironmentVariable('SYNC_DATABASE_URL', $null, 'Process')
        }
        return Start-Process -FilePath $File -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
    } finally {
        if ($hadApiKey) { [Environment]::SetEnvironmentVariable('API_KEY', $oldApiKey, 'Process') } else { Remove-DpapiProcessApiKey }
        [Environment]::SetEnvironmentVariable('DATABASE_URL', $oldDatabaseUrl, 'Process')
        [Environment]::SetEnvironmentVariable('SYNC_DATABASE_URL', $oldSyncDatabaseUrl, 'Process')
        [Environment]::SetEnvironmentVariable('CHROM_BATCH_CURSOR_SIGNING_KEY', $oldCursorSigningKey, 'Process')
        $cursorSecret = $null
        $secret = $null
    }
}

function Get-StatePath([string]$Name) { return Join-Path $pidRoot "$Name.json" }

function Read-State([string]$Name) {
    $path = Get-StatePath $Name
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Convert-StateCreatedAtToUtc([object]$Value) {
    if ($null -eq $Value) { return $null }
    # ConvertFrom-Json materializes ISO values as DateTime. Preserve that
    # runtime kind instead of reparsing the local display string as UTC.
    if ($Value -is [DateTimeOffset]) { return $Value.UtcDateTime }
    if ($Value -is [DateTime]) {
        if ($Value.Kind -eq [DateTimeKind]::Utc) { return $Value }
        if ($Value.Kind -eq [DateTimeKind]::Local) { return $Value.ToUniversalTime() }
        # The state writer's legacy no-offset form was explicitly UTC. An
        # Unspecified value can therefore only be accepted under that state
        # contract; it is never converted through the machine local zone.
        return [DateTime]::SpecifyKind($Value, [DateTimeKind]::Utc)
    }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    if ($text -notmatch '(?i)(?:Z|[+-]\d{2}:\d{2})$') {
        $legacy = [DateTime]::MinValue
        if (![DateTime]::TryParse($text, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind, [ref]$legacy)) { return $null }
        # The pre-offset state writer emitted UTC text without a suffix. Do
        # not let DateTimeOffset.TryParse silently apply the local time zone.
        return [DateTime]::SpecifyKind($legacy, [DateTimeKind]::Utc)
    }
    $parsed = [DateTimeOffset]::MinValue
    if (![DateTimeOffset]::TryParse($text, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind, [ref]$parsed)) { return $null }
    # State timestamps without an offset are the legacy UTC representation;
    # offset-bearing and Z strings retain their encoded offset.
    return $parsed.UtcDateTime
}

function Test-ManagedProcess($State) {
    if ($null -eq $State -or $null -eq $State.pid -or $null -eq $State.created_at) { return $false }
    try {
        $process = Get-Process -Id ([int]$State.pid) -ErrorAction Stop
        $actual = $process.StartTime.ToUniversalTime()
        $expected = Convert-StateCreatedAtToUtc $State.created_at
        if ($null -eq $expected) { return $false }
        return ([Math]::Abs(($actual - $expected).TotalSeconds) -le 2)
    } catch { return $false }
}

function Assert-SelectedState($State) {
    if (!$AcceptanceMode -or $null -eq $State) { return }
    try { $recorded = [IO.Path]::GetFullPath([string]$State.config) } catch { Fail 'Managed process state does not identify its protected runtime' }
    if ($recorded -cne [IO.Path]::GetFullPath($ConfigFile)) { Fail 'Managed process state belongs to a different runtime profile' }
}

function Write-State($Name, $State) {
    $path = Get-StatePath $Name
    $tmp = "$path.$PID.tmp"
    $State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $tmp -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath $tmp -Destination $path -Force
}

function Remove-State([string]$Name) {
    $path = Get-StatePath $Name
    if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
}

function Assert-PortFree([int]$Port, [string]$Name) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) { return }
    $state = Read-State $Name
    $managed = $false
    foreach ($item in $listeners) { if ($null -ne $state -and [int]$item.OwningProcess -eq [int]$state.pid -and (Test-ManagedProcess $state)) { $managed = $true } }
    if (!$managed) { Fail "Port $Port is already occupied; refusing to contend with an existing process" }
}

function Start-One([string]$Name, [pscustomobject]$Config) {
    $old = Read-State $Name
    if (Test-ManagedProcess $old) { Fail "$Name is already running under the recorded PID and creation time" }
    if ($Name -eq 'api') { Assert-PortFree (Get-Port $Config 'API_PORT') 'api' }
    if ($Name -eq 'frontend') { Assert-PortFree (Get-Port $Config 'FRONTEND_PORT') 'frontend' }
    $stdout = Join-Path $logRoot "$Name.stdout.log"
    $stderr = Join-Path $logRoot "$Name.stderr.log"
    if ($Name -eq 'api') {
        $file = $Python; $args = @('-m', 'uvicorn', 'chrom_platform.app:create_app_from_environment', '--factory', '--host', '127.0.0.1', '--port', (Get-Port $Config 'API_PORT'))
    } elseif ($Name -eq 'worker') {
        $file = $Python; $args = @('-m', 'chrom_platform.worker_cli')
    } else {
        $vite = Assert-File (Join-Path $repo 'frontend\node_modules\vite\bin\vite.js') 'Vite entry'
        $file = $Node; $args = @($vite, '--host', '127.0.0.1', '--port', (Get-Port $Config 'FRONTEND_PORT'), '--strictPort')
    }
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { Fail "Required runtime executable is missing: $file" }
    $needsProviderSecret = ($Name -in @('api', 'worker')) -and !$AcceptanceMode
    $needsBatchCursorKey = $Name -eq 'api' -and [bool]$script:BatchCursorRequired
    $workingDirectory = if ($Name -eq 'frontend' -and $DataAcceptanceMode) { $dataFrontendRoot } elseif ($Name -eq 'frontend' -and $AcceptanceMode) { Join-Path $repo 'frontend' } else { $repo }
    $process = Invoke-ChildStart $file $args $stdout $stderr $needsProviderSecret $needsBatchCursorKey $Config $workingDirectory
    $createdAt = ([DateTimeOffset]$process.StartTime.ToUniversalTime()).ToString('o', [Globalization.CultureInfo]::InvariantCulture)
    $state = [ordered]@{ service = $Name; pid = $process.Id; created_at = $createdAt; stdout = $stdout; stderr = $stderr; ready_file = (Get-ConfigValue $Config @('WORKER_READY_FILE')); config = $ConfigFile }
    Write-State $Name $state
    Write-Output ("STARTED service={0} pid={1}" -f $Name, $process.Id)
}

function Stop-One([string]$Name) {
    $state = Read-State $Name
    Assert-SelectedState $state
    if (!(Test-ManagedProcess $state)) {
        Remove-State $Name
        Write-Output ("STOPPED service={0} status=not-managed" -f $Name)
        return
    }
    $pidValue = [int]$state.pid
    Stop-Process -Id $pidValue -Force -ErrorAction Stop
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do { Start-Sleep -Milliseconds 200; $alive = Test-ManagedProcess $state } while ($alive -and [DateTime]::UtcNow -lt $deadline)
    if ($alive) { Fail "$Name did not stop within the identity-gated timeout" }
    if ($Name -eq 'worker') {
        $ready = [string]$state.ready_file
        if ($ready -and (Test-Path -LiteralPath $ready -PathType Leaf)) {
            try { $marker = Get-Content -LiteralPath $ready -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $marker = $null }
            if ($null -ne $marker -and [int]$marker.pid -eq $pidValue) { Remove-Item -LiteralPath $ready -Force }
        }
    }
    Remove-State $Name
    Write-Output ("STOPPED service={0} pid={1}" -f $Name, $pidValue)
}

function Get-Status([string]$Name) {
    $state = Read-State $Name
    Assert-SelectedState $state
    $running = Test-ManagedProcess $state
    [pscustomobject]@{ service = $Name; status = if ($running) { 'RUNNING' } else { 'STOPPED' }; pid = if ($running) { [int]$state.pid } else { $null }; created_at = if ($running) { [string]$state.created_at } else { $null }; stdout = if ($null -ne $state) { [string]$state.stdout } else { $null }; stderr = if ($null -ne $state) { [string]$state.stderr } else { $null }; config = if ($null -ne $state) { [string]$state.config } else { $null } }
}

if ($DataAcceptanceMode -and $Action -in @('start', 'stop', 'restart')) { Assert-DataLauncherParent $Action }
$config = if ($Action -eq 'status' -and $AcceptanceMode) { $null } else { Read-Config }
if ($Action -eq 'status') {
    $names = if ($Service -eq 'all') { @('api', 'worker', 'frontend') } else { @($Service) }
    $names | ForEach-Object { Get-Status $_ } | ConvertTo-Json -Depth 4
    exit 0
}

if (!(Test-Path -LiteralPath $stateRoot -PathType Container)) { New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null }
if (!(Test-Path -LiteralPath $logRoot -PathType Container)) { New-Item -ItemType Directory -Path $logRoot -Force | Out-Null }
if (!(Test-Path -LiteralPath $pidRoot -PathType Container)) { New-Item -ItemType Directory -Path $pidRoot -Force | Out-Null }
if ($AcceptanceMode) { Get-AcceptanceDatabaseBindings $config }
Set-ProcessEnvironment $config
Assert-DeploymentBindings $config | Out-Null
if ($Action -eq 'validate') {
    if ($AcceptanceMode) { Write-Output 'ASSISTANT_ACCEPTANCE_RUNTIME_VALIDATED' } else { Write-Output 'PRIORITY_LOCAL_RUNTIME_VALIDATED' }
    exit 0
}

$names = if ($Service -eq 'all') { @('api', 'worker', 'frontend') } else { @($Service) }
if ($Action -eq 'stop') {
    [Array]::Reverse($names)
    foreach ($name in $names) { Stop-One $name }
    exit 0
}
if ($Action -eq 'restart') {
    [Array]::Reverse($names)
    foreach ($name in $names) { Stop-One $name }
    [Array]::Reverse($names)
    foreach ($name in $names) { Start-One $name $config }
    exit 0
}
foreach ($name in $names) { Start-One $name $config }
