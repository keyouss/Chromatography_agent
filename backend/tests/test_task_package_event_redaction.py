"""Public event projection must not disclose private process-stop facts."""

from datetime import datetime, timezone

from chrom_platform.task_core.models import JobEvent
from chrom_platform.task_core.service import _event_view


def _row(event_type, data):
    return JobEvent(
        id="event_redaction", job_id="job_redaction", stream_id="job_redaction",
        seq=1, type=event_type, schema_version="1.0", data=data,
        timestamp=datetime.now(timezone.utc),
    )


def test_private_stop_receipt_is_redacted_without_mutating_storage():
    private = {
        "attempt": 1, "request_ref": "private-ref", "request_sha256": "a" * 64,
        "execute_dispatch_id": "private-dispatch",
        "termination": {"confirmed": True, "cleanup_completed": True,
                        "output_drained": True, "process_id": 123,
                        "creation_filetime": 456, "exit_filetime": 789,
                        "private_path": "must-not-appear"},
    }
    row = _row("model_package.termination", private)
    assert _event_view(row).data == {"stop_confirmed": True, "attempt": 1}
    assert row.data is private
    assert row.data["termination"]["process_id"] == 123


def test_unconfirmed_stop_and_malformed_attempt_stay_fail_closed():
    row = _row("model_package.termination", {
        "attempt": True,
        "termination": {"confirmed": True, "cleanup_completed": False,
                        "output_drained": True},
    })
    assert _event_view(row).data == {"stop_confirmed": False}


def test_unrelated_event_projection_is_preserved():
    public = {"status": "RUNNING", "progress": 10}
    assert _event_view(_row("job.running", public)).data == public
