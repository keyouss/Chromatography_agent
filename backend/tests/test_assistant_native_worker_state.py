from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.sql.selectable import Join

from chrom_platform.modules.assistant.persistence import (
    AgentEventRow,
    AgentRunRow,
    AssistantFrameworkStateRow,
    MessageRow,
)
from chrom_platform.modules.assistant.production_worker import (
    AssistantProductionWorker,
    AssistantWorkerError,
    AssistantWorkerMessage,
    AsyncAssistantExecutionStore,
    _Claim,
    _AssistantRunCancellationRequested,
)
from chrom_platform.task_core.state import JobStatus


CONFIG_HASH = "a" * 64


class _Result:
    def __init__(self, rows: list[object]):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _Transaction:
    def __init__(self, session: "_Session"):
        self.session = session

    async def __aenter__(self):
        self.session.log.append("begin")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            for row in self.session.pending:
                if isinstance(row, AgentEventRow):
                    self.session.database.events.append(row)
            self.session.log.append("commit")
        else:
            self.session.pending.clear()
            self.session.log.append("rollback")
        return False


class _Session:
    """Small async-session fake that evaluates these ORM reads against rows."""

    def __init__(self, database: "_Database"):
        self.database = database
        self.pending: list[object] = []
        self.log = database.log

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def begin(self):
        return _Transaction(self)

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        # Match by ORM class name so the test rows can remain simple objects.
        name = getattr(entity, "__name__", "")
        if name == "Job":
            rows = [self.database.job]
        elif name == "OutboxEvent":
            rows = [self.database.dispatch]
        elif name == "JobAttempt":
            rows = [self.database.job_attempt]
        elif name == "AgentRunRow":
            rows = [self.database.run]
        elif name == "ConversationRow":
            rows = [self.database.conversation]
        elif name == "AgentAttemptRow":
            rows = [self.database.agent_attempt]
        elif name == "LlmConfigurationRow":
            rows = [self.database.configuration]
        elif name == "MessageRow":
            rows = [self.database.message] if self.database.message is not None else []
        elif name == "AgentEventRow":
            rows = list(self.database.events)
        else:
            rows = []
        return _Result(rows)

    async def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is AssistantFrameworkStateRow:
            if any(isinstance(from_clause, Join) for from_clause in statement.get_final_froms()):
                prior = self.database.prior_framework_state
                run = self.database.prior_run
                if (prior is not None and run is not None
                        and run.status == "SUCCEEDED"
                        and prior.project_id == self.database.run.project_id
                        and prior.conversation_id == self.database.run.conversation_id
                        and prior.agent_run_id != self.database.run.id):
                    return prior
                return None
            return self.database.framework_state
        if entity is AgentRunRow:
            return self.database.run
        return None

    def add(self, row: object) -> None:
        if isinstance(row, AgentEventRow):
            self.log.append(("event_add", deepcopy(self.database.message.blocks)))
        self.pending.append(row)

    async def flush(self) -> None:
        self.log.append(("flush", deepcopy(self.database.message.blocks) if self.database.message is not None else None))


class _Database:
    def __init__(
        self,
        *,
        framework_state: AssistantFrameworkStateRow | None = None,
        prior_framework_state: AssistantFrameworkStateRow | None = None,
        prior_status: str | None = None,
    ):
        self.log: list[Any] = []
        self.framework_state = framework_state
        self.prior_framework_state = prior_framework_state
        self.prior_run = SimpleNamespace(
            id="run-prior", project_id="project-1", conversation_id="conversation-1",
            status=prior_status, finished_at=None,
        ) if prior_framework_state is not None and prior_status is not None else None
        self.events: list[AgentEventRow] = []
        self.message = MessageRow(
            id="message-assistant",
            conversation_id="conversation-1",
            seq=2,
            role="assistant",
            agent_run_id="run-1",
            status="PARTIAL",
            blocks=[{"type": "text", "text": "汉"}],
            attachment_ids=[],
            client_message_id=None,
            payload_hash=None,
        )
        self.job = SimpleNamespace(
            id="job-1", kind="assistant", attempt=1, status="RUNNING",
            resource_ref={"type": "agent_run", "id": "run-1"}, project_id="project-1",
            result_ref=None, error=None, finished_at=None, updated_at=None, revision=1, etag='"1"',
        )
        self.job_attempt = SimpleNamespace(
            job_id="job-1", attempt=1, status="RUNNING", execution_token="exec-1", fencing_token=7,
            lease_until=datetime.now(timezone.utc) + timedelta(seconds=60),
            result_ref=None, error=None, finished_at=None,
        )
        self.run = SimpleNamespace(
            id="run-1", project_id="project-1", conversation_id="conversation-1",
            input_message_id="message-user", status="RUNNING", attempt=1,
            fencing_token=7, cancel_requested=False, pending_actions=[], provider_configuration_id="config-1",
            provider_configuration_revision="revision-1", provider_configuration_hash=CONFIG_HASH,
            last_event_seq=0, finished_at=None, error=None,
        )
        self.conversation = SimpleNamespace(
            id="conversation-1", project_id="project-1", owner_id="actor-1",
        )
        self.agent_attempt = SimpleNamespace(
            id="agent-attempt-1", agent_run_id="run-1", attempt=1,
            status="RUNNING", execution_token="exec-1", fencing_token=7,
            dispatch_id="dispatch-1",
            error=None, finished_at=None, lease_until=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
        self.run.dispatch_id = "dispatch-1"
        self.run.lease_until = self.agent_attempt.lease_until = self.job_attempt.lease_until
        self.configuration = SimpleNamespace(
            id="config-1", project_id="project-1", revision="revision-1",
            status="ACTIVE", settings={"config_hash": CONFIG_HASH},
        )
        self.dispatch = SimpleNamespace(
            dispatch_id="dispatch-1", aggregate_type="job", aggregate_id="job-1",
            event_type="job.execute", status="PROCESSING",
            payload={"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"},
        )


def _worker_store(
    *,
    state: dict[str, Any] | None = None,
    prior_state: dict[str, Any] | None = None,
    prior_status: str | None = None,
):
    framework_state = None
    if state is not None:
        framework_state = AssistantFrameworkStateRow(
            agent_run_id="run-1", project_id="project-1", conversation_id="conversation-1",
            engine="pydantic-ai", framework_version="2.56.0", serialization_version="ModelMessagesTypeAdapter",
            config_hash=CONFIG_HASH, state=deepcopy(state), stream_message_id="message-assistant",
        )
    prior_framework_state = None
    if prior_state is not None:
        prior_framework_state = AssistantFrameworkStateRow(
            agent_run_id="run-prior", project_id="project-1", conversation_id="conversation-1",
            engine="pydantic-ai", framework_version="2.56.0", serialization_version="ModelMessagesTypeAdapter",
            config_hash=CONFIG_HASH, state=deepcopy(prior_state), stream_message_id=None,
        )
    database = _Database(
        framework_state=framework_state,
        prior_framework_state=prior_framework_state,
        prior_status=prior_status,
    )
    store = AsyncAssistantExecutionStore(lambda: _Session(database))
    message = AssistantWorkerMessage("job-1", 1, "dispatch-1")
    claim = _Claim(message, "run-1", database.configuration, "exec-1", 7, "agent-attempt-1")
    return database, store, claim


def test_framework_state_round_trips_private_native_history_without_public_projection() -> None:
    native = {
        "config_hash": CONFIG_HASH,
        "framework_version": "2.56.0",
        "serialization_version": "ModelMessagesTypeAdapter",
        "stream_message_id": "message-assistant",
        "history": [{"kind": "request", "tool_args": {"query": "silica"}, "tool_result": {"count": 3}}],
        "usage": {"requests": 2, "input_tokens": 17},
        "pending": {"tool_call_id": "call-1", "tool_result": {"draft": "draft-7"}},
    }
    database, store, claim = _worker_store(state=native)

    async def scenario():
        loaded = await store.framework_state(claim)
        assert loaded == native
        loaded["history"][0]["tool_args"]["query"] = "mutated"
        assert database.framework_state.state["history"][0]["tool_args"]["query"] == "silica"

        replacement = {**native, "history": [{"kind": "response", "tool_result": {"count": 4}}]}
        assert await store.framework_state(claim, replacement) is True
        assert database.framework_state.state == replacement
        assert database.framework_state.framework_version == "2.56.0"
        assert database.framework_state.serialization_version == "ModelMessagesTypeAdapter"

    asyncio.run(scenario())

    assert database.framework_state.state["history"][0]["tool_result"] == {"count": 4}


@pytest.mark.parametrize("mode", ["missing", "config-row", "state-config"])
def test_framework_state_fails_closed_for_missing_stamp_or_config_mismatch(mode: str) -> None:
    if mode == "missing":
        database, store, claim = _worker_store()
    else:
        database, store, claim = _worker_store(state={"config_hash": CONFIG_HASH})
        if mode == "config-row":
            database.framework_state.config_hash = "b" * 64

    async def scenario():
        with pytest.raises(AssistantWorkerError) as exc:
            if mode == "state-config":
                await store.framework_state(claim, {"config_hash": "c" * 64})
            else:
                await store.framework_state(claim)
        assert exc.value.code == "ASSISTANT_ENGINE_STATE_INCOMPATIBLE"

    asyncio.run(scenario())


def test_fresh_run_inherits_only_completed_prior_native_history() -> None:
    history = {"messages": [{"kind": "response", "text": "trusted prior turn"}], "usage": {"requests": 2}}
    database, store, claim = _worker_store(
        state={}, prior_state={"history_state": history}, prior_status="SUCCEEDED",
    )

    async def scenario():
        inherited = await store.framework_state(claim)
        assert inherited == {
            "fresh_run": True,
            "prior_history_state": history,
            "config_hash": CONFIG_HASH,
        }
        assert inherited["prior_history_state"] is not database.prior_framework_state.state["history_state"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("prior_status", "prior_state"),
    [
        ("RUNNING", {"history_state": {"messages": ["unfinished"]}}),
        ("SUCCEEDED", {"history_state": {}}),
        ("SUCCEEDED", {"other": "no serialized native history"}),
    ],
)
def test_fresh_run_does_not_inherit_unfinished_or_missing_prior_history(
    prior_status: str,
    prior_state: dict[str, Any],
) -> None:
    _, store, claim = _worker_store(state={}, prior_state=prior_state, prior_status=prior_status)

    async def scenario():
        assert await store.framework_state(claim) is None

    asyncio.run(scenario())


def test_framework_state_and_stream_text_reject_stale_fencing_claim() -> None:
    _, store, claim = _worker_store(state={"config_hash": CONFIG_HASH})
    stale = _Claim(claim.message, claim.run_id, claim.provider_config, claim.execution_token, 6, claim.agent_attempt_id)

    async def scenario():
        with pytest.raises(AssistantWorkerError) as exc:
            await store.framework_state(stale)
        assert exc.value.code == "WORKER_JOB_FENCE_INVALID"
        with pytest.raises(AssistantWorkerError) as stream_exc:
            await store.stream_text(stale, "append", {"block_index": 0, "offset": 1, "delta": "字"})
        assert stream_exc.value.code == "WORKER_JOB_FENCE_INVALID"

    asyncio.run(scenario())


def test_stream_text_uses_unicode_codepoint_offsets_and_commits_replacement_warning_after_snapshot() -> None:
    database, store, claim = _worker_store(state={"config_hash": CONFIG_HASH})

    async def scenario():
        for invalid_offset in (True, 1.0):
            with pytest.raises(AssistantWorkerError) as invalid_type:
                await store.stream_text(claim, "append", {
                    "message_id": "message-assistant", "block_index": 0,
                    "offset": invalid_offset, "delta": "x",
                })
            assert invalid_type.value.code == "ASSISTANT_STREAM_OFFSET_INVALID"

        appended = await store.stream_text(claim, "append", {
            "message_id": "message-assistant", "block_index": 0, "offset": 1, "delta": "🚀",
        })
        assert set(appended) == {"message_id", "block_index", "offset", "text"}
        assert appended == {"message_id": "message-assistant", "block_index": 0, "offset": 2, "text": "汉🚀"}
        delta = database.events[-1]
        assert delta.type == "message.delta"
        assert delta.data == {
            "message_id": "message-assistant", "block_index": 0,
            "delta": "🚀", "offset": 1,
        }
        assert not {"history", "usage", "pending", "state"} & set(delta.data)
        assert database.message.blocks[0]["text"] == "汉🚀"

        event_count = len(database.events)
        with pytest.raises(AssistantWorkerError) as bad_offset:
            await store.stream_text(claim, "append", {
                "message_id": "message-assistant", "block_index": 0, "offset": 1, "delta": "x",
            })
        assert bad_offset.value.code == "ASSISTANT_STREAM_OFFSET_INVALID"
        assert len(database.events) == event_count

        database.log.clear()
        replaced = await store.stream_text(claim, "replace", {
            "message_id": "message-assistant", "block_index": 0, "text": "最终快照",
        })
        assert replaced["text"] == "最终快照"
        warning = database.events[-1]
        assert warning.type == "warning"
        assert warning.data == {
            "code": "MESSAGE_SNAPSHOT_REPLACED",
            "message": "消息内容已更新，请读取最新快照",
            "message_id": "message-assistant",
            "block_index": 0,
        }
        assert database.message.blocks[0]["text"] == "最终快照"
        flush_index = next(i for i, item in enumerate(database.log) if isinstance(item, tuple) and item[0] == "flush")
        warning_index = next(i for i, item in enumerate(database.log) if isinstance(item, tuple) and item[0] == "event_add")
        commit_index = database.log.index("commit")
        assert database.log[flush_index][1][0]["text"] == "最终快照"
        assert database.log[warning_index][1][0]["text"] == "最终快照"
        assert flush_index < warning_index < commit_index

    asyncio.run(scenario())


@pytest.mark.parametrize("lease", [None, datetime(2000, 1, 1, tzinfo=timezone.utc)])
def test_expired_or_missing_lease_cannot_write_stream(lease):
    database, store, claim = _worker_store(state={"config_hash": CONFIG_HASH})
    database.job_attempt.lease_until = lease
    async def scenario():
        with pytest.raises(AssistantWorkerError) as exc:
            await store.stream_text(claim, "ensure", {})
        assert exc.value.code == "WORKER_JOB_FENCE_INVALID"
    asyncio.run(scenario())


def test_business_lease_heartbeat_keeps_delayed_stream_and_checkpoint_live(monkeypatch):
    database, store, claim = _worker_store(state={"config_hash": CONFIG_HASH})
    original_expiry = database.job_attempt.lease_until
    clock = [original_expiry - timedelta(seconds=60)]
    monkeypatch.setattr("chrom_platform.modules.assistant.production_worker._now", lambda: clock[0])
    worker = AssistantProductionWorker(session_factory=lambda: None)
    worker.store = store
    guard = SimpleNamespace(dispatcher=SimpleNamespace(heartbeat_seconds=0.01))

    async def delayed_stream():
        for _ in range(4):
            clock[0] += timedelta(seconds=20)
            await asyncio.sleep(0.02)
        assert clock[0] > original_expiry
        await store.stream_text(claim, "append", {
            "message_id": "message-assistant", "block_index": 0, "offset": 1, "delta": "续",
        })
        assert await store.framework_state(claim, {"config_hash": CONFIG_HASH, "checkpoint": "after-stream"})
        return "completed"

    async def scenario():
        result = await worker._run_with_lease_heartbeat(claim, delayed_stream(), guard)
        assert result == "completed"

    asyncio.run(scenario())
    assert database.message.blocks[0]["text"] == "汉续"
    assert database.framework_state.state["checkpoint"] == "after-stream"
    assert database.job_attempt.lease_until == database.run.lease_until == database.agent_attempt.lease_until
    assert database.job_attempt.lease_until > clock[0]


@pytest.mark.parametrize("lost", [
    "expired", "stale-fence", "canceled", "run-fence", "agent-attempt-id",
    "agent-attempt-status", "run-dispatch", "agent-dispatch", "run-expired", "agent-expired",
])
def test_business_lease_heartbeat_fails_closed_for_lost_claim(monkeypatch, lost):
    database, store, claim = _worker_store(state={"config_hash": CONFIG_HASH})
    now = [database.job_attempt.lease_until - timedelta(seconds=60)]
    monkeypatch.setattr("chrom_platform.modules.assistant.production_worker._now", lambda: now[0])
    if lost == "expired":
        now[0] = database.job_attempt.lease_until
    elif lost == "stale-fence":
        claim = _Claim(claim.message, claim.run_id, claim.provider_config, claim.execution_token, 6, claim.agent_attempt_id)
    elif lost == "canceled":
        database.run.cancel_requested = True
    elif lost == "run-fence":
        database.run.fencing_token = claim.fencing_token + 1
    elif lost == "agent-attempt-id":
        database.agent_attempt.id = "replacement-attempt"
    elif lost == "agent-attempt-status":
        database.agent_attempt.status = "FAILED"
    elif lost == "run-dispatch":
        database.run.dispatch_id = "replacement-dispatch"
    elif lost == "agent-dispatch":
        database.agent_attempt.dispatch_id = "replacement-dispatch"
    elif lost == "run-expired":
        database.run.lease_until = now[0]
    elif lost == "agent-expired":
        database.agent_attempt.lease_until = now[0]
    original_leases = (database.job_attempt.lease_until, database.run.lease_until, database.agent_attempt.lease_until)

    async def scenario():
        assert await store.renew_lease(claim) is False
        assert (database.job_attempt.lease_until, database.run.lease_until, database.agent_attempt.lease_until) == original_leases
        if lost in {"expired", "stale-fence", "canceled"}:
            with pytest.raises(AssistantWorkerError) as exc:
                await store.stream_text(claim, "ensure", {})
            assert exc.value.code == "WORKER_JOB_FENCE_INVALID"

    asyncio.run(scenario())


def test_business_heartbeat_failure_cancels_delayed_runtime():
    _database, store, claim = _worker_store(state={"config_hash": CONFIG_HASH})
    worker = AssistantProductionWorker(session_factory=lambda: None)
    worker.store = store
    store.renew_lease = lambda _claim: asyncio.sleep(0, result=False)
    guard = SimpleNamespace(dispatcher=SimpleNamespace(heartbeat_seconds=0.001))
    canceled = []

    async def delayed_runtime():
        try:
            await asyncio.sleep(1)
        finally:
            canceled.append(True)

    async def scenario():
        with pytest.raises(AssistantWorkerError) as exc:
            await worker._run_with_lease_heartbeat(claim, delayed_runtime(), guard)
        assert exc.value.code == "WORKER_JOB_FENCE_INVALID"

    asyncio.run(scenario())
    assert canceled == [True]


def test_human_cancel_interrupts_provider_task_and_converges_assistant_rows():
    database, store, claim = _worker_store(state={"config_hash": CONFIG_HASH})
    database.job.status = database.job_attempt.status = database.run.status = database.agent_attempt.status = "CANCEL_REQUESTED"
    database.run.cancel_requested = True
    database.run.pending_actions = [{"confirmation_id": "pending-1"}]
    worker = AssistantProductionWorker(session_factory=lambda: None)
    worker.store = store
    guard = SimpleNamespace(dispatcher=SimpleNamespace(heartbeat_seconds=0.001))
    provider_closed = []

    async def delayed_provider():
        try:
            await asyncio.sleep(1)
        finally:
            provider_closed.append(True)

    async def scenario():
        with pytest.raises(_AssistantRunCancellationRequested):
            await worker._run_with_lease_heartbeat(claim, delayed_provider(), guard)
        finished = await store.finish(
            claim, status="CANCELED",
            error={"code": "RUN_CANCELED", "message": "助手运行已取消", "retryable": False},
        )
        assert finished is JobStatus.CANCELED

    asyncio.run(scenario())

    assert provider_closed == [True]
    assert database.job.status == database.job_attempt.status == "CANCELED"
    assert database.run.status == database.agent_attempt.status == "CANCELED"
    assert database.run.pending_actions == []
    assert database.run.finished_at is not None
    assert database.job.finished_at is not None
    assert database.message.status == "FAILED"
    assert [row.type for row in database.events][-1] == "run.canceled"


def test_cancel_requested_probe_reads_durable_flag_or_run_status():
    database, store, _claim = _worker_store(state={"config_hash": CONFIG_HASH})

    async def scenario():
        assert await store.is_cancel_requested("run-1") is False
        database.run.status = "CANCEL_REQUESTED"
        assert await store.is_cancel_requested("run-1") is True

    asyncio.run(scenario())
