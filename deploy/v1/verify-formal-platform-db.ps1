[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$python = 'D:\Anaconda\envs\SFC\python.exe'
$formalRuntime = Join-Path (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform') 'priority-local') 'runtime.json'
. (Join-Path $PSScriptRoot 'dpapi-secret-source.ps1')

function Fail-ReadOnlyVerification([string]$Message) { throw $Message }

if (!(Test-Path -LiteralPath $formalRuntime -PathType Leaf) -or !(Test-Path -LiteralPath $python -PathType Leaf)) {
    Fail-ReadOnlyVerification 'Protected runtime or approved Python interpreter is missing'
}
$runtimeRoot = [IO.Path]::GetDirectoryName($formalRuntime)
Assert-DpapiNoReparse $runtimeRoot 'Formal runtime directory' | Out-Null
Assert-DpapiProtectedAcl $runtimeRoot $true 'Formal runtime directory' | Out-Null
Assert-DpapiNoReparse $formalRuntime 'Formal runtime configuration' | Out-Null
Assert-DpapiProtectedAcl $formalRuntime $false 'Formal runtime configuration' | Out-Null
try { $config = Get-Content -LiteralPath $formalRuntime -Raw -Encoding UTF8 | ConvertFrom-Json } catch {
    Fail-ReadOnlyVerification 'Formal protected runtime JSON is unreadable'
}

$property = $config.PSObject.Properties['SYNC_DATABASE_URL']
$url = if ($null -ne $property) { [string]$property.Value } else { '' }
$pattern = '^(mysql\+pymysql://[^/]+@127\.0\.0\.1:3306/)(?<schema>[^/?#]+)(?<query>\?.*)?$'
if ($url -notmatch $pattern -or $Matches.schema -cne 'chrom_platform') {
    Fail-ReadOnlyVerification 'Protected sync database binding is outside the audited formal schema'
}
[Environment]::SetEnvironmentVariable('DATABASE_URL', $url, 'Process')

$query = @'
import hashlib
import json
import os
import re
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

engine = None
try:
    engine = create_engine(os.environ["DATABASE_URL"], poolclass=NullPool, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        user, schema, server = connection.execute(text("SELECT CURRENT_USER(), DATABASE(), VERSION()")).one()
        if schema != "chrom_platform":
            raise RuntimeError("unexpected schema")
        objects = connection.execute(
            text("SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.tables WHERE table_schema=:schema ORDER BY TABLE_NAME"),
            {"schema": schema},
        ).all()
        columns = connection.execute(
            text("SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA "
                 "FROM information_schema.columns WHERE table_schema=:schema ORDER BY TABLE_NAME, ORDINAL_POSITION"),
            {"schema": schema},
        ).all()
        object_names = {name for name, kind in objects}
        revision = "unavailable"
        if "alembic_version" in object_names:
            revisions = connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars().all()
            revision = ",".join(revisions) if revisions else "base"
        signature_payload = {
            "objects": [list(row) for row in objects],
            "columns": [list(row) for row in columns],
        }
        signature = hashlib.sha256(json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        base_tables = sum(1 for _, kind in objects if kind == "BASE TABLE")
        views = sum(1 for _, kind in objects if kind == "VIEW")
        print("READ_ONLY_SQL=true")
        print("DATABASE_IDENTITY user=%s schema=%s server=%s" % (user, schema, server))
        print("FORMAL_SCHEMA revision=%s base_tables=%d views=%d columns=%d object_signature_sha256=%s" % (
            revision, base_tables, views, len(columns), signature))
except BaseException as exc:
    code = exc.args[0] if getattr(exc, "args", None) and isinstance(exc.args[0], int) else "none"
    print("FORMAL_DB_VERIFY_FAILURE class=%s driver_errno=%s" % (type(exc).__name__, code))
    raise SystemExit(4)
finally:
    if engine is not None:
        engine.dispose()
'@

try {
    & $python -B -c $query
    $verifyExit = $LASTEXITCODE
} catch {
    $verifyExit = 1
} finally {
    [Environment]::SetEnvironmentVariable('DATABASE_URL', $null, 'Process')
    $url = $null
    $config = $null
    $query = $null
}
if ($verifyExit -ne 0) { Fail-ReadOnlyVerification 'Formal database verification failed' }
Write-Output 'FORMAL_PLATFORM_DB_READONLY_VERIFY_OK'
