"""Fixed data-import task handlers. Uploaded content is parsed as data only."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
from datetime import datetime, timezone
from collections.abc import Callable, Mapping
from typing import Any, get_args
from uuid import uuid4

from sqlalchemy import select
from pydantic import TypeAdapter

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.models import AuditEvent, Confirmation, Job, OutboxEvent, Project
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import JobStatus, sync_job_lifecycle
from chrom_platform.task_core.outbox_dispatch import LeaseLostError

from .errors import DataConflictError, DataUnavailableError, DataValidationError
from .import_artifacts import SQLAlchemyImportArtifactPort
from .persistence import (
    DataImportRow,
    DataImportValidationRow,
    DataVersionRow,
    ExperimentRecordRow,
    MoleculeRow,
    SQLAlchemyDataDomainRegistry,
    SQLAlchemyDataRepository,
)
from .schemas import DataImport, DataImportValidation


_ACTOR_AUTH_METHODS = frozenset(get_args(ActorContext.model_fields["auth_method"].annotation))
_AUDIT_INTERACTIONS = frozenset({"human", "agent_tool", "worker", "service"})


def _parser_binding_hash(profile: Any, domain: Any) -> tuple[str, str, str]:
    from . import csv_import
    from . import import_report

    profile_json = json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    domain_json = json.dumps(domain.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    module_hash = hashlib.sha256(inspect.getsource(sys.modules[csv_import.__name__]).encode("utf-8")).hexdigest()
    report_module_hash = hashlib.sha256(inspect.getsource(sys.modules[import_report.__name__]).encode("utf-8")).hexdigest()
    profile_hash = hashlib.sha256(profile_json.encode("utf-8")).hexdigest()
    domain_hash = hashlib.sha256(domain_json.encode("utf-8")).hexdigest()
    parser_hash = hashlib.sha256(json.dumps({
        "revision": csv_import.PARSER_REVISION,
        "module_sha256": module_hash,
        "report_module_sha256": report_module_hash,
        "report_schema_version": "data-import-validation-report-1",
        "profile_sha256": profile_hash,
        "domain_sha256": domain_hash,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return parser_hash, profile_hash, domain_hash


def _add_publish_audit(session: Any, *, job: Job, data_import: DataImportRow,
                       binding: Mapping[str, Any], result: Mapping[str, Any], version_number: int) -> None:
    version_id = result.get("data_version_id")
    if not isinstance(version_id, str) or not version_id:
        raise DataConflictError("发布审计缺少数据版本身份")
    session.add(AuditEvent(
        id=f"audit_{uuid4().hex}", project_id=data_import.project_id,
        actor_id=job.owner_id, auth_method="service", interaction="worker",
        request_id=f"worker:{job.id}", action="data_import.commit",
        resource_type="data_version", resource_id=version_id,
        resource_ref={"type": "data_version", "id": version_id},
        before_summary={"status": "COMMITTING"},
        after_summary={
            "phase": "published", "status": "READY", "data_import_id": data_import.id,
            "data_version_id": version_id,
            "data_version_number": version_number,
            "snapshot_artifact_id": result.get("snapshot_artifact_id"),
            "snapshot_sha256": result.get("sha256"), "record_count": result.get("record_count"),
            "source_artifact_id": data_import.artifact_id,
            "source_sha256": binding.get("source_sha256"), "domain_id": data_import.domain_id,
            "schema_version": data_import.schema_version,
            "parent_version_id": binding.get("parent_version_id"),
            "binding_hash": binding.get("binding_hash"),
            "origin_actor_id": binding.get("origin_actor_id"),
            "origin_auth_method": binding.get("origin_auth_method"),
            "origin_request_id": binding.get("origin_request_id"),
        },
        confirmation_id=binding.get("confirmation_id"),
        config_hash=binding.get("binding_hash"), job_id=job.id,
        agent_run_id=binding.get("origin_agent_run_id"), result="SUCCEEDED",
        timestamp=datetime.now(timezone.utc),
    ))
    session.flush()


class CSVImportValidationPort:
    """Bind the pure parser to scoped artifacts, a trusted profile and report storage."""

    parser_revision = "csv-import-parser-1"

    def __init__(
        self,
        session: Any,
        artifact_store: Any,
        *,
        profile_resolver: Callable[[str, str], Any | None],
        owner_id: str,
        normalize_structure: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self.session = session
        self.artifact_store = artifact_store
        self.profile_resolver = profile_resolver
        self.owner_id = owner_id
        self.normalize_structure = normalize_structure
        self.parser_hash: str | None = None

    def validate(self, data_import: DataImport, domain) -> DataImportValidation:
        from . import csv_import
        from .csv_import import make_identity_key, parse_csv_import
        from .import_artifacts import SQLAlchemyImportArtifactPort
        from .service import _now, _id
        from .schemas import DataImportValidation, ValidationCounts

        artifact_port = SQLAlchemyImportArtifactPort(self.session, self.artifact_store)
        source = artifact_port.read_input(data_import.project_id, data_import.artifact_id)
        if source.media_type != "text/csv":
            raise DataValidationError("该导入切片只接受 text/csv 制品")
        profile = self.profile_resolver(data_import.domain_id, data_import.schema_version)
        if profile is None:
            raise DataUnavailableError("该数据域没有受信 CSV profile")
        profile_payload = profile.model_dump(mode="json")
        domain_payload = domain.model_dump(mode="json")
        self.parser_hash, profile_hash, domain_hash = _parser_binding_hash(profile, domain)
        existing: set[str] = set()
        latest = self.session.scalar(
            select(DataVersionRow)
            .where(
                DataVersionRow.project_id == data_import.project_id,
                DataVersionRow.domain_id == data_import.domain_id,
                DataVersionRow.status == "READY",
            )
            .order_by(DataVersionRow.version.desc())
            .limit(1)
        )
        if latest is not None:
            for values in self.session.scalars(select(ExperimentRecordRow.values).where(
                ExperimentRecordRow.project_id == data_import.project_id,
                ExperimentRecordRow.domain_id == data_import.domain_id,
                ExperimentRecordRow.data_version_id == latest.id,
            )):
                existing.add(make_identity_key(domain, values))
        parsed = parse_csv_import(
            source.content, data_import, domain, profile,
            normalize_structure=self.normalize_structure,
            existing_identity_keys=existing,
        )
        report_bytes = parsed.report.to_json_bytes()
        imported_row = self.session.scalar(select(DataImportRow).where(
            DataImportRow.id == data_import.id,
            DataImportRow.project_id == data_import.project_id,
        ).with_for_update())
        if imported_row is None or imported_row.revision != data_import.revision or imported_row.config_hash != data_import.config_hash:
            raise DataConflictError("导入 revision 在校验过程中已变化")
        frozen = dict(imported_row.request_snapshot or {})
        parent_id = frozen.get("validation_parent_version_id")
        parent = self.session.scalar(select(DataVersionRow).where(DataVersionRow.id == parent_id)) if parent_id else None
        frozen["validation_binding"] = {
            "revision": data_import.revision,
            "config_hash": data_import.config_hash,
            "source_sha256": source.sha256,
            "profile_id": profile.profile_id,
            "profile_hash": profile_hash,
            "domain_hash": domain_hash,
            "parser_hash": self.parser_hash,
            "parent_version_id": parent_id,
            "parent_version_sha256": parent.sha256 if parent else None,
        }
        imported_row.request_snapshot = frozen
        report_artifact_id, _, _, report_sha = artifact_port.publish_generated(
            project_id=data_import.project_id,
            owner_id=self.owner_id,
            kind="export_report",
            title=f"data-import-validation-{data_import.id}.json",
            media_type="application/json",
            content=report_bytes,
            source_ref={"type": "data_import_validation", "id": "pending"},
            generation="data_import_validation_report_v1",
        )
        frozen = dict(frozen)
        frozen["validation_binding"] = dict(frozen["validation_binding"])
        frozen["validation_binding"]["report_sha256"] = report_sha
        imported_row.request_snapshot = frozen
        validation_id = _id("div")
        # The report source reference is immutable. Update it in this same
        # transaction once the validation identifier has been allocated.
        report_row = self.session.scalar(select(Artifact).where(Artifact.id == report_artifact_id).with_for_update())
        if report_row is None:
            raise DataUnavailableError("导入验证报告制品未持久化")
        report_row.metadata_json = {
            "source_ref": {"type": "data_import_validation", "id": validation_id},
            "generation": "data_import_validation_report_v1",
            "validation_binding": {
                "revision": data_import.revision,
                "config_hash": data_import.config_hash,
                "source_artifact_id": data_import.artifact_id,
                "source_sha256": source.sha256,
                "parser_hash": self.parser_hash,
            },
        }
        status = parsed.report.status if parsed.report.status in {"VALID", "INVALID"} else "INVALID"
        return DataImportValidation(
            id=validation_id,
            import_id=data_import.id,
            revision=data_import.revision,
            config_hash=data_import.config_hash,
            status=status,
            counts=ValidationCounts(**parsed.report.counts.model_dump()),
            report_artifact_id=report_artifact_id,
            warnings=[] if parsed.report.status == "VALID" else ["CSV 校验未通过；请查看受控验证报告。"],
            parser_revision=self.parser_revision,
            parser_hash=self.parser_hash,
            created_at=_now(),
        )


class SQLAlchemyImportCommitter:
    """Reparse the frozen source and atomically publish its complete snapshot."""

    def __init__(
        self,
        artifact_store: Any,
        *,
        profile_resolver: Callable[[str, str], Any | None],
        ensure_structure: Callable[..., Mapping[str, Any]] | None,
        normalize_structure: Callable[[str], Mapping[str, Any]] | None,
    ) -> None:
        self.artifact_store = artifact_store
        self.profile_resolver = profile_resolver
        self.ensure_structure = ensure_structure
        self.normalize_structure = normalize_structure

    def __call__(self, *, session: Any, data_import: DataImportRow, job: Job, payload: Mapping[str, Any], lease_guard: Any) -> Mapping[str, Any]:
        from .csv_import import make_identity_key, parse_csv_import
        from .import_artifacts import SQLAlchemyImportArtifactPort
        from .import_report import CsvImportValidationReport
        from .schemas import DataImport, DataVersionDraft

        snapshot = dict(data_import.request_snapshot or {})
        binding = snapshot.get("commit_binding")
        if not isinstance(binding, dict) or binding.get("job_id") != job.id:
            raise DataConflictError("冻结提交授权缺失或任务不匹配")
        if binding.get("revision") != data_import.revision or binding.get("config_hash") != data_import.config_hash:
            raise DataConflictError("冻结提交授权与当前导入 revision 不匹配")
        repo = SQLAlchemyDataRepository(session)
        imported = repo.get_import(data_import.project_id, data_import.id)
        if imported is None:
            raise DataConflictError("导入目标不存在")
        confirmation_id = binding.get("confirmation_id")
        binding_hash = binding.get("binding_hash")
        origin_actor_id = binding.get("origin_actor_id")
        origin_auth_method = binding.get("origin_auth_method")
        origin_interaction = binding.get("origin_interaction")
        origin_request_id = binding.get("origin_request_id")
        origin_agent_run_id = binding.get("origin_agent_run_id")
        if (
            job.project_id != imported.project_id
            or job.owner_id != origin_actor_id
            or job.kind != "data_import_commit"
            or job.resource_ref != {"type": "data_import", "id": imported.id}
            or not isinstance(confirmation_id, str) or not confirmation_id or len(confirmation_id) > 96
            or not isinstance(binding_hash, str) or re.fullmatch(r"[0-9a-f]{64}", binding_hash) is None
            or not isinstance(origin_auth_method, str) or origin_auth_method not in _ACTOR_AUTH_METHODS
            or not isinstance(origin_interaction, str) or origin_interaction not in _AUDIT_INTERACTIONS
            or not isinstance(origin_request_id, str) or not origin_request_id or len(origin_request_id) > 96
            or (origin_agent_run_id is not None and (not isinstance(origin_agent_run_id, str) or len(origin_agent_run_id) > 96))
        ):
            raise DataConflictError("提交审计来源绑定无效")
        confirmation = session.scalar(select(Confirmation).where(
            Confirmation.id == confirmation_id,
            Confirmation.project_id == imported.project_id,
        ).with_for_update())
        if (
            confirmation is None or confirmation.status != "CONSUMED"
            or confirmation.actor_id != origin_actor_id
            or confirmation.action != "data_import.commit"
            or confirmation.resource_ref != {"type": "data_import", "id": imported.id}
            or confirmation.revision != imported.revision
            or confirmation.config_hash != binding_hash
        ):
            raise DataConflictError("提交确认与审计来源绑定不匹配")
        input_artifact = SQLAlchemyImportArtifactPort(session, self.artifact_store).read_input(
            imported.project_id, imported.artifact_id
        )
        if input_artifact.media_type != "text/csv":
            raise DataConflictError("冻结的输入制品不再是 text/csv")
        if input_artifact.sha256 != binding.get("source_sha256"):
            raise DataConflictError("输入制品哈希已变化，拒绝使用旧确认")
        from chrom_platform.artifact_core.models import Artifact
        validation_row = session.scalar(select(DataImportValidationRow).where(
            DataImportValidationRow.id == binding.get("validation_id"),
            DataImportValidationRow.project_id == imported.project_id,
            DataImportValidationRow.import_id == imported.id,
            DataImportValidationRow.status == "VALID",
        ))
        report_row = session.scalar(select(Artifact).where(
            Artifact.id == binding.get("report_artifact_id"),
            Artifact.project_id == imported.project_id,
            Artifact.status == "AVAILABLE",
            Artifact.kind == "export_report",
        ))
        if validation_row is None or report_row is None or report_row.scan_status != "NOT_CONFIGURED" or (report_row.metadata_json or {}).get("generation") != "data_import_validation_report_v1" or (report_row.metadata_json or {}).get("source_ref") != {"type": "data_import_validation", "id": validation_row.id}:
            raise DataConflictError("原验证结果或报告已不可用")
        report_bytes = self.artifact_store.read(report_row.storage_key)
        report_sha = hashlib.sha256(report_bytes).hexdigest()
        if len(report_bytes) != report_row.size_bytes or report_sha != report_row.sha256 or report_sha != binding.get("report_sha256"):
            raise DataConflictError("冻结验证报告哈希已变化")
        try:
            report = CsvImportValidationReport.model_validate_json(report_bytes)
        except (TypeError, ValueError):
            raise DataConflictError("冻结验证报告格式无效") from None
        if report.status != "VALID" or report.source_sha256 != input_artifact.sha256 or report.revision != imported.revision or report.config_hash != imported.config_hash:
            raise DataConflictError("验证报告与冻结输入不匹配")
        if (imported.request_snapshot or {}).get("validation_binding", {}).get("source_sha256") != input_artifact.sha256:
            raise DataConflictError("验证源制品SHA与冻结校验不匹配")
        domain = SQLAlchemyDataDomainRegistry(session).get(imported.domain_id, imported.schema_version)
        if domain is None:
            raise DataUnavailableError("受信数据域定义不可用")
        profile = self.profile_resolver(imported.domain_id, imported.schema_version)
        if profile is None:
            raise DataUnavailableError("该数据域没有受信 CSV profile")
        parser_hash, profile_hash, domain_hash = _parser_binding_hash(profile, domain)
        validation_binding = (imported.request_snapshot or {}).get("validation_binding")
        if not isinstance(validation_binding, dict) or (
            validation_binding.get("parser_hash") != validation_row.parser_hash
            or validation_binding.get("parser_hash") != parser_hash
            or validation_binding.get("profile_hash") != profile_hash
            or validation_binding.get("domain_hash") != domain_hash
            or binding.get("parser_hash") != parser_hash
            or binding.get("profile_hash") != profile_hash
            or binding.get("domain_hash") != domain_hash
        ):
            raise DataConflictError("受信profile、领域schema或parser指纹已变化")
        latest = session.scalar(select(DataVersionRow).where(
            DataVersionRow.project_id == imported.project_id,
            DataVersionRow.domain_id == imported.domain_id,
            DataVersionRow.status == "READY",
        ).order_by(DataVersionRow.version.desc()).limit(1).with_for_update())
        parent_id = latest.id if latest else None
        parent_sha256 = latest.sha256 if latest else None
        if parent_id != binding.get("parent_version_id") or parent_sha256 != binding.get("parent_version_sha256"):
            raise DataConflictError("提交父版本已变化，拒绝丢失更新")
        previous_records = list(session.scalars(select(ExperimentRecordRow).where(
            ExperimentRecordRow.project_id == imported.project_id,
            ExperimentRecordRow.domain_id == imported.domain_id,
            ExperimentRecordRow.data_version_id == parent_id,
        ))) if parent_id else []
        existing = {make_identity_key(domain, row.values) for row in previous_records}
        parsed = parse_csv_import(
            input_artifact.content, imported, domain, profile,
            normalize_structure=self.normalize_structure,
            existing_identity_keys=existing,
        )
        if parsed.report.status != "VALID" or parsed.report.source_sha256 != report.source_sha256 or parsed.report.counts.model_dump() != report.counts.model_dump():
            raise DataConflictError("提交重新校验结果与确认绑定不一致")
        if parsed.rows and self.ensure_structure is None:
            raise DataUnavailableError("化学结构持久化端口未配置")
        if lease_guard is not None:
            lease_guard.check()

        now = datetime.now(timezone.utc)
        version_id = repo._id_factory("dv")
        records: list[dict[str, Any]] = [
            {"molecule_id": row.molecule_id, "identity_hash": row.identity_hash, "values": row.values}
            for row in previous_records
        ]
        for parsed_row in parsed.rows:
            normalized = parsed_row.normalized_structure
            if normalized is None:
                raise DataValidationError("数据行缺少经校验的化学结构")
            structure = self.ensure_structure(
                session=session,
                project_id=imported.project_id,
                normalized_structure=normalized,
                source_artifact_id=imported.artifact_id,
            )
            if not isinstance(structure, Mapping) or not isinstance(structure.get("structure_id"), str) or not structure.get("structure_id") or not isinstance(structure.get("canonical_smiles"), str) or not structure.get("canonical_smiles"):
                raise DataUnavailableError("结构持久化端口未返回可信结构身份")
            molecule = session.scalar(select(MoleculeRow).where(
                MoleculeRow.project_id == imported.project_id,
                MoleculeRow.structure_id == structure["structure_id"],
            ).with_for_update())
            if molecule is None:
                molecule = MoleculeRow(
                    id=repo._id_factory("mol"), project_id=imported.project_id,
                    structure_id=structure["structure_id"], canonical_smiles=structure["canonical_smiles"],
                    inchikey=structure.get("inchikey"), source=imported.source_description,
                    quality_status="UNKNOWN", created_at=now,
                )
                session.add(molecule)
                session.flush()
            identity_hash = hashlib.sha256(parsed_row.identity_key.encode("utf-8")).hexdigest()
            values = json.loads(TypeAdapter(dict[str, Any]).dump_json(dict(parsed_row.normalized_values)))
            records.append({"molecule_id": molecule.id, "identity_hash": identity_hash, "values": values})

        if not records or len(records) != report.counts.new + len(previous_records):
            raise DataConflictError("提交记录数与验证计数不一致")
        snapshot_doc = {
            "snapshot_schema_version": "data-import-snapshot-1",
            "project_id": imported.project_id,
            "domain_id": imported.domain_id,
            "schema_version": imported.schema_version,
            "source_import_id": imported.id,
            "source_artifact_id": imported.artifact_id,
            "source_sha256": input_artifact.sha256,
            "parent_version_id": parent_id,
            "records": records,
        }
        snapshot_bytes = TypeAdapter(dict[str, Any]).dump_json(snapshot_doc)
        snapshot_sha = hashlib.sha256(snapshot_bytes).hexdigest()
        if lease_guard is not None:
            lease_guard.check()
        artifact_port = SQLAlchemyImportArtifactPort(session, self.artifact_store)
        snapshot_artifact_id, _, _, stored_sha = artifact_port.publish_generated(
            project_id=imported.project_id, owner_id=job.owner_id,
            kind="dataset_package", title=f"data-version-{version_id}.json",
            media_type="application/json", content=snapshot_bytes,
            source_ref={"type": "data_version", "id": version_id},
            generation="data_import_snapshot_v1",
        )
        if stored_sha != snapshot_sha:
            raise DataConflictError("快照存储哈希不匹配")
        version = repo.allocate_and_save_new_data_version(DataVersionDraft(
            project_id=imported.project_id, domain_id=imported.domain_id,
            source_import_id=imported.id, snapshot_artifact_id=snapshot_artifact_id,
            schema_version=imported.schema_version, sha256=snapshot_sha,
            record_count=len(records), quality_report_ref={"type": "artifact", "id": report_row.id},
        ), version_id=version_id)
        for record in records:
            session.add(ExperimentRecordRow(
                id=repo._id_factory("er"), project_id=imported.project_id,
                molecule_id=record["molecule_id"], domain_id=imported.domain_id,
                chromatography_type=domain.chromatography_type,
                data_version_id=version.id, identity_hash=record["identity_hash"],
                values=record["values"], created_at=now,
            ))
        committed = imported.model_copy(update={"status": "COMMITTED", "new_data_version_id": version.id, "updated_at": now})
        repo.save_import(committed)
        session.flush()
        if lease_guard is not None:
            lease_guard.check()
        _add_publish_audit(
            session, job=job, data_import=imported,
            binding=binding, result={
                "data_version_id": version.id,
                "snapshot_artifact_id": snapshot_artifact_id,
                "sha256": snapshot_sha,
                "record_count": len(records),
            }, version_number=version.version,
        )
        return {"data_version_id": version.id, "snapshot_artifact_id": snapshot_artifact_id, "sha256": snapshot_sha, "record_count": len(records)}


class DataImportWorker:
    """Validate task delivery and fence late attempts before applying a result."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        artifact_store: Any,
        *,
        profile_resolver: Callable[[str, str], Any | None],
        structure_normalizer: Callable[[str], Mapping[str, Any]] | None = None,
        ensure_structure: Callable[..., Mapping[str, Any]] | None = None,
        commit_handler: Callable[..., Mapping[str, Any]] | None = None,
        cancel_handler: Callable[..., Mapping[str, Any]] | None = None,
        allowed_project_id: str | None = None,
        allow_unleased_fixture: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.artifact_store = artifact_store
        self.profile_resolver = profile_resolver
        self.structure_normalizer = structure_normalizer
        self.commit_handler = commit_handler or (
            SQLAlchemyImportCommitter(
                artifact_store, profile_resolver=profile_resolver,
                ensure_structure=ensure_structure, normalize_structure=structure_normalizer,
            ) if ensure_structure is not None else None
        )
        self.cancel_handler = cancel_handler
        self.allowed_project_id = allowed_project_id
        self.allow_unleased_fixture = allow_unleased_fixture

    def handle(self, payload: Mapping[str, Any], *, event_type: str = "job.execute", lease_guard: Any = None) -> Mapping[str, Any]:
        required = {"job_id", "attempt", "dispatch_id"}
        if event_type not in {"job.execute", "job.cancel"} or set(payload) != required:
            raise ValueError("data-import task payload or event is invalid")
        if lease_guard is None and not self.allow_unleased_fixture:
            raise LeaseLostError("OUTBOX_LEASE_REQUIRED")
        if lease_guard is not None:
            lease_guard.check()
        if event_type == "job.cancel":
            return self._cancel(payload, lease_guard=lease_guard)
        try:
            return self._execute(payload, lease_guard=lease_guard)
        except Exception as exc:
            if isinstance(exc, LeaseLostError):
                raise
            try:
                self._project_failure(payload, lease_guard=lease_guard, cause=exc)
            except LeaseLostError:
                raise exc from None
            raise

    def _fence_delivery(
        self,
        session: Any,
        *,
        job: Job,
        payload: Mapping[str, Any],
        lease_guard: Any,
    ) -> None:
        """Lock and validate the current server-owned lease before projection."""
        if lease_guard is None:
            if self.allow_unleased_fixture:
                return
            raise LeaseLostError("OUTBOX_LEASE_REQUIRED")
        claim = getattr(lease_guard, "claim", None)
        dispatch = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.dispatch_id == payload.get("dispatch_id"),
                OutboxEvent.aggregate_type == "job",
                OutboxEvent.aggregate_id == job.id,
                OutboxEvent.event_type == "job.execute",
            ).with_for_update().execution_options(populate_existing=True)
        )
        # Claim expiry is an old snapshot after heartbeats; the locked DB row is authoritative.
        now = datetime.now(timezone.utc)
        lease_guard.check()
        if dispatch is None or claim is None:
            raise LeaseLostError("OUTBOX_LEASE_LOST")
        expires = dispatch.lease_expires_at
        if expires is not None:
            expires = expires.replace(tzinfo=timezone.utc) if expires.tzinfo is None else expires.astimezone(timezone.utc)
        expected = {
            "job_id": payload.get("job_id"),
            "attempt": payload.get("attempt"),
            "dispatch_id": payload.get("dispatch_id"),
        }
        try:
            valid = (
                claim.dispatch_id == dispatch.dispatch_id
                and claim.event_type == dispatch.event_type == "job.execute"
                and claim.aggregate_type == dispatch.aggregate_type == "job"
                and claim.aggregate_id == dispatch.aggregate_id == job.id
                and dict(claim.payload) == expected == dict(dispatch.payload or {})
                and claim.project_id == job.project_id
                and dict(claim.resource_ref) == dict(job.resource_ref or {})
                and isinstance(claim.lease_owner, str) and bool(claim.lease_owner)
                and claim.lease_owner == dispatch.lease_owner
                and isinstance(claim.lease_token, int) and not isinstance(claim.lease_token, bool)
                and claim.lease_token == dispatch.lease_token
                and dispatch.status == "PROCESSING"
                and expires is not None and expires > now
            )
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise LeaseLostError("OUTBOX_LEASE_LOST")

    def _project_failure(self, payload: Mapping[str, Any], *, lease_guard: Any, cause: Exception) -> None:
        """Fail the current import/job together, only while its delivery fence is live."""
        job_id, attempt, dispatch_id = payload["job_id"], payload["attempt"], payload["dispatch_id"]
        with self.session_factory() as session:
            with session.begin():
                observed_job = session.scalar(select(Job).where(Job.id == job_id))
                if observed_job is None or (
                    self.allowed_project_id is not None
                    and observed_job.project_id != self.allowed_project_id
                ):
                    return
                session.scalar(select(Project).where(Project.id == observed_job.project_id).with_for_update())
                job = session.scalar(select(Job).where(Job.id == job_id).with_for_update().execution_options(populate_existing=True))
                dispatch = session.scalar(select(OutboxEvent).where(
                    OutboxEvent.dispatch_id == dispatch_id,
                    OutboxEvent.aggregate_type == "job",
                    OutboxEvent.aggregate_id == job_id,
                    OutboxEvent.event_type == "job.execute",
                ).execution_options(populate_existing=True))
                expected = {"job_id": job_id, "attempt": attempt, "dispatch_id": dispatch_id}
                if (
                    job is None or (self.allowed_project_id is not None and job.project_id != self.allowed_project_id)
                    or dispatch is None or dispatch.payload != expected
                    or job.attempt != attempt or job.status not in {"QUEUED", "RUNNING"}
                    or job.kind not in {"data_import_validation", "data_import_commit"}
                ):
                    return
                if lease_guard is not None:
                    lease_guard.check()
                ref = job.resource_ref or {}
                if ref.get("type") != "data_import" or not isinstance(ref.get("id"), str):
                    return
                imported = session.scalar(select(DataImportRow).where(
                    DataImportRow.id == ref["id"], DataImportRow.project_id == job.project_id,
                ).with_for_update().execution_options(populate_existing=True))
                if imported is None or imported.job_id != job_id:
                    return
                expected_status = "VALIDATING" if job.kind == "data_import_validation" else "COMMITTING"
                if imported.status != expected_status:
                    return
                task_repo = SQLAlchemyTaskRepository(lambda: session)
                cause_chain = cause
                transient_cause = False
                while cause_chain is not None:
                    if isinstance(cause_chain, (OSError, TimeoutError, ConnectionError)):
                        transient_cause = True
                        break
                    cause_chain = cause_chain.__cause__
                retryable = job.kind == "data_import_commit" and transient_cause
                error = {
                    "code": "DATA_IMPORT_TEMPORARY_FAILURE" if retryable else "DATA_IMPORT_FAILED",
                    "message": "数据导入暂时失败，可重试同一任务。" if retryable else "数据导入任务失败；请修正问题后重新验证。",
                    "retryable": retryable,
                }
                self._fence_delivery(session, job=job, payload=payload, lease_guard=lease_guard)
                if job.status == "QUEUED":
                    started = sync_job_lifecycle(
                        task_repo, session, job_id=job_id, status=JobStatus.RUNNING,
                        attempt=attempt, expected_attempt=attempt, dispatch_id=dispatch_id,
                        expected_dispatch_id=dispatch_id, resource_ref=job.resource_ref,
                        started_at=datetime.now(timezone.utc),
                    )
                    if started is None:
                        return
                failed = sync_job_lifecycle(
                    task_repo, session, job_id=job_id, status=JobStatus.FAILED,
                    attempt=attempt, expected_attempt=attempt, dispatch_id=dispatch_id,
                    expected_dispatch_id=dispatch_id, resource_ref=job.resource_ref,
                    error=error, finished_at=datetime.now(timezone.utc),
                )
                if failed is None:
                    return
                failed.retryable = retryable
                imported.status = "FAILED"
                imported.updated_at = datetime.now(timezone.utc)

    def _execute(self, payload: Mapping[str, Any], *, lease_guard: Any) -> Mapping[str, Any]:
        job_id, attempt, dispatch_id = payload["job_id"], payload["attempt"], payload["dispatch_id"]
        with self.session_factory() as session:
            with session.begin():
                observed_job = session.scalar(select(Job).where(Job.id == job_id))
                if observed_job is None or (
                    self.allowed_project_id is not None
                    and observed_job.project_id != self.allowed_project_id
                ):
                    return {"status": "STALE"}
                session.scalar(select(Project).where(Project.id == observed_job.project_id).with_for_update())
                job = session.scalar(select(Job).where(Job.id == job_id).with_for_update().execution_options(populate_existing=True))
                dispatch = session.scalar(select(OutboxEvent).where(
                    OutboxEvent.dispatch_id == dispatch_id,
                    OutboxEvent.aggregate_type == "job",
                    OutboxEvent.aggregate_id == job_id,
                    OutboxEvent.event_type == "job.execute",
                ).execution_options(populate_existing=True))
                expected_dispatch = {"job_id": job_id, "attempt": attempt, "dispatch_id": dispatch_id}
                if (
                    job is None or (self.allowed_project_id is not None and job.project_id != self.allowed_project_id)
                    or dispatch is None or job.status not in {"QUEUED", "RUNNING"}
                    or job.attempt != attempt or dispatch.payload != expected_dispatch
                ):
                    return {"status": "STALE"}
                if lease_guard is not None:
                    lease_guard.check()
                if job.kind not in {"data_import_validation", "data_import_commit"}:
                    raise ValueError("data-import handler received an unsupported job kind")
                task_repo = SQLAlchemyTaskRepository(lambda: session)
                if sync_job_lifecycle(
                    task_repo, session, job_id=job_id, status=JobStatus.RUNNING,
                    attempt=attempt, expected_attempt=attempt, dispatch_id=dispatch_id,
                    expected_dispatch_id=dispatch_id, resource_ref=job.resource_ref,
                    started_at=datetime.now(timezone.utc),
                ) is None:
                    return {"status": "STALE"}
                session.flush()
                job = session.scalar(select(Job).where(Job.id == job_id).with_for_update().execution_options(populate_existing=True))
                ref = job.resource_ref or {}
                if ref.get("type") != "data_import" or not ref.get("id"):
                    raise DataConflictError("数据导入任务 resource_ref 无效")
                imported = session.scalar(select(DataImportRow).where(
                    DataImportRow.id == ref["id"], DataImportRow.project_id == job.project_id
                ).with_for_update().execution_options(populate_existing=True))
                if imported is None:
                    raise DataConflictError("数据导入任务目标不存在")
                if job.kind == "data_import_validation":
                    if imported.status != "VALIDATING" or imported.job_id != job_id:
                        return {"status": "STALE"}
                    session.scalar(select(Project).where(Project.id == job.project_id).with_for_update())
                    latest = session.scalar(select(DataVersionRow).where(
                        DataVersionRow.project_id == job.project_id,
                        DataVersionRow.domain_id == imported.domain_id,
                        DataVersionRow.status == "READY",
                    ).order_by(DataVersionRow.version.desc()).limit(1))
                    expected_parent = (imported.request_snapshot or {}).get("validation_parent_version_id")
                    expected_parent_sha = (imported.request_snapshot or {}).get("validation_parent_version_sha256")
                    if (latest.id if latest else None) != expected_parent or (latest.sha256 if latest else None) != expected_parent_sha:
                        raise DataConflictError("校验期间数据域父版本已变化，请重新校验")
                    service = self._service(session, job.owner_id)
                    validation = service.validate(job.project_id, imported.id, job_id=job.id)
                    self._fence_delivery(session, job=job, payload=payload, lease_guard=lease_guard)
                    ok = sync_job_lifecycle(
                        task_repo, session, job_id=job_id, status=JobStatus.SUCCEEDED,
                        attempt=attempt, expected_attempt=attempt, dispatch_id=dispatch_id,
                        expected_dispatch_id=dispatch_id,
                        resource_ref=job.resource_ref,
                        result_ref={"type": "data_import_validation", "id": validation.id},
                    )
                    if ok is None:
                        raise DataConflictError("数据导入校验任务 fencing 已过期")
                    return {"status": "SUCCEEDED", "validation_id": validation.id}
                if self.commit_handler is None:
                    raise DataUnavailableError("数据导入提交处理器未配置")
                if imported.job_id != job_id:
                    return {"status": "STALE"}
                if imported.status == "FAILED" and job.kind == "data_import_commit" and attempt > 1 and job.retryable:
                    frozen = (imported.request_snapshot or {}).get("commit_binding", {})
                    if frozen.get("job_id") != job_id or frozen.get("revision") != imported.revision or frozen.get("config_hash") != imported.config_hash:
                        return {"status": "STALE"}
                    imported.status = "COMMITTING"
                    imported.updated_at = datetime.now(timezone.utc)
                    session.flush()
                if imported.status != "COMMITTING":
                    return {"status": "STALE"}
                session.scalar(select(Project).where(Project.id == job.project_id).with_for_update())
                latest = session.scalar(select(DataVersionRow).where(
                    DataVersionRow.project_id == job.project_id,
                    DataVersionRow.domain_id == imported.domain_id,
                    DataVersionRow.status == "READY",
                ).order_by(DataVersionRow.version.desc()).limit(1))
                commit_binding = (imported.request_snapshot or {}).get("commit_binding", {})
                if (latest.id if latest else None) != commit_binding.get("parent_version_id"):
                    raise DataConflictError("提交时数据域父版本已变化，拒绝合并或覆盖")
                result = self.commit_handler(session=session, data_import=imported, job=job, payload=payload, lease_guard=lease_guard)
                self._fence_delivery(session, job=job, payload=payload, lease_guard=lease_guard)
                ok = sync_job_lifecycle(
                    task_repo, session, job_id=job_id, status=JobStatus.SUCCEEDED,
                    attempt=attempt, expected_attempt=attempt, dispatch_id=dispatch_id,
                    expected_dispatch_id=dispatch_id, resource_ref=job.resource_ref,
                    result_ref={"type": "data_version", "id": result["data_version_id"]},
                )
                if ok is None:
                    raise DataConflictError("数据导入提交任务 fencing 已过期")
                self._fence_delivery(session, job=job, payload=payload, lease_guard=lease_guard)
                return {"status": "SUCCEEDED", **dict(result)}

    def _service(self, session: Any, owner_id: str):
        from .service import DataImportService
        from .persistence import SQLAlchemyDataRepository, SQLAlchemyDataDomainRegistry

        artifact_port = SQLAlchemyImportArtifactPort(session, self.artifact_store)
        validation_port = CSVImportValidationPort(
            session, self.artifact_store, profile_resolver=self.profile_resolver,
            owner_id=owner_id, normalize_structure=self.structure_normalizer,
        )
        return DataImportService(
            SQLAlchemyDataRepository(session, parser_revision=validation_port.parser_revision, parser_hash=validation_port.parser_hash),
            SQLAlchemyDataDomainRegistry(session), validation_port=validation_port,
            # read_input itself requires the persisted AVAILABLE+PASSED state.
            artifact_port=artifact_port,
        )

    def _cancel(self, payload: Mapping[str, Any], *, lease_guard: Any = None) -> Mapping[str, Any]:
        if self.cancel_handler is None:
            raise DataUnavailableError("数据导入取消处理器未配置")
        if self.allowed_project_id is None:
            return self.cancel_handler(payload=payload, lease_guard=lease_guard)
        return self.cancel_handler(
            payload=payload, lease_guard=lease_guard,
            allowed_project_id=self.allowed_project_id,
        )


__all__ = ["CSVImportValidationPort", "DataImportWorker"]
