from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent
from chrom_platform.modules.data_center.persistence import PredictionRecordRow
from chrom_platform.modules.prediction.persistence import (
    PredictionExecutionSnapshot,
    PredictionRow,
    SQLAlchemyPredictionPersistence,
)
from chrom_platform.modules.prediction.production_worker import AsyncPredictionExecutionStore, _etag
from chrom_platform.modules.prediction.schemas import PublicErrorSummary
from chrom_platform.task_core.state import JobStatus


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def first(self):
        return None


class _AsyncSession:
    def __init__(self, rows):
        self.rows = rows
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return self

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is JobEvent.seq:
            return _Result(None)
        return _Result(self.rows.get(entity))

    def add(self, value):
        self.added.append(value)


class _SyncSession:
    def __init__(self, rows):
        self.rows = rows
        self.added = []

    def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is JobEvent.seq:
            return None
        return self.rows.get(entity)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        pass


class _RecordWriter:
    def update_prediction_record(self, *_args, **_kwargs):
        pass


def _rows(*, job_attempt=1, lease_until=None):
    return {
        Job: SimpleNamespace(
            id="job-1", project_id="project-1", attempt=job_attempt, status="RUNNING",
            progress={"completed": 0, "total": 1, "unit": "prediction", "stage": "queued"},
            result_ref=None, error=None, finished_at=None, updated_at=None, revision=1, etag="",
        ),
        JobAttempt: SimpleNamespace(
            job_id="job-1", attempt=1, status="RUNNING", execution_token="exec-1",
            fencing_token=1, lease_until=lease_until, result_ref=None, error=None, finished_at=None,
        ),
        PredictionRow: SimpleNamespace(
            id="prediction-1", prediction_record_id="record-1", status="RUNNING", result=None,
            error=None, completed_at=None, updated_at=None, revision=1, etag="",
        ),
        PredictionRecordRow: SimpleNamespace(
            id="record-1", status="RUNNING", result_ref=None, revision=1, etag="",
        ),
    }


def _snapshot() -> PredictionExecutionSnapshot:
    return PredictionExecutionSnapshot(
        prediction_id="prediction-1", project_id="project-1", job_id="job-1", attempt=1,
        status=JobStatus.RUNNING, type_id="tlc", tool="single", schema_version="1.0",
        request=None, model_snapshot=None, input_snapshot=None, dispatch_id="dispatch-1",
        execution_token="exec-1", fencing_token=1,
    )


@pytest.mark.parametrize("failure", [False, True])
def test_async_terminal_prediction_finishes_one_progress_unit(failure):
    rows = _rows()
    session = _AsyncSession(rows)
    store = AsyncPredictionExecutionStore(lambda: session)
    error = PublicErrorSummary(code="FAILED", message="safe failure") if failure else None

    assert asyncio.run(store.finish(_snapshot(), result=None if failure else {"ok": True}, error=error)) is True
    assert rows[Job].progress == {"completed": 1, "total": 1, "unit": "prediction", "stage": "finished"}
    assert rows[Job].etag


@pytest.mark.parametrize("failure", [False, True])
def test_sync_terminal_prediction_finishes_one_progress_unit(failure):
    rows = _rows()
    session = _SyncSession(rows)
    store = SQLAlchemyPredictionPersistence(session, record_writer=_RecordWriter())
    error = {"code": "FAILED", "message": "safe failure"} if failure else None

    store._finish(_snapshot(), result=None if failure else {"ok": True}, error=error, session=session)
    assert rows[Job].progress == {"completed": 1, "total": 1, "unit": "prediction", "stage": "finished"}
    assert rows[Job].etag


@pytest.mark.parametrize("job_attempt,lease_until", [
    (2, None),
    (1, datetime.now(timezone.utc) - timedelta(seconds=1)),
])
def test_sync_rejected_attempt_or_lease_does_not_advance_progress(job_attempt, lease_until):
    rows = _rows(job_attempt=job_attempt, lease_until=lease_until)
    session = _SyncSession(rows)
    store = SQLAlchemyPredictionPersistence(session, record_writer=_RecordWriter())
    before = dict(rows[Job].progress)

    store._finish(_snapshot(), result={"ok": True}, error=None, session=session)
    assert rows[Job].progress == before
    assert session.added == []


@pytest.mark.parametrize("job_attempt,lease_until", [
    (2, None),
    (1, datetime.now(timezone.utc) - timedelta(seconds=1)),
])
def test_async_rejected_attempt_or_lease_does_not_advance_progress(job_attempt, lease_until):
    rows = _rows(job_attempt=job_attempt, lease_until=lease_until)
    session = _AsyncSession(rows)
    store = AsyncPredictionExecutionStore(lambda: session)
    before = dict(rows[Job].progress)

    assert asyncio.run(store.finish(_snapshot(), result={"ok": True}, error=None)) is False
    assert rows[Job].progress == before
    assert session.added == []


def test_async_cancellation_race_keeps_final_error_and_etag_consistent():
    rows = _rows()
    rows[Job].status = 'CANCEL_REQUESTED'
    session = _AsyncSession(rows)
    store = AsyncPredictionExecutionStore(lambda: session)
    before = dict(rows[Job].progress)

    outcome = asyncio.run(store.finish(
        _snapshot(), result=None,
        error=PublicErrorSummary(code='MODEL_FAILED', message='safe failure'),
    ))

    assert outcome is JobStatus.CANCELED
    assert rows[PredictionRow].error is None
    assert rows[PredictionRow].etag == _etag(rows[PredictionRow])
    assert rows[Job].progress == before
    assert rows[Job].result_ref is None
