from __future__ import annotations

from datetime import datetime, timezone

import pytest

from chrom_platform.artifact_core.models import Structure
from chrom_platform.core.idempotency import IdempotencyScope
from chrom_platform.infrastructure.repositories import SQLAlchemyIdempotencyStore, SQLAlchemyStructureRepository
from chrom_platform.infrastructure.structure_uow import SQLAlchemyStructureUnitOfWork
from chrom_platform.structure_core.schemas import OriginalInput, StructureView


class _Nested:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        self.session.nested_enters += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.session.nested_exits += 1
        return False


class _Session:
    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.nested_enters = 0
        self.nested_exits = 0

    def add(self, row):
        self.added.append(row)
        if self.row is None:
            self.row = row

    def flush(self):
        return None

    def scalar(self, _statement):
        return self.row

    def begin_nested(self):
        return _Nested(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _scope() -> IdempotencyScope:
    return IdempotencyScope("actor-1", "project-1", "POST", "/api/v1/structures/normalize", "k-1")


def test_bound_idempotency_store_never_commits_or_closes_caller_session() -> None:
    session = _Session()
    store = SQLAlchemyIdempotencyStore(session)

    acquired = store.begin(_scope(), "a" * 64)
    store.complete(_scope(), "a" * 64, 201, {"id": "str-1"})

    assert acquired.acquired is True
    assert session.commits == 0
    assert session.rollbacks == 0
    assert session.closed is False
    assert session.nested_enters == 1
    assert session.nested_exits == 1
    assert session.row.status == "COMPLETED"


def test_bound_structure_repository_dedupes_project_snapshot_without_commit() -> None:
    row = Structure(
        id="str-existing",
        project_id="project-1",
        schema_version="1.0",
        original_input={"input_type": "smiles", "value": "CCO", "source_artifact_id": None},
        canonical_smiles="CCO",
        is_valid=True,
        has_stereochemistry=False,
        stereochemistry_status="not_applicable",
        stereochemistry_warnings=[],
        snapshot_hash="a" * 64,
        source_artifact_id=None,
        created_at=datetime.now(timezone.utc),
    )
    session = _Session(row)
    incoming = StructureView(
        id="str-new",
        project_id="project-1",
        schema_version="1.0",
        original_input=OriginalInput(input_type="smiles", value="CCO"),
        canonical_smiles="CCO",
        is_valid=True,
        has_stereochemistry=False,
        stereochemistry_status="not_applicable",
        stereochemistry_warnings=[],
        snapshot_hash="a" * 64,
        created_at=datetime.now(timezone.utc),
    )

    result = SQLAlchemyStructureRepository(session).save(incoming)

    assert result.id == "str-existing"
    assert session.added == []
    assert session.commits == 0
    assert session.closed is False


def test_structure_repository_rejects_factory_misuse() -> None:
    with pytest.raises(TypeError, match="caller-owned Session"):
        SQLAlchemyStructureRepository(lambda: _Session())  # type: ignore[arg-type]


class _Transaction:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.rolled_back = exc_type is not None
        self.committed = exc_type is None
        return False


class _UowSession(_Session):
    def __init__(self):
        super().__init__()
        self.transaction = _Transaction()

    def begin(self):
        return self.transaction


def test_structure_uow_owns_outer_commit_and_rolls_back_on_failure() -> None:
    session = _UowSession()
    uow = SQLAlchemyStructureUnitOfWork(lambda: session)

    with uow.transaction() as transaction:
        assert transaction.structures is not None
        assert transaction.idempotency is not None

    assert session.transaction.committed is True
    assert session.closed is True

    failed_session = _UowSession()
    failed_uow = SQLAlchemyStructureUnitOfWork(lambda: failed_session)
    try:
        with failed_uow.transaction():
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass
    assert failed_session.transaction.committed is False
    assert failed_session.transaction.rolled_back is True
    assert failed_session.closed is True
