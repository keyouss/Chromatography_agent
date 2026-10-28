[CmdletBinding()]
param(
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$targetSchema = 'chrom_assistant_accept_20261006'
$projectId = 'priority-local'
$python = 'D:\Anaconda\envs\SFC\python.exe'
$formalRuntime = Join-Path (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform') 'priority-local') 'runtime.json'
$dpapiHelper = Join-Path $PSScriptRoot 'dpapi-secret-source.ps1'
$urlEnvironmentName = 'CHROM_ASSISTANT_PROJECT_VERIFY_URL'

foreach ($path in @($formalRuntime, $python, $dpapiHelper)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'A fixed read-only verifier input is missing' }
}

. $dpapiHelper
$formalRoot = [IO.Path]::GetDirectoryName($formalRuntime)
Assert-DpapiNoReparse $formalRoot 'Formal runtime directory' | Out-Null
Assert-DpapiProtectedAcl $formalRoot $true 'Formal runtime directory' | Out-Null
Assert-DpapiNoReparse $formalRuntime 'Formal runtime configuration' | Out-Null
Assert-DpapiProtectedAcl $formalRuntime $false 'Formal runtime configuration' | Out-Null
try { $config = Get-Content -LiteralPath $formalRuntime -Raw -Encoding UTF8 | ConvertFrom-Json } catch {
    throw 'Formal protected runtime JSON is unreadable'
}

$property = $config.PSObject.Properties['SYNC_DATABASE_URL']
$value = if ($null -ne $property) { [string]$property.Value } else { '' }
$pattern = '^(mysql\+pymysql://[^/]+@127\.0\.0\.1:3306/)(?<schema>[^/?#]+)(?<query>\?.*)?$'
if ($value -notmatch $pattern -or $Matches.schema -cne 'chrom_platform') {
    throw 'Protected sync database binding is outside the audited local formal schema'
}
$targetUrl = $Matches[1] + $targetSchema + $Matches.query

if (-not $Execute) {
    [pscustomobject]@{
        Status = 'PREFLIGHT_OK'
        TargetSchema = $targetSchema
        ProjectId = $projectId
        SqlPolicy = 'SELECT only'
        DatabaseConnectionOpened = $false
    } | ConvertTo-Json -Compress
    return
}

$query = @'
import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

schema = "chrom_assistant_accept_20261006"
project_id = "priority-local"
engine = None
try:
    engine = create_engine(
        os.environ["CHROM_ASSISTANT_PROJECT_VERIFY_URL"],
        poolclass=NullPool,
        connect_args={"connect_timeout": 5},
    )
    with engine.connect() as connection:
        user, current_schema = connection.execute(
            text("SELECT CURRENT_USER(), DATABASE()")
        ).one()
        if current_schema != schema:
            raise RuntimeError("unexpected schema")
        rows = connection.execute(
            text(
                "SELECT id, status, tenant_id, revision FROM project "
                "WHERE id=:project_id OR slug=:project_id ORDER BY id"
            ),
            {"project_id": project_id},
        ).mappings().all()
        audit_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM audit_event WHERE project_id=:project_id "
                "AND action='project.initialize_local' AND resource_id=:project_id "
                "AND result='SUCCEEDED'"
            ),
            {"project_id": project_id},
        ).scalar_one()
        valid = (
            len(rows) == 1
            and rows[0]["id"] == project_id
            and rows[0]["status"] == "ACTIVE"
            and rows[0]["tenant_id"] is None
            and rows[0]["revision"] == 1
            and audit_count == 1
        )
        print("READ_ONLY_SQL=true")
        print("DATABASE_IDENTITY schema=%s" % current_schema)
        if rows:
            row = rows[0]
            print(
                "PROJECT id=%s status=%s tenant_is_null=%s revision=%s audit_action_count=%d"
                % (row["id"], row["status"], str(row["tenant_id"] is None).lower(), row["revision"], audit_count)
            )
        else:
            print("PROJECT found=false audit_action_count=%d" % audit_count)
        if not valid:
            raise SystemExit(5)
except SystemExit:
    raise
except BaseException as exc:
    code = exc.args[0] if getattr(exc, "args", None) and isinstance(exc.args[0], int) else "none"
    print("READ_ONLY_VERIFY_FAILURE class=%s driver_errno=%s" % (type(exc).__name__, code))
    raise SystemExit(4)
finally:
    if engine is not None:
        engine.dispose()
'@

try {
    [Environment]::SetEnvironmentVariable($urlEnvironmentName, $targetUrl, 'Process')
    & $python -B -c $query
    $verifyExit = $LASTEXITCODE
} catch {
    $verifyExit = 1
} finally {
    [Environment]::SetEnvironmentVariable($urlEnvironmentName, $null, 'Process')
    Remove-Item -LiteralPath "Env:$urlEnvironmentName" -ErrorAction SilentlyContinue
    $targetUrl = $null
    $value = $null
    $config = $null
    $query = $null
}
if ($verifyExit -ne 0) { throw 'Read-only project bootstrap verification failed' }
Write-Output 'ASSISTANT_ACCEPTANCE_PROJECT_READONLY_VERIFY_OK'
