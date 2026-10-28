# Alembic M1 baseline

The migration is MySQL-only.  From `backend`, provide an explicit
`DATABASE_URL` (or set `sqlalchemy.url` in a local config) to generate offline
SQL:

```text
alembic -c alembic/alembic.ini upgrade 0001_m1_persistence --sql
```

SQLite is intentionally unsupported because it cannot represent the MySQL
locking, JSON and FULLTEXT semantics used by the platform.
