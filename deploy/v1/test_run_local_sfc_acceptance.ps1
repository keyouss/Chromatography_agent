[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$runLocalPath = Join-Path $PSScriptRoot 'run-local.ps1'
$tokens = $null
$parseErrors = $null
$scriptAst = [System.Management.Automation.Language.Parser]::ParseFile($runLocalPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw 'run-local.ps1 has PowerShell parse errors' }

# Load only the pure binding/hash functions; never dot-source run-local.ps1 or read runtime.json.
foreach ($name in @('Fail', 'Get-ConfigValue', 'Get-Sha256', 'Assert-Hash', 'Assert-SfcModelBinding', 'Assert-AcceptanceSfcBindings')) {
    $functionAst = $scriptAst.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if ($null -eq $functionAst) { throw "Function not found: $name" }
    Invoke-Expression $functionAst.Extent.Text
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
}

function Assert-Rejected([scriptblock]$Action, [string]$Name, [string]$ForbiddenText = '') {
    try {
        & $Action
    } catch {
        if ($ForbiddenText -and $_.Exception.Message.Contains($ForbiddenText)) { throw "$Name leaked an untrusted value" }
        return
    }
    throw "$Name was accepted"
}

function Copy-Config([pscustomobject]$Config) {
    $copy = [ordered]@{}
    foreach ($property in $Config.PSObject.Properties) { $copy[$property.Name] = $property.Value }
    return [pscustomobject]$copy
}

$modelsRoot = 'D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\models'
$registryPath = Join-Path $modelsRoot 'registry.json'
$catalogPath = Join-Path $repo 'Services\sfc_assets\conditions.sqlite'
$fixturePath = Join-Path $repo 'testing\fixtures\priority-launch\sfc-single-reference-2p5-warning-v3.json'
$fixture = Get-Content -LiteralPath $fixturePath -Raw -Encoding UTF8 | ConvertFrom-Json
$config = [pscustomobject]@{
    CHROM_SFC_MODEL_ID = 'sc-rs12-amylose-dmpc-coated'
    CHROM_SFC_MODEL_VERSION = '1'
    CHROM_SFC_BUSINESS_SCOPE_ID = 'priority-local'
    CHROM_SFC_MODEL_ROOT = $modelsRoot
    CHROM_SFC_REGISTRY_PATH = $registryPath
    CHROM_SFC_REGISTRY_SHA256 = $fixture.model.registry_sha256
    CHROM_SFC_ARTIFACT_SHA256 = $fixture.model.artifact_sha256
    CHROM_SFC_CANDIDATE_DB_PATH = $catalogPath
    CHROM_SFC_GOLDEN_FIXTURE = $fixturePath
}

Assert-SfcModelBinding $config $true
$null = Assert-AcceptanceSfcBindings $config
Write-Output 'PASS acceptance binds RS12 model/version, priority-local, exact registry root, platform catalog, and v3 fixture pins'

$formalConfig = Copy-Config $config
$formalConfig.CHROM_SFC_MODEL_ID = 'rs15-extra-trees-s80-seed2024'
$formalConfig.CHROM_SFC_BUSINESS_SCOPE_ID = 'PRIORITY-LOCAL'
Assert-SfcModelBinding $formalConfig $false
Assert-Rejected { Assert-SfcModelBinding $config $false } 'formal mode accepting RS12'
Assert-Rejected { Assert-SfcModelBinding $formalConfig $true } 'acceptance mode accepting RS15'
Write-Output 'PASS formal identity remains RS15/version 1/priority-local; acceptance rejects RS15'

$negativeCases = @(
    @{ Name = 'unknown model'; Key = 'CHROM_SFC_MODEL_ID'; Value = 'sentinel-secret-unknown-model' },
    @{ Name = 'wrong model version'; Key = 'CHROM_SFC_MODEL_VERSION'; Value = '2' },
    @{ Name = 'wrong business scope'; Key = 'CHROM_SFC_BUSINESS_SCOPE_ID'; Value = 'other-project' },
    @{ Name = 'non-exact acceptance business scope'; Key = 'CHROM_SFC_BUSINESS_SCOPE_ID'; Value = 'PRIORITY-LOCAL' },
    @{ Name = 'arbitrary model root'; Key = 'CHROM_SFC_MODEL_ROOT'; Value = 'D:\untrusted-secret-root' },
    @{ Name = 'arbitrary registry'; Key = 'CHROM_SFC_REGISTRY_PATH'; Value = 'D:\untrusted-secret-root\registry.json' },
    @{ Name = 'arbitrary catalog'; Key = 'CHROM_SFC_CANDIDATE_DB_PATH'; Value = 'D:\untrusted-secret-catalog.sqlite' },
    @{ Name = 'arbitrary fixture'; Key = 'CHROM_SFC_GOLDEN_FIXTURE'; Value = 'D:\sentinel-secret-fixture.json' },
    @{ Name = 'wrong registry digest'; Key = 'CHROM_SFC_REGISTRY_SHA256'; Value = ('0' * 64) },
    @{ Name = 'wrong artifact binding'; Key = 'CHROM_SFC_ARTIFACT_SHA256'; Value = ('f' * 64) }
)
foreach ($case in $negativeCases) {
    $candidateConfig = Copy-Config $config
    $candidateConfig.($case.Key) = $case.Value
    Assert-Rejected { Assert-AcceptanceSfcBindings $candidateConfig } $case.Name 'sentinel-secret'
}
Write-Output ('PASS fail-closed mismatch cases: {0}; error messages do not echo sentinel paths/model ids' -f $negativeCases.Count)

$runLocalText = Get-Content -LiteralPath $runLocalPath -Raw -Encoding UTF8
$acceptanceSfcBranch = '(?s)if \(\$AcceptanceMode\) \{\s*Assert-AcceptanceSfcBindings \$Config\s*\} else \{'
Assert-True ([regex]::IsMatch($runLocalText, $acceptanceSfcBranch)) 'Acceptance SFC validator is not wired to the acceptance-only branch'
$assemblyPath = Join-Path $repo 'backend\src\chrom_platform\assembly\prediction.py'
$assemblyText = Get-Content -LiteralPath $assemblyPath -Raw -Encoding UTF8
$pinMatch = [regex]::Match($assemblyText, 'SFC_SINGLE_REFERENCE_SHA256\s*=\s*"([0-9a-f]{64})"')
Assert-True ($pinMatch.Success -and $pinMatch.Groups[1].Value -eq (Get-Sha256 $fixturePath)) 'Assembly fixture auto pin does not match the fixed v3 fixture'
Assert-True ($runLocalText.Contains("Assert-Hash `$candidate '266a6d00df80166c3faf64be9dc21748d9a39a9f7106a24b8741fdd628eb9c1d' 'SFC candidate database'")) 'Formal SFC catalog hash changed'
Assert-True ($runLocalText.Contains("Assert-Hash (Get-ConfigValue `$Config @('CHROM_SFC_GOLDEN_FIXTURE')) '335f027e3483dc36e3f7f1be4a8d00e00471880ce436b93b5b67c85dd5bd2905' 'SFC golden fixture'")) 'Formal SFC fixture hash changed'
Write-Output 'PASS acceptance branch is wired and fixture auto pin matches the v3 fixture'
Write-Output 'PASS formal RS15 candidate and golden pins remain unchanged'
