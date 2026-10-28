[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$targetSchema = 'chrom_assistant_accept_20261006'
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
$value = if ($null -ne $property) { [string]$property.Value } else { '' }
$pattern = '^(mysql\+pymysql://[^/]+@127\.0\.0\.1:3306/)(?<schema>[^/?#]+)(?<query>\?.*)?$'
if ($value -notmatch $pattern -or $Matches.schema -cne 'chrom_platform') {
    Fail-ReadOnlyVerification 'Protected sync database binding is outside the audited local schema'
}
$syncUrl = $Matches[1] + $targetSchema + $Matches.query
[Environment]::SetEnvironmentVariable('DATABASE_URL', $syncUrl, 'Process')

$query = @'
import os
import re
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

schema = "chrom_assistant_accept_20261006"
engine = None
try:
    engine = create_engine(os.environ["DATABASE_URL"], poolclass=NullPool, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        user, current_schema, server = connection.execute(
            text("SELECT CURRENT_USER(), DATABASE(), VERSION()")
        ).one()
        if current_schema != schema:
            raise RuntimeError("unexpected schema")
        tables = connection.execute(
            text("SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=:schema ORDER BY TABLE_NAME"),
            {"schema": schema},
        ).scalars().all()
        revisions = connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars().all()
        required = {
            "agent_run": {"provider_configuration_hash"},
            "llm_configuration": {"last_test_id"},
            "llm_test_result": {"configuration_hash", "job_id"},
            "llm_configuration_active": {"project_id", "configuration_id", "configuration_revision", "revision", "etag", "updated_at"},
        }
        missing = []
        for table, names in required.items():
            columns = set(connection.execute(
                text("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=:schema AND table_name=:table"),
                {"schema": schema, "table": table},
            ).scalars().all())
            if not names.issubset(columns):
                missing.append(table)
        grants = connection.exec_driver_sql("SHOW GRANTS").scalars().all()
        target_privileges = []
        target_scope_count = 0
        global_non_usage = False
        for grant in grants:
            upper = grant.upper()
            if "ON *.*" in upper and "USAGE" not in upper:
                global_non_usage = True
            if schema in grant and "ON " in upper:
                target_scope_count += 1
                match = re.search(r"GRANT\s+(.+?)\s+ON\s+", grant, re.I)
                if match:
                    target_privileges.extend(item.strip().upper() for item in match.group(1).split(","))
        exact_crud = set(target_privileges) == {"SELECT", "INSERT", "UPDATE", "DELETE"} and target_scope_count == 1
        print("READ_ONLY_SQL=true")
        print("DATABASE_IDENTITY user=%s schema=%s server=%s" % (user, current_schema, server))
        print("MIGRATION revision=%s table_count=%d required_columns_ok=%s" % (
            ",".join(revisions), len(tables), str(not missing).lower()))
        print("RUNTIME_PRIVILEGES exact_target_crud=%s global_non_usage=%s privileges=%s" % (
            str(exact_crud).lower(), str(global_non_usage).lower(), ",".join(sorted(set(target_privileges)))))
    if missing:
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
    & $python -B -c $query
    $verifyExit = $LASTEXITCODE
} catch {
    $verifyExit = 1
} finally {
    [Environment]::SetEnvironmentVariable('DATABASE_URL', $null, 'Process')
    $syncUrl = $null
    $value = $null
    $config = $null
    $query = $null
}
if ($verifyExit -ne 0) { Fail-ReadOnlyVerification 'Read-only acceptance database verification failed' }
Write-Output 'ASSISTANT_ACCEPTANCE_DB_READONLY_VERIFY_OK'
