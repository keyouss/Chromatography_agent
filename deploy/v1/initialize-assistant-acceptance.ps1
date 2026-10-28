[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$localRuntimeBase = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform'
$formalRuntime = Join-Path (Join-Path $localRuntimeBase 'priority-local') 'runtime.json'
$acceptanceRoot = [IO.Path]::GetFullPath((Join-Path $localRuntimeBase 'assistant-accept-20261006'))
$acceptanceRuntime = Join-Path $acceptanceRoot 'runtime.json'
. (Join-Path $PSScriptRoot 'dpapi-secret-source.ps1')

function Fail-Acceptance([string]$Message) { throw $Message }

if (!(Test-Path -LiteralPath $formalRuntime -PathType Leaf)) { Fail-Acceptance 'Formal protected runtime is missing' }
if (Test-Path -LiteralPath $acceptanceRoot) { Fail-Acceptance 'Acceptance root already exists; preserving its current contents for review' }
Assert-DpapiNoReparse $localRuntimeBase 'Protected runtime parent' | Out-Null

try { $config = Get-Content -LiteralPath $formalRuntime -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Fail-Acceptance 'Formal protected runtime JSON is invalid' }
if ($config -isnot [pscustomobject]) { Fail-Acceptance 'Formal protected runtime must be a JSON object' }

$providerUrlProperty = $config.PSObject.Properties['LLM_PROVIDER_URL']
if ($null -eq $providerUrlProperty) { Fail-Acceptance 'Formal provider endpoint is unavailable for explicit approval' }
try { $providerUri = [Uri][string]$providerUrlProperty.Value } catch { Fail-Acceptance 'Formal provider endpoint is invalid' }
if (!$providerUri.IsAbsoluteUri -or $providerUri.Scheme -cne 'https' -or !$providerUri.Host.EndsWith('.siliconflow.cn', [StringComparison]::OrdinalIgnoreCase) -or $providerUri.UserInfo -or $providerUri.Query -or $providerUri.Fragment) { Fail-Acceptance 'Only the explicitly authorized HTTPS SiliconFlow base endpoint can be approved' }

$config.PSObject.Properties.Remove('DATABASE_URL')
$config.PSObject.Properties.Remove('SYNC_DATABASE_URL')
$config.API_PORT = '9012'
$config.FRONTEND_PORT = '5174'
$config | Add-Member -NotePropertyName WORKER_QUEUE -NotePropertyValue 'chrom-platform-assistant-accept-20261006' -Force
$config.ARTIFACT_ROOT = Join-Path $acceptanceRoot 'artifacts'
$config.WORKER_READY_FILE = Join-Path (Join-Path $acceptanceRoot 'state') 'worker-ready.json'
$config.CHROM_SFC_SCOPE_STORAGE_PATH = Join-Path (Join-Path $acceptanceRoot 'state') 'sfc-scope.sqlite3'
$config | Add-Member -NotePropertyName CHROM_ASSISTANT_SECRET_ROOT -NotePropertyValue (Join-Path $acceptanceRoot 'assistant-llm-secrets') -Force
$config | Add-Member -NotePropertyName LLM_PROVIDER_APPROVED_ENDPOINTS -NotePropertyValue ([string]$providerUrlProperty.Value) -Force
$config | Add-Member -NotePropertyName CHROM_ACCEPTANCE_DATABASE_SCHEMA -NotePropertyValue 'chrom_assistant_accept_20261006' -Force
foreach ($key in @('LLM_PROVIDER_URL', 'LLM_MODEL_ID', 'LLM_KEY_REFERENCE', 'LLM_SECRET_SOURCE_PATH')) {
    $property = $config.PSObject.Properties[$key]
    if ($null -ne $property) { $config.PSObject.Properties.Remove($key) }
}

$directories = @(
    $acceptanceRoot,
    (Join-Path $acceptanceRoot 'state'),
    (Join-Path (Join-Path $acceptanceRoot 'state') 'pids'),
    (Join-Path $acceptanceRoot 'logs'),
    (Join-Path $acceptanceRoot 'artifacts'),
    (Join-Path $acceptanceRoot 'assistant-llm-secrets')
)
foreach ($directory in $directories) {
    New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null
    Assert-DpapiNoReparse $directory 'Acceptance runtime directory' | Out-Null
    Set-DpapiProtectedAcl $directory $true 'Acceptance runtime directory'
}

try {
    $json = $config | ConvertTo-Json -Depth 20
    $tmp = Join-Path $acceptanceRoot ('.runtime.' + $PID + '.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp, $json, (New-Object Text.UTF8Encoding($false)))
    Set-DpapiProtectedAcl $tmp $false 'Acceptance runtime temporary file'
    Move-Item -LiteralPath $tmp -Destination $acceptanceRuntime
    Assert-DpapiNoReparse $acceptanceRuntime 'Acceptance runtime configuration' | Out-Null
    Assert-DpapiProtectedAcl $acceptanceRuntime $false 'Acceptance runtime configuration'
} catch {
    throw 'Acceptance runtime creation failed; preserve the partial directory for inspection and do not rerun blindly'
} finally {
    if ($tmp -and (Test-Path -LiteralPath $tmp -PathType Leaf)) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}

Write-Output 'ASSISTANT_ACCEPTANCE_RUNTIME_READY schema=chrom_assistant_accept_20261006 api_port=9012 web_port=5174 queue=chrom-platform-assistant-accept-20261006 approved_endpoints=1'
