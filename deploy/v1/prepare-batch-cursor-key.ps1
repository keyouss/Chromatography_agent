[CmdletBinding()]
param([switch]$ShowPreparationCommands)

if (!$ShowPreparationCommands) {
    throw 'This preparation script only displays commands. No key or runtime configuration is created or changed.'
}

@'
PREPARATION ONLY. These commands are printed for review and are not executed by this script.

When separately authorized, create a 32-byte random key, encode it as unpadded base64url, and protect that text with the existing CurrentUser DPAPI helper under the selected protected runtime directory:

$bytes = New-Object byte[] 32
$rng = New-Object Security.Cryptography.RNGCryptoServiceProvider
$rng.GetBytes($bytes)
$rng.Dispose()
$keyText = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
[Array]::Clear($bytes, 0, $bytes.Length)
. 'D:\Jupyter\Chromatography_agent\deploy\v1\dpapi-secret-source.ps1'
$runtimeRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform\priority-local'
$keyPath = Join-Path $runtimeRoot 'batch-cursor-signing-key.dpapi'
Protect-DpapiSecretToFile $keyText $keyPath $runtimeRoot
$keyText = $null

Then, through the separately controlled protected-runtime configuration procedure, add only this non-secret reference:
CHROM_BATCH_CURSOR_KEY_SOURCE_PATH=<absolute path to batch-cursor-signing-key.dpapi>

Do not put CHROM_BATCH_CURSOR_SIGNING_KEY or the base64url key value in runtime.json, .env, source control, or logs. Formal and acceptance runtimes need references to their own protected DPAPI files.

Use one active key per deployment. Every API process must start from the same protected key reference and value. To rotate, stop all API processes for that deployment, replace the protected file through the approved procedure, then restart all API processes from that same reference. Do not replace the key while any API process is serving, and do not mix old-key and new-key processes. There is no hot reload or dual-key grace period; old cursors return 400 and clients must restart pagination. None of these rotation operations is performed by this preparation script.
'@
