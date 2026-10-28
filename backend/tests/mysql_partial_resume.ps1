#!/usr/bin/env pwsh
# Proves the MySQL 0009 -> 0010 partial-DDL retry path in a disposable database.
# The script reads DATABASE_URL only in-process and never prints its value.

$ErrorActionPreference = 'Stop'
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$container = if ($env:MYSQL_MIGRATION_CONTAINER) { $env:MYSQL_MIGRATION_CONTAINER } else { 'chrom-platform-v1-mysql-1' }
$baseUrl = $env:DATABASE_URL
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
    throw 'DATABASE_URL is required; the value is never printed.'
}

$suffix = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$database = "chrom_migration_partial_$suffix"
$containerEnv = & $docker inspect $container --format '{{range .Config.Env}}{{println .}}{{end}}'
$appUser = (($containerEnv | Where-Object { $_ -like 'MYSQL_USER=*' }) -split '=', 2)[1]
if ([string]::IsNullOrWhiteSpace($appUser)) {
    throw 'MYSQL_USER is unavailable in the existing secure container configuration.'
}
$sqlUser = $appUser.Replace("'", "''")
$uri = [UriBuilder]$baseUrl
$uri.Path = "/$database"
$isolatedUrl = $uri.Uri.AbsoluteUri
$oldUrl = $env:DATABASE_URL
$oldPythonPath = $env:PYTHONPATH

function Invoke-MySql([string]$sql) {
    $result = $sql | & $docker exec -i $container sh -lc 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --batch --raw' 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "isolated MySQL command failed with exit code $LASTEXITCODE"
    }
    return ($result -join "`n")
}

function Invoke-Alembic([string]$revision) {
    $output = & 'D:\Anaconda\envs\SFC\python.exe' -m alembic -c alembic/alembic.ini upgrade $revision 2>&1
    $code = $LASTEXITCODE
    if ($env:MYSQL_MIGRATION_DEBUG -eq '1' -and $output) {
        $output | ForEach-Object {
            Write-Host (($_ -replace 'mysql\+[^:]+://[^ ]+', 'mysql://[REDACTED]'))
        }
    }
    return $code
}

try {
    Invoke-MySql "CREATE DATABASE ``$database``"
    Invoke-MySql "GRANT ALL PRIVILEGES ON ``$database``.* TO '$sqlUser'@'%'"
    $env:DATABASE_URL = $isolatedUrl
    $env:PYTHONPATH = (Join-Path (Get-Location) 'src')

    if ((Invoke-Alembic '0009_training_outbox_lease') -ne 0) {
        throw 'isolated upgrade to 0009 failed'
    }
    $revision = Invoke-MySql "SELECT version_num FROM ``$database``.alembic_version"
    if ($revision.Trim() -notmatch '0009_training_outbox_lease') {
        throw 'isolated database did not reach 0009'
    }

    # Simulate non-transactional 0010 DDL: registration is valid partial state,
    # evaluation has a conflicting dispatch_id definition that must fail closed.
    Invoke-MySql "ALTER TABLE ``$database``.registration ADD COLUMN attempt INT NOT NULL DEFAULT 1, ADD COLUMN dispatch_id VARCHAR(96) NULL"
    Invoke-MySql "ALTER TABLE ``$database``.evaluation ADD COLUMN attempt INT NOT NULL DEFAULT 1, ADD COLUMN dispatch_id VARCHAR(32) NULL"
    if ((Invoke-Alembic 'head') -eq 0) {
        throw 'first upgrade unexpectedly succeeded with an incompatible partial definition'
    }
    $revision = Invoke-MySql "SELECT version_num FROM ``$database``.alembic_version"
    if ($revision.Trim() -notmatch '0009_training_outbox_lease') {
        throw 'failed first upgrade changed the Alembic revision'
    }

    Invoke-MySql "ALTER TABLE ``$database``.evaluation MODIFY COLUMN dispatch_id VARCHAR(96) NULL"
    if ((Invoke-Alembic 'head') -ne 0) {
        throw 'second upgrade did not resume successfully'
    }
    $revision = Invoke-MySql "SELECT version_num FROM ``$database``.alembic_version"
    if ($revision.Trim() -notmatch '0010_model_center_job_fencing') {
        throw 'isolated database did not reach 0010'
    }
    $fencingColumns = Invoke-MySql @"
SELECT COUNT(*) FROM information_schema.columns
WHERE table_schema='$database' AND table_name IN ('registration','evaluation','deployment')
  AND column_name IN ('attempt','dispatch_id');
"@
    if ([int]($fencingColumns.Trim().Split("`n")[-1]) -ne 6) {
        throw 'isolated database is missing fencing columns after retry'
    }
    Write-Output 'mysql partial-resume isolation: PASS'
}
finally {
    $cleanupError = $null
    try {
        # Revoke the exact temporary grant before dropping the exact database.
        Invoke-MySql "REVOKE ALL PRIVILEGES ON ``$database``.* FROM '$sqlUser'@'%'"
        Invoke-MySql 'FLUSH PRIVILEGES'
        Invoke-MySql "DROP DATABASE IF EXISTS ``$database``"
        Invoke-MySql 'FLUSH PRIVILEGES'
        $remaining = Invoke-MySql "SELECT COUNT(*) FROM mysql.db WHERE Db = '$($database.Replace("'", "''"))'"
        if ([int]($remaining.Trim().Split("`n")[-1]) -ne 0) {
            throw 'temporary database grant residue remains after cleanup'
        }
    } catch {
        $cleanupError = $_
    } finally {
        $env:DATABASE_URL = $oldUrl
        $env:PYTHONPATH = $oldPythonPath
    }
    if ($null -ne $cleanupError) {
        throw "isolated cleanup failed: $cleanupError"
    }
}
