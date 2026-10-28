"""Private, durable launch/stop ledger for the fixed model-package child.

This module is deliberately separate from assistant execution-ledger policy.
It stores physical process facts only; a confirmed row never means that a
model or registration passed validation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.models import (
    ExecutionLedger,
    Job,
    JobAttempt,
    OutboxEvent,
)
from chrom_platform.modules.model_center.persistence import ModelRegistrationRow


_SLOT_KEY = "model_package:global_child_slot:v1"
_SLOT_TYPE = "model_package.global_child_slot"
_INVOCATION_TYPE = "model_package.child_invocation"
_SCHEMA_VERSION = 1
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_INNODB_TABLES = (
    "execution_ledger", "job", "job_attempt", "outbox_event", "registration",
)
_TERMINATION_FIELDS = frozenset({
    "request_ref", "request_sha256", "started", "never_started", "confirmed",
    "process_id", "creation_filetime", "exit_filetime", "exit_code",
    "job_active_processes", "output_drained", "cleanup_completed", "stop_cause",
})


class ModelPackageRecoveryError(RuntimeError):
    """A fail-closed ledger or launch-binding condition."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: object) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _safe_binding(value: Mapping[str, object]) -> dict[str, object]:
    required = {
        "project_id", "registration_id", "job_id", "attempt", "execute_dispatch_id",
        "resource_type", "resource_id", "artifact_id", "artifact_sha256",
        "artifact_size_bytes", "lease_owner", "lease_token",
    }
    if set(value) != required:
        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_BINDING_INVALID")
    for key in ("project_id", "registration_id", "job_id", "execute_dispatch_id", "resource_type", "resource_id", "artifact_id", "lease_owner"):
        if not isinstance(value[key], str) or not value[key] or len(value[key]) > 128:
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_BINDING_INVALID")
    if type(value["attempt"]) is not int or value["attempt"] < 1:
        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_BINDING_INVALID")
    if type(value["lease_token"]) is not int or value["lease_token"] < 1:
        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_BINDING_INVALID")
    if not _sha(value["artifact_sha256"]):
        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_BINDING_INVALID")
    if type(value["artifact_size_bytes"]) is not int or value["artifact_size_bytes"] <= 0:
        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_BINDING_INVALID")
    if value["resource_type"] not in {"registration", "model_registration"} or value["resource_id"] != value["registration_id"]:
        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_BINDING_INVALID")
    return {key: value[key] for key in sorted(required)}


def _business_binding(binding: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in binding.items() if key not in {"lease_owner", "lease_token"}}


def _invocation_key(request_ref: str) -> str:
    digest = hashlib.sha256(request_ref.encode("utf-8")).hexdigest()
    return f"model_package:invocation:v1:{digest}"


def _validate_termination(
    request_ref: str, request_sha256: str, value: Mapping[str, object],
) -> tuple[dict[str, object], bool]:
    if set(value) != _TERMINATION_FIELDS:
        raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
    if value.get("request_ref") != request_ref or value.get("request_sha256") != request_sha256:
        raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_BINDING_INVALID")
    if not request_ref or len(request_ref) > 96 or not _sha(request_sha256):
        raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
    if type(value.get("started")) is not bool or type(value.get("never_started")) is not bool or type(value.get("confirmed")) is not bool:
        raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
    if type(value.get("output_drained")) is not bool or type(value.get("cleanup_completed")) is not bool:
        raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
    active_count = value.get("job_active_processes")
    if active_count is not None and (type(active_count) is not int or active_count < 0):
        raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
    if not isinstance(value.get("stop_cause"), str) or not value["stop_cause"] or len(value["stop_cause"]) > 64:
        raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")

    never_started = value["started"] is False and value["never_started"] is True
    started = value["started"] is True and value["never_started"] is False
    if not (never_started or started):
        raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
    if never_started:
        if (value["process_id"] is not None or value["creation_filetime"] is not None
                or value["exit_filetime"] is not None or value["exit_code"] is not None):
            raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
        if value["confirmed"] is True and (
            active_count != 0 or value["output_drained"] is not True or value["cleanup_completed"] is not True
        ):
            raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
    else:
        for key in ("process_id", "creation_filetime", "exit_filetime", "exit_code"):
            item = value.get(key)
            if item is not None and type(item) is not int:
                raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
        if value["confirmed"] is True:
            if (type(value.get("process_id")) is not int or value["process_id"] <= 0
                    or type(value.get("creation_filetime")) is not int or value["creation_filetime"] <= 0
                    or type(value.get("exit_filetime")) is not int or value["exit_filetime"] < value["creation_filetime"]
                    or type(value.get("exit_code")) is not int or value["exit_code"] == 259
                    or active_count != 0 or value["output_drained"] is not True
                    or value["cleanup_completed"] is not True):
                raise ModelPackageRecoveryError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
    return {key: value[key] for key in sorted(_TERMINATION_FIELDS)}, bool(value["confirmed"])


class SQLAlchemyModelPackageExecutionLedger:
    """Fresh-session adapter over the existing ExecutionLedger table."""

    def __init__(
        self, session_factory: Callable[[], Session], *, _allow_sqlite_for_tests: bool = False,
    ):
        if not callable(session_factory):
            raise TypeError("session_factory is required")
        if type(_allow_sqlite_for_tests) is not bool:
            raise TypeError("_allow_sqlite_for_tests must be bool")
        self._session_factory = session_factory
        self._allow_sqlite_for_tests = _allow_sqlite_for_tests

    def _require_supported_backend(self, session: Session) -> None:
        bind = session.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
        if dialect_name == "sqlite" and self._allow_sqlite_for_tests:
            return
        if dialect_name != "mysql":
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_BACKEND_UNSUPPORTED")
        for table_name in _REQUIRED_INNODB_TABLES:
            engine = session.execute(
                text(
                    "SELECT ENGINE FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name"
                ),
                {"table_name": table_name},
            ).scalar_one_or_none()
            if not isinstance(engine, str) or engine.casefold() != "innodb":
                raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_BACKEND_UNSUPPORTED")

    @staticmethod
    def _ensure_slot(session: Session) -> ExecutionLedger:
        query = select(ExecutionLedger).where(
            ExecutionLedger.operation_key == _SLOT_KEY,
            ExecutionLedger.operation_type == _SLOT_TYPE,
        ).execution_options(populate_existing=True).with_for_update()
        slot = session.scalar(query)
        if slot is not None:
            return slot
        candidate = ExecutionLedger(
            id=f"pkgslot_{uuid4().hex}", operation_key=_SLOT_KEY,
            operation_type=_SLOT_TYPE, status="SUCCEEDED", job_id=None,
            result_ref={"schema_version": _SCHEMA_VERSION, "phase": "IDLE", "generation": 0, "active_ref": None},
            error=None, started_at=_now(), finished_at=_now(), updated_at=_now(),
        )
        try:
            with session.begin_nested():
                session.add(candidate)
                session.flush()
        except IntegrityError:
            pass
        slot = session.scalar(query)
        if slot is None:
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_UNAVAILABLE")
        return slot

    @staticmethod
    def _validate_slot(slot: ExecutionLedger) -> tuple[dict[str, object], int, str | None]:
        data = slot.result_ref
        phase = data.get("phase") if isinstance(data, dict) else None
        expected_keys = {"schema_version", "phase", "generation", "active_ref"}
        if phase in {"ACTIVE", "UNKNOWN"}:
            expected_keys |= {"invocation_id"}
        if (slot.operation_type != _SLOT_TYPE or slot.job_id is not None
                or not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION
                or set(data) != expected_keys
                or type(data.get("generation")) is not int or data["generation"] < 0
                or phase not in {"IDLE", "ACTIVE", "UNKNOWN"}
                or (phase == "IDLE" and slot.status != "SUCCEEDED")
                or (phase == "ACTIVE" and slot.status != "STARTED")
                or (phase == "UNKNOWN" and slot.status != "UNKNOWN")):
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_INCONSISTENT")
        active_ref = data.get("active_ref")
        if active_ref is not None and (not isinstance(active_ref, str) or not active_ref):
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_INCONSISTENT")
        if (data["phase"] == "IDLE") != (active_ref is None):
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_INCONSISTENT")
        return dict(data), int(data["generation"]), active_ref

    @staticmethod
    def _validate_invocation(row: ExecutionLedger) -> tuple[dict[str, object], dict[str, object] | None]:
        data = row.result_ref
        base_keys = {
            "schema_version", "phase", "generation", "request_ref", "request_sha256",
            "binding", "binding_sha256",
        }
        phase = data.get("phase") if isinstance(data, dict) else None
        if phase in {"STOP_UNCONFIRMED", "STOP_CONFIRMED"}:
            expected_keys = base_keys | {"termination"}
        else:
            expected_keys = base_keys
        if (row.operation_type != _INVOCATION_TYPE or not isinstance(data, dict)
                or set(data) != expected_keys or data.get("schema_version") != _SCHEMA_VERSION
                or type(data.get("generation")) is not int or data["generation"] < 1
                or not isinstance(data.get("request_ref"), str) or not data["request_ref"]
                or len(data["request_ref"]) > 96 or not _sha(data.get("request_sha256"))
                or not isinstance(data.get("binding"), dict)
                or not isinstance(data.get("binding_sha256"), str)
                or row.job_id != data["binding"].get("job_id")
                or hashlib.sha256(_canonical(_safe_binding(data["binding"])).encode("utf-8")).hexdigest() != data["binding_sha256"]
                or row.operation_key != _invocation_key(data["request_ref"])):
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_INVOCATION_INCONSISTENT")
        receipt: dict[str, object] | None = None
        if phase == "LAUNCH_INTENT":
            if row.status != "STARTED":
                raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_INVOCATION_INCONSISTENT")
        elif phase in {"STOP_UNCONFIRMED", "STOP_CONFIRMED"}:
            raw = data.get("termination")
            if not isinstance(raw, dict):
                raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_INVOCATION_INCONSISTENT")
            receipt, confirmed = _validate_termination(data["request_ref"], data["request_sha256"], raw)
            if ((phase == "STOP_CONFIRMED") != confirmed
                    or (phase == "STOP_CONFIRMED" and row.status != "SUCCEEDED")
                    or (phase == "STOP_UNCONFIRMED" and row.status != "UNKNOWN")):
                raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_INVOCATION_INCONSISTENT")
        else:
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_INVOCATION_INCONSISTENT")
        return dict(data), receipt

    @classmethod
    def _validate_global_rows(
        cls, session: Session, slot: ExecutionLedger,
        slot_data: Mapping[str, object], generation: int, active_ref: str | None,
    ) -> list[ExecutionLedger]:
        rows = list(session.scalars(select(ExecutionLedger).where(
            ExecutionLedger.operation_type == _INVOCATION_TYPE,
        ).order_by(ExecutionLedger.id).with_for_update().execution_options(populate_existing=True)))
        unresolved: list[ExecutionLedger] = []
        max_generation = 0
        for row in rows:
            data, _receipt = cls._validate_invocation(row)
            max_generation = max(max_generation, int(data["generation"]))
            if row.status in {"STARTED", "UNKNOWN"}:
                unresolved.append(row)
        if max_generation != generation:
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_INCONSISTENT")
        phase = slot_data.get("phase")
        if phase == "IDLE":
            if unresolved or active_ref is not None:
                raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_INCONSISTENT")
        elif phase in {"ACTIVE", "UNKNOWN"}:
            if len(unresolved) != 1:
                raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_INCONSISTENT")
            active = unresolved[0]
            data = active.result_ref
            if (active.id != slot_data.get("invocation_id")
                    or not isinstance(data, dict) or data.get("request_ref") != active_ref
                    or data.get("generation") != generation):
                raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_INCONSISTENT")
        else:
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_INCONSISTENT")
        return unresolved

    @staticmethod
    def _validate_current_binding(session: Session, binding: Mapping[str, object]) -> None:
        job = session.scalar(
            select(Job).where(Job.id == binding["job_id"], Job.project_id == binding["project_id"])
            .execution_options(populate_existing=True).with_for_update()
        )
        attempt = session.scalar(
            select(JobAttempt).where(
                JobAttempt.job_id == binding["job_id"], JobAttempt.attempt == binding["attempt"],
            ).execution_options(populate_existing=True).with_for_update()
        )
        dispatch = session.scalar(
            select(OutboxEvent).where(OutboxEvent.dispatch_id == binding["execute_dispatch_id"])
            .execution_options(populate_existing=True).with_for_update()
        )
        registration = session.scalar(
            select(ModelRegistrationRow).where(
                ModelRegistrationRow.id == binding["registration_id"],
                ModelRegistrationRow.project_id == binding["project_id"],
            ).execution_options(populate_existing=True).with_for_update()
        )
        now = _now()
        if (job is None or attempt is None or dispatch is None or registration is None
                or job.kind != "model_registration_validation" or job.attempt != binding["attempt"]
                or job.status != "RUNNING" or attempt.status != "RUNNING"
                or job.resource_ref.get("type") != binding["resource_type"]
                or job.resource_ref.get("id") != binding["resource_id"]
                or registration.job_id != binding["job_id"]
                or registration.attempt != binding["attempt"]
                or registration.dispatch_id != binding["execute_dispatch_id"]
                or registration.status != "VALIDATING"
                or registration.task_type != "gc"
                or not isinstance(registration.source, dict)
                or registration.source.get("kind") != "external_package"
                or registration.source.get("artifact_id") != binding["artifact_id"]
                or dispatch.aggregate_type != "job" or dispatch.aggregate_id != binding["job_id"]
                or dispatch.event_type != "job.execute" or dispatch.status != "PROCESSING"
                or dict(dispatch.payload or {}) != {
                    "job_id": binding["job_id"], "attempt": binding["attempt"],
                    "dispatch_id": binding["execute_dispatch_id"],
                }
                or dispatch.lease_owner != binding["lease_owner"]
                or dispatch.lease_token != binding["lease_token"]
                or dispatch.lease_expires_at is None or dispatch.lease_expires_at <= now):
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_LAUNCH_BINDING_STALE")

    def before_launch(
        self, binding: Mapping[str, object], request_ref: str, request_sha256: str,
    ) -> None:
        normalized = _safe_binding(binding)
        if not isinstance(request_ref, str) or not request_ref or len(request_ref) > 96 or not _sha(request_sha256):
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_REQUEST_INVALID")
        session = self._session_factory()
        try:
            try:
                with session.begin():
                    self._require_supported_backend(session)
                    slot = self._ensure_slot(session)
                    slot_data, generation, active_ref = self._validate_slot(slot)
                    unresolved = self._validate_global_rows(session, slot, slot_data, generation, active_ref)
                    if unresolved or slot.status != "SUCCEEDED" or slot_data["phase"] != "IDLE" or active_ref is not None:
                        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_REQUIRED")
                    history = session.scalars(select(ExecutionLedger).where(
                        ExecutionLedger.operation_type == _INVOCATION_TYPE,
                        ExecutionLedger.job_id == normalized["job_id"],
                    ).order_by(ExecutionLedger.started_at.desc(), ExecutionLedger.id.desc())
                        .with_for_update().execution_options(populate_existing=True))
                    for prior_row in history:
                        prior_data = prior_row.result_ref
                        prior_binding = prior_data.get("binding") if isinstance(prior_data, dict) else None
                        if (isinstance(prior_binding, dict)
                                and _business_binding(prior_binding) == _business_binding(normalized)):
                            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_REQUEST_REUSED")
                    self._validate_current_binding(session, normalized)
                    operation_key = _invocation_key(request_ref)
                    existing = session.scalar(select(ExecutionLedger).where(
                        ExecutionLedger.operation_key == operation_key,
                    ).execution_options(populate_existing=True).with_for_update())
                    if existing is not None:
                        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_REQUEST_REUSED")
                    binding_hash = hashlib.sha256(_canonical(normalized).encode("utf-8")).hexdigest()
                    invocation = ExecutionLedger(
                        id=f"pkgexec_{uuid4().hex}", operation_key=operation_key,
                        operation_type=_INVOCATION_TYPE, status="STARTED",
                        job_id=str(normalized["job_id"]),
                        result_ref={
                            "schema_version": _SCHEMA_VERSION, "phase": "LAUNCH_INTENT",
                            "generation": generation + 1, "request_ref": request_ref,
                            "request_sha256": request_sha256, "binding": normalized,
                            "binding_sha256": binding_hash,
                        },
                        error=None, started_at=_now(), finished_at=None, updated_at=_now(),
                    )
                    session.add(invocation)
                    slot.status = "STARTED"
                    slot.result_ref = {
                        "schema_version": _SCHEMA_VERSION, "phase": "ACTIVE",
                        "generation": generation + 1, "active_ref": request_ref,
                        "invocation_id": invocation.id,
                    }
                    slot.finished_at = None
                    slot.updated_at = _now()
            except ModelPackageRecoveryError:
                raise
            except Exception as exc:
                # Even an ambiguous commit must not permit a child launch.
                # The durable row, if committed, blocks the global slot.
                raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_UNAVAILABLE") from exc
        finally:
            session.close()

    def stop_sink(
        self, binding: Mapping[str, object], request_ref: str, request_sha256: str,
        termination: Mapping[str, object],
    ) -> None:
        normalized = _safe_binding(binding)
        receipt, confirmed = _validate_termination(request_ref, request_sha256, termination)
        session = self._session_factory()
        try:
            with session.begin():
                slot = self._ensure_slot(session)
                slot_data, generation, active_ref = self._validate_slot(slot)
                self._validate_global_rows(session, slot, slot_data, generation, active_ref)
                operation_key = _invocation_key(request_ref)
                invocation = session.scalar(select(ExecutionLedger).where(
                    ExecutionLedger.operation_key == operation_key,
                    ExecutionLedger.operation_type == _INVOCATION_TYPE,
                ).execution_options(populate_existing=True).with_for_update())
                if invocation is None:
                    raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_INTENT_MISSING")
                prior, previous_receipt = self._validate_invocation(invocation)
                if (prior.get("request_ref") != request_ref or prior.get("request_sha256") != request_sha256
                        or prior.get("binding") != normalized):
                    raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_RECEIPT_BINDING_INVALID")
                if invocation.status == "SUCCEEDED":
                    if prior.get("phase") == "STOP_CONFIRMED" and previous_receipt == receipt:
                        return
                    raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_RECEIPT_CONFLICT")
                if invocation.status not in {"STARTED", "UNKNOWN"}:
                    raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_INVOCATION_INCONSISTENT")
                if previous_receipt is not None:
                    if not isinstance(previous_receipt, dict):
                        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_INVOCATION_INCONSISTENT")
                    if previous_receipt == receipt:
                        return
                    if confirmed is not True or previous_receipt.get("confirmed") is True:
                        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_RECEIPT_CONFLICT")
                    if any(previous_receipt.get(key) != receipt.get(key) for key in ("started", "never_started")):
                        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_RECEIPT_CONFLICT")
                    for key in ("process_id", "creation_filetime", "exit_filetime"):
                        old, new = previous_receipt.get(key), receipt.get(key)
                        if old is not None and old != new:
                            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_RECEIPT_CONFLICT")
                    old_exit_code = previous_receipt.get("exit_code")
                    if old_exit_code is not None and old_exit_code != 259 and receipt.get("exit_code") != old_exit_code:
                        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_RECEIPT_CONFLICT")
                    if (previous_receipt.get("stop_cause") != receipt.get("stop_cause")
                            or previous_receipt.get("output_drained") is True and receipt.get("output_drained") is not True
                            or previous_receipt.get("cleanup_completed") is True and receipt.get("cleanup_completed") is not True):
                        raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_RECEIPT_CONFLICT")
                if active_ref != request_ref or slot_data.get("invocation_id") != invocation.id:
                    raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_GENERATION_STALE")
                if int(prior.get("generation", -1)) != generation or int(slot_data.get("generation", -1)) != generation:
                    raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_SLOT_GENERATION_STALE")
                prior = dict(prior)
                prior["termination"] = receipt
                prior["phase"] = "STOP_CONFIRMED" if confirmed else "STOP_UNCONFIRMED"
                invocation.result_ref = dict(prior)
                invocation.status = "SUCCEEDED" if confirmed else "UNKNOWN"
                invocation.finished_at = _now() if confirmed else None
                invocation.updated_at = _now()
                if confirmed:
                    slot.status = "SUCCEEDED"
                    slot.result_ref = {
                        "schema_version": _SCHEMA_VERSION, "phase": "IDLE",
                        "generation": generation, "active_ref": None,
                    }
                    slot.finished_at = _now()
                else:
                    slot.status = "UNKNOWN"
                    slot.result_ref = {
                        "schema_version": _SCHEMA_VERSION, "phase": "UNKNOWN",
                        "generation": generation, "active_ref": request_ref,
                        "invocation_id": invocation.id,
                    }
                    slot.finished_at = None
                slot.updated_at = _now()
        finally:
            session.close()

    def confirmed_receipt(self, binding: Mapping[str, object]) -> dict[str, object] | None:
        """Read a historical stop fact for the same business attempt only."""
        normalized = _safe_binding(binding)
        session = self._session_factory()
        try:
            with session.begin():
                slot = self._ensure_slot(session)
                slot_data, generation, active_ref = self._validate_slot(slot)
                self._validate_global_rows(session, slot, slot_data, generation, active_ref)
                rows = session.scalars(select(ExecutionLedger).where(
                    ExecutionLedger.operation_type == _INVOCATION_TYPE,
                    ExecutionLedger.job_id == normalized["job_id"],
                ).order_by(ExecutionLedger.started_at.desc(), ExecutionLedger.id.desc()).with_for_update()
                    .execution_options(populate_existing=True))
                for row in rows:
                    data, termination = self._validate_invocation(row)
                    if (row.status != "SUCCEEDED" or data.get("phase") != "STOP_CONFIRMED"
                            or not isinstance(termination, dict)
                            or _business_binding(data["binding"]) != _business_binding(normalized)):
                        continue
                    return {
                        "request_ref": data.get("request_ref"),
                        "request_sha256": data.get("request_sha256"),
                        "termination": dict(termination),
                        "executor_status": "SUCCEEDED",
                        "executor_error_code": None,
                            "ledger_stop_confirmed": True,
                    }
                return None
        finally:
            session.close()

    def confirmed_stop(
        self, binding: Mapping[str, object], request_ref: str, request_sha256: str,
    ) -> bool:
        normalized = _safe_binding(binding)
        session = self._session_factory()
        try:
            with session.begin():
                slot = self._ensure_slot(session)
                slot_data, generation, active_ref = self._validate_slot(slot)
                self._validate_global_rows(session, slot, slot_data, generation, active_ref)
                row = session.scalar(select(ExecutionLedger).where(
                    ExecutionLedger.operation_key == _invocation_key(request_ref),
                    ExecutionLedger.operation_type == _INVOCATION_TYPE,
                ).execution_options(populate_existing=True).with_for_update())
                if row is None:
                    return False
                data, receipt = self._validate_invocation(row)
                return bool(
                    row.status == "SUCCEEDED"
                    and data.get("phase") == "STOP_CONFIRMED"
                    and data.get("request_ref") == request_ref
                    and data.get("request_sha256") == request_sha256
                    and data.get("binding") == normalized
                    and isinstance(receipt, dict)
                )
        finally:
            session.close()

    def confirmed_receipt_for_attempt(
        self, *, project_id: str, registration_id: str, job_id: str,
        attempt: int, execute_dispatch_id: str, artifact_id: str,
    ) -> dict[str, object] | None:
        """Find a confirmed physical stop for the exact current business binding.

        Lease owner/token are intentionally not part of this read: a new owner
        may consume an old owner's physical fact, but only its own lease may
        project that fact into the existing JobEvent/business lifecycle.
        """
        expected = {
            "project_id": project_id, "registration_id": registration_id,
            "job_id": job_id, "attempt": attempt,
            "execute_dispatch_id": execute_dispatch_id,
            "resource_id": registration_id, "artifact_id": artifact_id,
        }
        if (any(not isinstance(value, str) or not value for key, value in expected.items() if key != "attempt")
                or type(attempt) is not int or attempt < 1):
            raise ModelPackageRecoveryError("MODEL_PACKAGE_RECOVERY_BINDING_INVALID")
        session = self._session_factory()
        try:
            with session.begin():
                slot = self._ensure_slot(session)
                slot_data, generation, active_ref = self._validate_slot(slot)
                self._validate_global_rows(session, slot, slot_data, generation, active_ref)
                rows = session.scalars(select(ExecutionLedger).where(
                    ExecutionLedger.operation_type == _INVOCATION_TYPE,
                    ExecutionLedger.job_id == job_id,
                    ExecutionLedger.status == "SUCCEEDED",
                ).order_by(ExecutionLedger.started_at.desc(), ExecutionLedger.id.desc())
                    .with_for_update().execution_options(populate_existing=True))
                for row in rows:
                    data, termination = self._validate_invocation(row)
                    binding = data["binding"]
                    if (not isinstance(termination, dict) or data.get("phase") != "STOP_CONFIRMED"
                            or binding.get("resource_type") not in {"registration", "model_registration"}
                            or any(binding.get(key) != value for key, value in expected.items())):
                        continue
                    return {
                        "request_ref": data.get("request_ref"),
                        "request_sha256": data.get("request_sha256"),
                        "termination": dict(termination),
                        "executor_status": "SUCCEEDED",
                        "executor_error_code": None,
                        "ledger_stop_confirmed": True,
                    }
                return None
        finally:
            session.close()


__all__ = ["ModelPackageRecoveryError", "SQLAlchemyModelPackageExecutionLedger"]
