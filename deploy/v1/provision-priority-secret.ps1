[CmdletBinding()]
param(
    [string]$DotEnv,
    [string]$RuntimeFile = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform\priority-local\runtime.json')
)

$ErrorActionPreference = 'Stop'
if ($null -eq $DotEnv -or !$DotEnv) { $DotEnv = Join-Path $PSScriptRoot '..\..\.env' }
. (Join-Path $PSScriptRoot 'dpapi-secret-source.ps1')

function Fail-Provision([string]$Message) { throw $Message }

function Read-ExactProviderTriple([string]$Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { Fail-Provision 'Project .env is missing' }
    try { $lines = [IO.File]::ReadAllLines([IO.Path]::GetFullPath($Path), (New-Object Text.UTF8Encoding($false, $true))) } catch { Fail-Provision 'Project .env must be readable UTF-8 text' }
    $values = @{}
    foreach ($line in $lines) {
        $trim = $line.Trim()
        if (!$trim -or $trim.StartsWith('#') -or $trim.IndexOf('=') -lt 1) { continue }
        $index = $trim.IndexOf('=')
        $key = $trim.Substring(0, $index).Trim()
        if ($key -notin @('BASE_URL', 'MODEL_ID', 'API_KEY')) { continue }
        if ($values.ContainsKey($key)) { Fail-Provision "Project .env contains a duplicate $key" }
        $value = $trim.Substring($index + 1).Trim()
        if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[$value.Length - 1] -eq '"') -or ($value[0] -eq "'" -and $value[$value.Length - 1] -eq "'"))) { $value = $value.Substring(1, $value.Length - 2) }
        if ([string]::IsNullOrWhiteSpace($value)) { Fail-Provision "Project .env contains an empty $key" }
        $values[$key] = $value
    }
    foreach ($key in @('BASE_URL', 'MODEL_ID', 'API_KEY')) { if (!$values.ContainsKey($key)) { Fail-Provision "Project .env is missing $key" } }
    try { $uri = [Uri]$values['BASE_URL'] } catch { Fail-Provision 'Project BASE_URL is invalid' }
    if (!$uri.IsAbsoluteUri -or $uri.UserInfo -or $uri.Fragment -or $uri.Query -match '(?i)(api[_-]?key|token|secret|password)=') { Fail-Provision 'Project BASE_URL contains credentials or secret query parameters' }
    return [pscustomobject]@{ BASE_URL = $values['BASE_URL']; MODEL_ID = $values['MODEL_ID']; API_KEY = $values['API_KEY'] }
}

$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$expectedDotEnv = [IO.Path]::GetFullPath((Join-Path $repo '.env'))
$runtimeFile = [IO.Path]::GetFullPath($RuntimeFile)
$runtimeRoot = [IO.Path]::GetDirectoryName($runtimeFile)
$expectedRuntime = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform\priority-local\runtime.json'))
if ($runtimeFile -ne $expectedRuntime) { Fail-Provision 'Only the protected priority-local runtime.json is accepted' }
if ([IO.Path]::GetFullPath($DotEnv) -ne $expectedDotEnv) { Fail-Provision 'Only the project root .env is accepted as the migration source' }
if (!(Test-Path -LiteralPath $runtimeFile -PathType Leaf)) { Fail-Provision 'Protected runtime configuration file is missing' }
Assert-DpapiNoReparse $runtimeRoot 'Protected runtime directory' | Out-Null
Assert-DpapiNoReparse $runtimeFile 'Protected runtime configuration' | Out-Null

$provider = Read-ExactProviderTriple $expectedDotEnv
try { $config = Get-Content -LiteralPath $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Fail-Provision 'Protected runtime configuration must be valid UTF-8 JSON' }
if ($config -isnot [pscustomobject]) { Fail-Provision 'Protected runtime configuration must be a JSON object' }
foreach ($property in $config.PSObject.Properties) {
    if ($property.Name -cnotmatch '^[A-Z][A-Z0-9_]*$' -or $property.Value -isnot [string]) { Fail-Provision 'Protected runtime configuration has an invalid key or value' }
    if ($property.Name -match '(?i)(^|_)(API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)(_|$)' -and $property.Name -ne 'LLM_SECRET_SOURCE_PATH') { Fail-Provision 'Protected runtime configuration must not contain raw secret keys' }
}
$url = $config.PSObject.Properties['LLM_PROVIDER_URL']
$model = $config.PSObject.Properties['LLM_MODEL_ID']
$reference = $config.PSObject.Properties['LLM_KEY_REFERENCE']
if ($null -eq $url -or [string]$url.Value -ne $provider.BASE_URL) { Fail-Provision 'Protected runtime provider URL does not match the project .env' }
if ($null -eq $model -or [string]$model.Value -ne $provider.MODEL_ID) { Fail-Provision 'Protected runtime model does not match the project .env' }
if ($null -eq $reference -or [string]$reference.Value -ne 'API_KEY') { Fail-Provision 'Protected runtime key reference must remain API_KEY' }

$secretPath = Join-Path $runtimeRoot 'secret-source.dpapi'
Assert-DpapiUnderRoot $secretPath $runtimeRoot 'Protected provider secret' | Out-Null
if (Test-Path -LiteralPath $secretPath) { Assert-DpapiNoReparse $secretPath 'Protected provider secret' | Out-Null }

$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$backupPath = "$runtimeFile.bak-$stamp"
Copy-Item -LiteralPath $runtimeFile -Destination $backupPath -Force
Set-DpapiProtectedAcl $backupPath $false 'Runtime timestamp backup'
Set-DpapiProtectedAcl $runtimeRoot $true 'Protected runtime directory'
Set-DpapiProtectedAcl $runtimeFile $false 'Protected runtime configuration'

try {
    Protect-DpapiSecretToFile $provider.API_KEY $secretPath $runtimeRoot | Out-Null
    $config | Add-Member -NotePropertyName LLM_SECRET_SOURCE_PATH -NotePropertyValue $secretPath -Force
    $json = $config | ConvertTo-Json -Depth 10
    if ($json.Contains($provider.API_KEY)) { Fail-Provision 'Protected runtime serialization would contain the raw provider secret' }
    $runtimeTmp = "$runtimeFile.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($runtimeTmp, $json, (New-Object Text.UTF8Encoding($false)))
        Set-DpapiProtectedAcl $runtimeTmp $false 'Protected runtime temporary file'
        Move-Item -LiteralPath $runtimeTmp -Destination $runtimeFile -Force
        Assert-DpapiProtectedAcl $runtimeFile $false 'Protected runtime configuration'
    } finally {
        if (Test-Path -LiteralPath $runtimeTmp -PathType Leaf) { Remove-Item -LiteralPath $runtimeTmp -Force -ErrorAction SilentlyContinue }
    }
} catch {
    throw $_
}

Assert-DpapiProtectedAcl $runtimeRoot $true 'Protected runtime directory'
Assert-DpapiProtectedAcl $runtimeFile $false 'Protected runtime configuration'
Assert-DpapiSecretFile $secretPath $runtimeRoot | Out-Null
$final = Get-Content -LiteralPath $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json
$finalSecret = $final.PSObject.Properties['LLM_SECRET_SOURCE_PATH']
if ($null -eq $finalSecret -or [string]$finalSecret.Value -ne $secretPath) { Fail-Provision 'Protected runtime secret source reference was not persisted' }
Write-Output ("PROVISIONED runtime_keys={0} secret_source={1} key_reference=API_KEY backup={2}" -f @($final.PSObject.Properties).Count, $secretPath, $backupPath)
