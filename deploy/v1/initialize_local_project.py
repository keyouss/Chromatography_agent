"""Idempotently initialize the explicitly selected local acceptance project."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src"))

from sqlalchemy import func, or_, select, text
from sqlalchemy.engine import URL, make_url
from chrom_platform.infrastructure.db import create_database_engine
from chrom_platform.infrastructure.models import AuditEvent, Project
from sqlalchemy.orm import Session


ACCEPTANCE_SCHEMA = "chrom_assistant_accept_20261006"
FORMAL_SCHEMA = "chrom_platform"
ACCEPTANCE_SYNC_URL_ENV = "CHROM_ACCEPTANCE_FORMAL_SYNC_URL"


def _initialize_url(database_url: str | URL, *, expected_database: str | None = None) -> tuple[str, int]:
    project_id, slug, name = "priority-local", "priority-local", "本机色谱项目"
    url = make_url(database_url) if isinstance(database_url, str) else database_url
    if url.drivername != "mysql+pymysql":
        raise ValueError("Explicit synchronous MySQL configuration required")
    engine = create_database_engine(url)
    created = False
    audit_count = 0
    try:
        with Session(engine) as session, session.begin():
            database = session.execute(text("SELECT DATABASE()")).scalar_one_or_none()
            if database != (expected_database or url.database):
                raise RuntimeError("Connected database does not match the fixed project bootstrap target")
            rows = session.scalars(select(Project).where(or_(Project.id == project_id, Project.slug == slug))).all()
            if rows:
                if len(rows) != 1 or (rows[0].id, rows[0].slug, rows[0].name, rows[0].status, rows[0].tenant_id) != (project_id, slug, name, "ACTIVE", None):
                    raise ValueError("Existing local project conflicts with bootstrap configuration")
            else:
                session.add(Project(id=project_id, slug=slug, name=name, status="ACTIVE", revision=1))
                session.flush()
                session.add(AuditEvent(
                    id="audit_" + uuid.uuid4().hex, project_id=project_id,
                    actor_id="deployment-default", auth_method="deployment_default",
                    interaction="service", request_id="bootstrap_" + uuid.uuid4().hex,
                    action="project.initialize_local", resource_type="project", resource_id=project_id,
                    resource_ref={"type": "project", "id": project_id}, before_summary={},
                    after_summary={"name": name, "status": "ACTIVE", "revision": 1}, result="SUCCEEDED",
                ))
                created = True
        with Session(engine) as session:
            database = session.execute(text("SELECT DATABASE()")).scalar_one_or_none()
            if database != (expected_database or url.database):
                raise RuntimeError("Post-commit database verification failed")
            project = session.get(Project, project_id)
            if project is None or project.status != "ACTIVE":
                raise RuntimeError("Committed project verification failed")
            audit_count = int(session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.project_id == project_id,
                AuditEvent.action == "project.initialize_local",
                AuditEvent.resource_id == project_id,
                AuditEvent.result == "SUCCEEDED",
            )) or 0)
    finally:
        engine.dispose()
    return ("created" if created else "existing"), audit_count


def initialize(config_path: Path) -> tuple[str, int]:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    return _initialize_url(config["SYNC_DATABASE_URL"])


def acceptance_database_url(raw_url: str) -> URL:
    if not raw_url:
        raise ValueError("Protected acceptance bootstrap input is unavailable")
    source_url = make_url(raw_url)
    if source_url.drivername != "mysql+pymysql":
        raise ValueError("Explicit synchronous MySQL configuration required")
    source_port = source_url.port if source_url.port is not None else 3306
    if source_url.host != "127.0.0.1" or source_port != 3306:
        raise ValueError("Acceptance bootstrap is restricted to the configured local MySQL endpoint")
    if source_url.database != FORMAL_SCHEMA:
        raise ValueError("Formal runtime database binding is not the expected source")
    target_url = source_url.set(database=ACCEPTANCE_SCHEMA)
    if target_url.database != ACCEPTANCE_SCHEMA:
        raise ValueError("Acceptance schema binding could not be fixed")
    return target_url


def initialize_acceptance() -> tuple[str, int]:
    raw_url = os.environ.get(ACCEPTANCE_SYNC_URL_ENV, "")
    target_url = acceptance_database_url(raw_url)
    return _initialize_url(target_url, expected_database=ACCEPTANCE_SCHEMA)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config", type=Path)
    mode.add_argument("--acceptance", action="store_true")
    args = parser.parse_args()
    try:
        status, audit_count = initialize_acceptance() if args.acceptance else initialize(args.config)
    except Exception:
        # SQLAlchemy exceptions can contain parameters or credentials.
        print("LOCAL_PROJECT_INITIALIZATION_FAILED; protected configuration and database require inspection", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({
        "database": ACCEPTANCE_SCHEMA if args.acceptance else "configured",
        "project_id": "priority-local",
        "status": status,
        "bootstrap_audit_count": audit_count,
    }, ensure_ascii=False))
