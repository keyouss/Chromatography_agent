[CmdletBinding()]
param(
    [switch]$FrameworkStateOnly,
    [string]$MigrationConfigFile
)

$ErrorActionPreference = 'Stop'
$taskMigrationEnvNames = @('DATABASE_URL', 'SYNC_DATABASE_URL', 'CHROM_MIGRATION_EVIDENCE_DIR', 'CHROM_MIGRATION_FRAMEWORK_STATE_ONLY', 'CHROM_MIGRATION_BACKEND_DIR')
$taskMigrationPreviousEnv = @{}
foreach ($taskMigrationEnvName in $taskMigrationEnvNames) {
    $taskMigrationPreviousEnv[$taskMigrationEnvName] = [Environment]::GetEnvironmentVariable($taskMigrationEnvName, 'Process')
}
try {
$formalRuntime = Join-Path (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform') 'priority-local') 'runtime.json'
$targetSchema = 'chrom_assistant_accept_20261006'
$python = 'D:\Anaconda\envs\SFC\python.exe'
$backend = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\backend'))
$acceptanceRoot = Join-Path (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform') 'assistant-accept-20261006') ''
$evidenceRoot = Join-Path $acceptanceRoot 'evidence'
. (Join-Path $PSScriptRoot 'dpapi-secret-source.ps1')

$migrationConfigPolicy = @'
function Assert-MigrationConfigOption([bool]$FrameworkStateOnly, [bool]$PathWasBound) {
    if ($PathWasBound -and !$FrameworkStateOnly) {
        throw 'MigrationConfigFile is allowed only with FrameworkStateOnly'
    }
}

function Set-MigrationDatabaseBindings([string]$SyncUrl) {
    [Environment]::SetEnvironmentVariable('DATABASE_URL', $SyncUrl, 'Process')
    [Environment]::SetEnvironmentVariable('SYNC_DATABASE_URL', $SyncUrl, 'Process')
}

function Resolve-MigrationConfigSource([string]$Path, [string[]]$KnownAppRuntimePaths) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -notmatch '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+(?:\\|$))') {
        throw 'Migration config path must be absolute'
    }
    try { $fullPath = [IO.Path]::GetFullPath($Path) } catch { throw 'Migration config path is invalid' }
    if ([IO.Path]::GetExtension($fullPath) -ine '.json') { throw 'Migration config must be a JSON file' }
    foreach ($knownPath in $KnownAppRuntimePaths) {
        try { $knownFullPath = [IO.Path]::GetFullPath($knownPath) } catch { continue }
        if ([StringComparer]::OrdinalIgnoreCase.Equals($fullPath, $knownFullPath)) {
            throw 'Application runtime files cannot be migration-only sources'
        }
    }
    if (!(Test-Path -LiteralPath $fullPath -PathType Leaf)) { throw 'Migration config file is unavailable' }
    $parent = [IO.Path]::GetDirectoryName($fullPath)
    if ([string]::IsNullOrWhiteSpace($parent)) { throw 'Migration config parent directory is invalid' }
    $directory = $parent
    while (![string]::IsNullOrWhiteSpace($directory)) {
        Assert-DpapiNoReparse $directory 'Migration config directory' | Out-Null
        $nextDirectory = [IO.Path]::GetDirectoryName($directory)
        if ([string]::IsNullOrWhiteSpace($nextDirectory) -or $nextDirectory -eq $directory) { break }
        $directory = $nextDirectory
    }
    Assert-DpapiProtectedAcl $parent $true 'Migration config parent directory'
    Assert-DpapiNoReparse $fullPath 'Migration config file' | Out-Null
    Assert-DpapiProtectedAcl $fullPath $false 'Migration config file'
    try { $config = Get-Content -LiteralPath $fullPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'Migration config JSON is unreadable' }
    $url = [string]$config.SYNC_DATABASE_URL
    $urlPattern = '\Amysql\+pymysql://[^@/?#]+@127\.0\.0\.1:3306/chrom_assistant_accept_20261006(?:\?charset=utf8mb4)?\z'
    if ($url -cnotmatch $urlPattern) {
        throw 'Migration config must bind only the approved local acceptance schema'
    }
    return $url
}
'@
Invoke-Expression $migrationConfigPolicy

function Fail-Migration([string]$Message) { throw $Message }

Assert-MigrationConfigOption $FrameworkStateOnly $PSBoundParameters.ContainsKey('MigrationConfigFile')
if (!(Test-Path -LiteralPath $python -PathType Leaf)) { Fail-Migration 'Approved Python interpreter is missing' }
$useMigrationConfigFile = $PSBoundParameters.ContainsKey('MigrationConfigFile')
if (!$useMigrationConfigFile -and !(Test-Path -LiteralPath $formalRuntime -PathType Leaf)) { Fail-Migration 'Protected runtime is missing' }
$acceptanceRoot = [IO.Path]::GetFullPath($acceptanceRoot)
Assert-DpapiNoReparse $acceptanceRoot 'Acceptance runtime root' | Out-Null
Assert-DpapiProtectedAcl $acceptanceRoot $true 'Acceptance runtime root'
if (!(Test-Path -LiteralPath $evidenceRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $evidenceRoot | Out-Null
    Set-DpapiProtectedAcl $evidenceRoot $true 'Migration evidence directory'
}
Assert-DpapiNoReparse $evidenceRoot 'Migration evidence directory' | Out-Null
Assert-DpapiProtectedAcl $evidenceRoot $true 'Migration evidence directory'
$evidenceRoot = [IO.Path]::GetFullPath($evidenceRoot)
Assert-DpapiUnderRoot $evidenceRoot $acceptanceRoot 'Migration evidence directory'
[Environment]::SetEnvironmentVariable('CHROM_MIGRATION_EVIDENCE_DIR', $evidenceRoot, 'Process')
$frameworkStateOnlyValue = if ($FrameworkStateOnly) { '1' } else { '0' }
[Environment]::SetEnvironmentVariable('CHROM_MIGRATION_FRAMEWORK_STATE_ONLY', $frameworkStateOnlyValue, 'Process')
[Environment]::SetEnvironmentVariable('CHROM_MIGRATION_BACKEND_DIR', $backend, 'Process')
if ($useMigrationConfigFile) {
    $acceptanceRuntime = Join-Path $acceptanceRoot 'runtime.json'
    $migrationSyncUrl = Resolve-MigrationConfigSource $MigrationConfigFile @($formalRuntime, $acceptanceRuntime)
    Set-MigrationDatabaseBindings $migrationSyncUrl
} else {
    $runtimeRoot = [IO.Path]::GetDirectoryName($formalRuntime)
    Assert-DpapiNoReparse $runtimeRoot 'Formal runtime directory' | Out-Null
    Assert-DpapiProtectedAcl $runtimeRoot $true 'Formal runtime directory'
    Assert-DpapiNoReparse $formalRuntime 'Formal runtime configuration' | Out-Null
    Assert-DpapiProtectedAcl $formalRuntime $false 'Formal runtime configuration'
    try { $config = Get-Content -LiteralPath $formalRuntime -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Fail-Migration 'Formal protected runtime JSON is unreadable' }

    foreach ($spec in @(@{Key='DATABASE_URL';Driver='asyncmy'},@{Key='SYNC_DATABASE_URL';Driver='pymysql'})) {
        $value = [string]$config.PSObject.Properties[$spec.Key].Value
        $pattern = '^(mysql\+' + [regex]::Escape($spec.Driver) + '://[^/]+@127\.0\.0\.1:3306/)(?<schema>[^/?#]+)(?<query>\?.*)?$'
        if ($value -notmatch $pattern -or $Matches.schema -cne 'chrom_platform') { Fail-Migration 'Formal runtime database binding is not the audited local schema' }
        $target = $Matches[1] + $targetSchema + $Matches.query
        [Environment]::SetEnvironmentVariable($spec.Key, $target, 'Process')
    }
}

$preflight = @'
import os
from sqlalchemy import create_engine, text
framework_only = os.environ.get("CHROM_MIGRATION_FRAMEWORK_STATE_ONLY") == "1"
url = os.environ.get("SYNC_DATABASE_URL")
if not url:
    raise SystemExit(31)
engine = create_engine(url, connect_args={"connect_timeout": 5})
with engine.connect() as connection:
    user, schema, version = connection.execute(text("SELECT CURRENT_USER(), DATABASE(), VERSION()")).one()
    tables = connection.execute(text("SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=:schema ORDER BY TABLE_NAME"), {"schema": schema}).scalars().all()
    if framework_only:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        config = Config(os.path.join(os.environ["CHROM_MIGRATION_BACKEND_DIR"], "alembic", "alembic.ini"))
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()
        target_revision = "0021_assistant_framework_state"
        parent_revision = "0020_assistant_llm_configuration_lifecycle"
        target = script.get_revision(target_revision)
        versions = connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars().all() if "alembic_version" in tables else []
        framework_table_absent = "assistant_framework_state" not in tables
        repository_chain_ok = heads == [target_revision] and target is not None and target.down_revision == parent_revision
        baseline = "framework_state_parent" if versions == [parent_revision] and framework_table_absent else "unexpected"
        print("MIGRATION_PREFLIGHT user=%s schema=%s server=%s baseline=%s heads=%s" % (user, schema, version, baseline, ",".join(heads)))
        if (schema != "chrom_assistant_accept_20261006" or not repository_chain_ok
                or versions != [parent_revision] or not framework_table_absent):
            raise SystemExit(32)
    else:
        empty_schema = len(tables) == 0
        empty_version_baseline = False
        if tables == ["alembic_version"]:
            columns = connection.execute(text("SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE, COLUMN_KEY FROM information_schema.columns WHERE table_schema=:schema AND table_name='alembic_version' ORDER BY ORDINAL_POSITION"), {"schema": schema}).all()
            version_rows = connection.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar_one()
            empty_version_baseline = columns == [("version_num", "varchar", 128, "NO", "PRI")] and version_rows == 0
        accepted_baseline = empty_schema or empty_version_baseline
        baseline = "empty" if empty_schema else "empty_alembic_version" if empty_version_baseline else "unexpected"
        print("MIGRATION_PREFLIGHT user=%s schema=%s server=%s baseline=%s" % (user, schema, version, baseline))
        if schema != "chrom_assistant_accept_20261006" or not accepted_baseline:
            raise SystemExit(32)
engine.dispose()
'@
$preflightOutput = & $python -B -c $preflight 2>$null
if ($LASTEXITCODE -ne 0) {
    [Environment]::SetEnvironmentVariable('DATABASE_URL', $null, 'Process')
    [Environment]::SetEnvironmentVariable('SYNC_DATABASE_URL', $null, 'Process')
    [Environment]::SetEnvironmentVariable('CHROM_MIGRATION_FRAMEWORK_STATE_ONLY', $null, 'Process')
    [Environment]::SetEnvironmentVariable('CHROM_MIGRATION_BACKEND_DIR', $null, 'Process')
    Fail-Migration 'Target-schema preflight failed; preserve state and do not retry automatically'
}
$preflightOutput | Where-Object { $_ -match '^MIGRATION_PREFLIGHT ' }

$migrationOutput = $null
$migrationExit = 1
$cleanupFailed = $false
$migrationDatabaseUrl = [Environment]::GetEnvironmentVariable('SYNC_DATABASE_URL', 'Process')
if ([string]::IsNullOrWhiteSpace($migrationDatabaseUrl)) {
    [Environment]::SetEnvironmentVariable('DATABASE_URL', $null, 'Process')
    [Environment]::SetEnvironmentVariable('SYNC_DATABASE_URL', $null, 'Process')
    Fail-Migration 'Synchronous migration database binding is missing'
}
# Alembic env.py uses a synchronous SQLAlchemy engine; the application URL is asyncmy.
[Environment]::SetEnvironmentVariable('DATABASE_URL', $migrationDatabaseUrl, 'Process')
$runner = @'
import contextlib
import io
import os
import re
import sys
import traceback
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

backend_dir = Path(os.environ.get("CHROM_MIGRATION_BACKEND_DIR", Path.cwd())).resolve()
sys.path.insert(0, str(backend_dir / "src"))
framework_only = os.environ.get("CHROM_MIGRATION_FRAMEWORK_STATE_ONLY") == "1"
framework_target = "0021_assistant_framework_state"
framework_parent = "0020_assistant_llm_configuration_lifecycle"

captured = io.StringIO()
stage = "imports"
operation_category = "UNKNOWN"
expected_target_object = "unknown"
_SAFE_STATEMENT_CATEGORIES = {
    "CREATE_TABLE", "ALTER_TABLE", "DROP_TABLE", "INSERT", "UPDATE", "DELETE",
    "SELECT", "MYSQL_LOCK", "UNKNOWN",
}
_SAFE_TARGET_OBJECTS = {"assistant_framework_state", "alembic_version", "migration_schema", "unknown"}

def persist_report(payload):
    try:
        evidence = Path(os.environ["CHROM_MIGRATION_EVIDENCE_DIR"])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        report = evidence / f"alembic-migration-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
        temporary = report.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, report)
        return True
    except BaseException:
        return False

class SuccessDiagnosticFailure(Exception):
    pass

def _safe_exception_class(exc):
    name = type(exc).__name__
    return name if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) else "Exception"

def _safe_error_category(exc):
    name = _safe_exception_class(exc).lower()
    if name in {"operationalerror", "interfaceerror", "timeouterror"}:
        return "DBAPI_OPERATIONAL"
    if name in {"programmingerror", "syntaxerror"}:
        return "DBAPI_PROGRAMMING"
    if name == "integrityerror":
        return "DBAPI_INTEGRITY"
    if name in {"databaseerror", "dbapierror"}:
        return "DBAPI_ERROR"
    if name == "runtimeerror":
        return "RUNTIME_ERROR"
    return "OTHER"

def _safe_sqlstate(exc):
    for attr in ("sqlstate", "pgcode"):
        value = getattr(exc, attr, None)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]{5}", value):
            return value
    return "none"

def _safe_errno(exc):
    value = getattr(exc, "errno", None)
    if not isinstance(value, int) or isinstance(value, bool):
        args = getattr(exc, "args", ())
        value = args[0] if isinstance(args, tuple) and args else None
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 999999 else None

def _safe_error_details(exc):
    chain = []
    pending = [exc]
    seen = set()
    while pending and len(chain) < 8:
        item = pending.pop(0)
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        chain.append({
            "exception_class": _safe_exception_class(item),
            "category": _safe_error_category(item),
            "sqlstate": _safe_sqlstate(item),
            "driver_errno": _safe_errno(item),
        })
        pending.extend(linked for linked in (
            getattr(item, "orig", None), getattr(item, "__cause__", None), getattr(item, "__context__", None)
        ) if linked is not None)
    if not chain:
        chain = [{"exception_class": "Exception", "category": "OTHER", "sqlstate": "none", "driver_errno": None}]
    sqlstate = next((item["sqlstate"] for item in chain if item["sqlstate"] != "none"), "none")
    driver_errno = next((item["driver_errno"] for item in chain if item["driver_errno"] is not None), None)
    return {
        "exception_class": chain[0]["exception_class"],
        "error_category": chain[0]["category"],
        "sqlstate": sqlstate,
        "driver_errno": str(driver_errno) if driver_errno is not None else "none",
        "exception_chain": chain,
    }

def _safe_statement_details(exc):
    pending = [exc]
    seen = set()
    while pending and len(seen) < 8:
        item = pending.pop(0)
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        statement = getattr(item, "statement", None)
        if isinstance(statement, str):
            if re.search(r"\bGET_LOCK\s*\(", statement, re.I):
                return "MYSQL_LOCK", "unknown"
            match = re.match(
                r"\s*(CREATE\s+(?:TEMPORARY\s+)?TABLE|ALTER\s+TABLE|DROP\s+TABLE|INSERT\s+INTO|UPDATE|DELETE\s+FROM|SELECT)\b",
                statement, re.I,
            )
            if match:
                verb = re.sub(r"\s+", "_", match.group(1).upper())
                category = {
                    "CREATE_TABLE": "CREATE_TABLE", "CREATE_TEMPORARY_TABLE": "CREATE_TABLE",
                    "ALTER_TABLE": "ALTER_TABLE", "DROP_TABLE": "DROP_TABLE",
                    "INSERT_INTO": "INSERT", "UPDATE": "UPDATE", "DELETE_FROM": "DELETE",
                    "SELECT": "SELECT",
                }.get(verb, "UNKNOWN")
                table_match = re.match(
                    r"\s*(?:CREATE\s+(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?|"
                    r"ALTER\s+TABLE\s+|DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?|INSERT\s+INTO\s+|"
                    r"UPDATE\s+|DELETE\s+FROM\s+|SELECT\b.*?\bFROM\s+)"
                    r"((?:`[^`]+`|\"[^\"]+\"|[A-Za-z0-9_$]+)(?:\s*\.\s*(?:`[^`]+`|\"[^\"]+\"|[A-Za-z0-9_$]+))?)",
                    statement, re.I | re.S,
                )
                target = "unknown"
                if table_match:
                    identifier = table_match.group(1).split(".")[-1].strip().strip("`").strip('"')
                    if identifier in _SAFE_TARGET_OBJECTS:
                        target = identifier
                return (category if category in _SAFE_STATEMENT_CATEGORIES else "UNKNOWN"), target
        pending.extend(linked for linked in (
            getattr(item, "orig", None), getattr(item, "__cause__", None), getattr(item, "__context__", None)
        ) if linked is not None)
    return "UNKNOWN", "unknown"

try:
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    stage = "upgrade"
    config = Config(str(backend_dir / "alembic" / "alembic.ini"))
    target_revision = "head"
    if framework_only:
        script = ScriptDirectory.from_config(config)
        target = script.get_revision(framework_target)
        if script.get_heads() != [framework_target] or target is None or target.down_revision != framework_parent:
            raise RuntimeError("Unexpected Alembic framework-state head")
        target_revision = framework_target
    operation_category = "ALEMBIC_UPGRADE"
    expected_target_object = "assistant_framework_state" if framework_only else "migration_schema"
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        command.upgrade(config, target_revision)
    from sqlalchemy import create_engine, text
    stage = "postcheck"
    operation_category = "POSTCHECK_VERSION"
    expected_target_object = "alembic_version"
    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        applied_versions = connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars().all()
        applied_count = str(len(applied_versions))
        if framework_only:
            required_columns = {
                "agent_run_id", "project_id", "conversation_id", "engine", "framework_version",
                "serialization_version", "config_hash", "state", "stream_message_id", "created_at", "updated_at",
            }
            operation_category = "POSTCHECK_COLUMNS"
            expected_target_object = "assistant_framework_state"
            columns = set(connection.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name='assistant_framework_state'"
            )).scalars().all())
            operation_category = "POSTCHECK_CONSTRAINTS"
            expected_target_object = "assistant_framework_state"
            constraints = dict(connection.execute(text(
                "SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE FROM information_schema.table_constraints "
                "WHERE table_schema=DATABASE() AND table_name='assistant_framework_state'"
            )).all())
            # Match env.py's Base.metadata naming convention. MySQL reports a primary key as PRIMARY;
            # SQLAlchemy prefixes CHECK names with ck_<table>_ and truncates the long hash-suffixed
            # config-hash name to the MySQL 64-character identifier limit.
            required_constraints = {
                "PRIMARY": "PRIMARY KEY",
                "fk_assistant_framework_state_agent_run_id_agent_run": "FOREIGN KEY",
                "fk_assistant_framework_state_project_id_project": "FOREIGN KEY",
                "fk_assistant_framework_state_conversation_id_conversation": "FOREIGN KEY",
                "fk_assistant_framework_state_stream_message_id_message": "FOREIGN KEY",
                "ck_assistant_framework_state_ck_assistant_framework_state_engine": "CHECK",
                "ck_assistant_framework_state_ck_assistant_framework_stat_8deb": "CHECK",
            }
            if applied_versions != [framework_target] or not required_columns.issubset(columns):
                operation_category = "POSTCHECK_VALIDATION"
                raise RuntimeError("Framework-state migration postcondition failed")
            if any(constraints.get(name) != kind for name, kind in required_constraints.items()):
                operation_category = "POSTCHECK_VALIDATION"
                raise RuntimeError("Framework-state migration constraints are incomplete")
    engine.dispose()
    payload = {"phase": stage, "alembic_status": "success", "alembic_exit_code": 0, "wrapper_exit_code": 0, "exception_class": None, "sqlstate": None, "driver_errno": None, "repository_frame": None, "applied_version_count": applied_count, "applied_revision": applied_versions[0] if len(applied_versions) == 1 else "multiple"}
    persisted = persist_report(payload)
    if persisted:
        print("ALEMBIC_UPGRADE_OK wrapper_exit_code=0 alembic_exit_code=0 evidence_persisted=true")
    else:
        print("ALEMBIC_RECEIPT_FAILURE code=WRITE_FAILED")
        print("ALEMBIC_UPGRADE_SUCCESS_DIAGNOSTIC_FAILURE wrapper_exit_code=4 alembic_exit_code=0 evidence_persisted=false receipt_status=WRITE_FAILED")
        raise SuccessDiagnosticFailure()
except SuccessDiagnosticFailure:
    raise SystemExit(4)
except BaseException as exc:
    error_details = _safe_error_details(exc)
    failed_statement_category, target_object = _safe_statement_details(exc)
    repo = Path.cwd().resolve()
    local_frame = "none"
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        try:
            candidate = Path(frame.filename).resolve()
            if candidate.is_relative_to(repo):
                local_frame = f"{candidate.name}:{frame.lineno}"
                break
        except (OSError, ValueError):
            continue
    applied_count = "unknown"
    diagnostic_failure = None
    diagnostic_operation_category = "DIAGNOSTIC_VERSION_TABLE"
    diagnostic_expected_target_object = "alembic_version"
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 5})
        with engine.connect() as connection:
            diagnostic_operation_category = "DIAGNOSTIC_VERSION_TABLE"
            diagnostic_expected_target_object = "alembic_version"
            exists = connection.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name='alembic_version'"
            )).scalar_one()
            if exists:
                diagnostic_operation_category = "DIAGNOSTIC_VERSION_COUNT"
                diagnostic_expected_target_object = "alembic_version"
                applied_count = str(connection.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar_one())
        engine.dispose()
    except BaseException as diagnostic_exc:
        applied_count = "unavailable"
        diagnostic_failure = _safe_error_details(diagnostic_exc)
    payload = {
        "phase": stage,
        "alembic_status": "exception",
        "alembic_exit_code": None,
        "wrapper_exit_code": 3,
        **error_details,
        "failed_statement_category": failed_statement_category,
        "target_object": target_object,
        "operation_category": operation_category,
        "expected_target_object": expected_target_object,
        "diagnostic_status": "failed" if diagnostic_failure is not None else "complete",
        "diagnostic_operation_category": diagnostic_operation_category,
        "diagnostic_expected_target_object": diagnostic_expected_target_object,
        "diagnostic_exception": diagnostic_failure,
        "repository_frame": local_frame,
        "applied_version_count": applied_count,
        "process_exit_code": 3,
    }
    persisted = persist_report(payload)
    if not persisted:
        print("ALEMBIC_RECEIPT_FAILURE code=WRITE_FAILED")
    print(
        f"ALEMBIC_UPGRADE_FAILURE stage={stage} wrapper_exit_code=3 alembic_exit_code=unavailable"
        f" exception_class={error_details['exception_class']} sqlstate={error_details['sqlstate']} driver_errno={error_details['driver_errno']}"
        f" failed_statement_category={failed_statement_category} target_object={target_object}"
        f" diagnostic_status={'failed' if diagnostic_failure is not None else 'complete'}"
        f" repository_frame={local_frame} applied_version_count={applied_count}"
        f" evidence_persisted={str(persisted).lower()} receipt_status={'WRITTEN' if persisted else 'WRITE_FAILED'}"
    )
    raise SystemExit(3)
'@
Push-Location $backend
try {
    $migrationOutput = & $python -B -c $runner 2>&1
    $migrationExit = $LASTEXITCODE
} catch {
    $migrationExit = 1
} finally {
    try { Pop-Location } catch { $cleanupFailed = $true }
    if ($migrationExit -ne 0 -and $null -ne $migrationOutput) {
        $migrationOutput | Where-Object { $_ -match '^ALEMBIC_(?:RECEIPT_FAILURE code=WRITE_FAILED|UPGRADE_FAILURE stage=[A-Za-z0-9_]+ wrapper_exit_code=|UPGRADE_SUCCESS_DIAGNOSTIC_FAILURE wrapper_exit_code=)' }
    }
    try { [Environment]::SetEnvironmentVariable('DATABASE_URL', $null, 'Process') } catch { $cleanupFailed = $true }
    try { [Environment]::SetEnvironmentVariable('SYNC_DATABASE_URL', $null, 'Process') } catch { $cleanupFailed = $true }
    try { [Environment]::SetEnvironmentVariable('CHROM_MIGRATION_EVIDENCE_DIR', $null, 'Process') } catch { $cleanupFailed = $true }
    try { [Environment]::SetEnvironmentVariable('CHROM_MIGRATION_FRAMEWORK_STATE_ONLY', $null, 'Process') } catch { $cleanupFailed = $true }
    try { [Environment]::SetEnvironmentVariable('CHROM_MIGRATION_BACKEND_DIR', $null, 'Process') } catch { $cleanupFailed = $true }
    $migrationDatabaseUrl = $null
    $config = $null
    $migrationOutput = $null
}
if ($migrationExit -ne 0) { Fail-Migration 'Alembic failed; preserve schema and migration scene, do not rerun or clean automatically' }
if ($cleanupFailed) { Fail-Migration 'Migration completed but process cleanup could not be confirmed' }
if ($FrameworkStateOnly) {
    Write-Output ('MIGRATION_COMPLETED schema={0} target=0021' -f $targetSchema)
} else {
    Write-Output ('MIGRATION_COMPLETED schema={0} target=0020' -f $targetSchema)
}

} finally {
    foreach ($taskMigrationEnvName in $taskMigrationEnvNames) {
        [Environment]::SetEnvironmentVariable($taskMigrationEnvName, $taskMigrationPreviousEnv[$taskMigrationEnvName], 'Process')
    }
}
