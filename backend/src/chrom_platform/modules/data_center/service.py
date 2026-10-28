"""Domain services for immutable chromatography data.

The services are deliberately adapter-free.  They enforce state transitions
and scope, then call explicitly supplied ports for parsing, persistence,
commit, confirmation and archival.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable
from uuid import uuid4

from .errors import (
    ConfirmationRequiredError,
    DataConflictError,
    DataNotFoundError,
    DataUnavailableError,
    DataValidationError,
)
from .ports import (
    ArtifactAvailabilityPort,
    ArtifactScannerPort,
    AtomicDataCommitPort,
    DataDomainRegistry,
    DataRepository,
    ExportPort,
    PredictionArchivePort,
    PredictionRecordPort,
    PredictionReplaySource,
    ValidationPort,
)


_UNSET = object()
from .schemas import (
    ArchiveRequest,
    ConfirmationConsumeRequest,
    DataCommitRequest,
    DataDomainSchema,
    DataImport,
    DataImportCreate,
    DataImportPatch,
    DataImportValidation,
    DataVersion,
    DataVersionDraft,
    ExperimentRecord,
    ExportAccepted,
    ExportCreate,
    ExportView,
    FeatureVersion,
    Molecule,
    MoleculeCreate,
    PredictionDraftView,
    PredictionRecord,
    ValidationCounts,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _etag(config_hash: str, revision: int) -> str:
    return f'"{config_hash[:48]}-{revision}"'


class StaticDataDomainRegistry:
    """Trusted registry, normally populated by the build assembly.

    ``register`` rejects the default/user source explicitly.  A request DTO
    can select a domain, but cannot create or alter its database contract.
    """

    def __init__(self, schemas: Sequence[DataDomainSchema] = ()) -> None:
        self._schemas: dict[tuple[str, str], DataDomainSchema] = {}
        for schema in schemas:
            self.register(schema, source="built_in")

    def register(self, schema: DataDomainSchema, *, source: str = "user") -> DataDomainSchema:
        if source not in {"built_in", "deployment"}:
            raise DataValidationError("用户不能注册或修改数据域 schema")
        key = (schema.id, schema.version)
        if key in self._schemas:
            raise DataConflictError("数据域 schema 已注册", details={"domain_id": schema.id, "version": schema.version})
        self._schemas[key] = schema
        return schema

    def get(self, domain_id: str, schema_version: str | None = None) -> DataDomainSchema | None:
        candidates = [schema for (identifier, _), schema in self._schemas.items() if identifier == domain_id]
        if schema_version is not None:
            return next((schema for schema in candidates if schema.version == schema_version), None)
        return sorted(candidates, key=lambda item: item.version)[-1] if candidates else None

    def list(self) -> Sequence[DataDomainSchema]:
        return tuple(self._schemas.values())


class FeatureVersionService:
    """Registers immutable feature versions from the trusted build registry."""

    def __init__(self, repository: DataRepository, *, registry: Sequence[FeatureVersion] = ()) -> None:
        self._repository = repository
        self._known: dict[str, FeatureVersion] = {}
        for value in registry:
            self.register(value, source="built_in")

    def register(self, value: FeatureVersion, *, source: str = "user") -> FeatureVersion:
        if source not in {"built_in", "deployment"}:
            raise DataValidationError("FeatureVersion 只能来自已构建注册表")
        existing = self._repository.get_feature_version(value.id) or self._known.get(value.id)
        if existing is not None:
            if existing != value:
                raise DataConflictError("FeatureVersion 不可覆盖", details={"feature_version_id": value.id})
            return existing
        self._known[value.id] = value
        return self._repository.save_feature_version(value)

    def get(self, version_id: str) -> FeatureVersion:
        value = self._repository.get_feature_version(version_id) or self._known.get(version_id)
        if value is None:
            raise DataNotFoundError("FeatureVersion 不存在")
        return value


class DataVersionService:
    """Creates new immutable snapshots and tracks external references."""

    def __init__(self, repository: DataRepository, *, id_factory: Callable[[str], str] = _id) -> None:
        self._repository = repository
        self._id_factory = id_factory

    def publish(
        self,
        *,
        project_id: str,
        domain_id: str,
        source_import_id: str,
        snapshot_artifact_id: str,
        schema_version: str,
        sha256: str,
        record_count: int,
        quality_report_ref: Any = None,
    ) -> DataVersion:
        if not project_id or not domain_id or not source_import_id:
            raise DataValidationError("数据版本必须绑定项目、数据域和来源导入")
        return self._repository.allocate_and_save_new_data_version(DataVersionDraft(
            project_id=project_id,
            domain_id=domain_id,
            source_import_id=source_import_id,
            snapshot_artifact_id=snapshot_artifact_id,
            schema_version=schema_version,
            sha256=sha256,
            record_count=record_count,
            quality_report_ref=quality_report_ref,
        ))

    def get(self, project_id: str, version_id: str) -> DataVersion:
        value = self._repository.get_data_version(project_id, version_id)
        if value is None:
            raise DataNotFoundError("数据版本不存在")
        return value

    def list(self, project_id: str, domain_id: str, *, status: str | None = None, limit: int | None = None, after_id: str | None = None) -> Sequence[DataVersion]:
        reader = getattr(self._repository, "list_data_versions", None)
        if not callable(reader):
            raise DataUnavailableError("数据版本读取端口未配置")
        try:
            return reader(project_id, domain_id, status=status, limit=limit, after_id=after_id)
        except TypeError:
            return reader(project_id, domain_id, status=status)

    def reference(self, project_id: str, version_id: str, *, reference_id: str) -> DataVersion:
        self.get(project_id, version_id)
        if not reference_id:
            raise DataValidationError("引用标识不能为空")
        result = self._repository.add_data_version_reference(project_id, version_id, reference_id)
        if result is None:
            raise DataNotFoundError("数据版本不存在或不属于当前项目")
        return result

    def archive(self, project_id: str, version_id: str) -> DataVersion:
        self.get(project_id, version_id)
        result = self._repository.archive_data_version(project_id, version_id)
        if result is None:
            raise DataNotFoundError("数据版本不存在或不属于当前项目")
        return result

    def overwrite(self, project_id: str, version_id: str, **_: Any) -> DataVersion:
        value = self.get(project_id, version_id)
        raise DataConflictError(
            "DataVersion 不可覆盖；请发布新版本",
            details={"data_version_id": value.id, "referenced_by_count": value.referenced_by_count},
        )


class DataImportService:
    """Import draft/revision/validation/commit state machine."""

    def __init__(
        self,
        repository: DataRepository,
        domains: DataDomainRegistry,
        *,
        validation_port: ValidationPort | None = None,
        atomic_commit_port: AtomicDataCommitPort | None = None,
        artifact_port: ArtifactAvailabilityPort | None | object = _UNSET,
        scanner_port: ArtifactScannerPort | None | object = _UNSET,
        id_factory: Callable[[str], str] = _id,
    ) -> None:
        self._repository = repository
        self._domains = domains
        self._validation_port = validation_port
        self._atomic_commit_port = atomic_commit_port
        self._artifact_port = artifact_port
        self._scanner_port = scanner_port
        self._id_factory = id_factory

    def _domain(self, value: DataImportCreate | DataImport) -> DataDomainSchema:
        domain = self._domains.get(value.domain_id, value.schema_version)
        if domain is None:
            raise DataValidationError("数据域或 schema 版本不可用", details={"domain_id": value.domain_id})
        if value.schema_version != domain.version:
            raise DataValidationError("导入 schema_version 与受控数据域不匹配")
        return domain

    @staticmethod
    def _check_mapping(value: dict[str, str], domain: DataDomainSchema) -> None:
        unknown = set(value) - set(domain.record_schema)
        if unknown:
            raise DataValidationError("field_mapping 含有不属于数据域的目标字段", details={"fields": sorted(unknown)})
        required = {name for name, field in domain.record_schema.items() if field.required}
        missing = required - set(value)
        if missing:
            raise DataValidationError("field_mapping 缺少数据域必填目标字段", details={"fields": sorted(missing)})
        if len(set(value.values())) != len(value.values()):
            raise DataValidationError("field_mapping 不能重复使用上传文件列名")

    @staticmethod
    def _config(value: DataImportCreate | DataImport) -> dict[str, Any]:
        return {
            "project_id": value.project_id,
            "domain_id": value.domain_id,
            "artifact_id": value.artifact_id,
            "schema_version": value.schema_version,
            "import_mode": value.import_mode,
            "field_mapping": value.field_mapping,
            "source_description": value.source_description,
        }

    def create(self, request: DataImportCreate) -> DataImport:
        domain = self._domain(request)
        if request.import_mode != "append" or domain.deduplication_policy != "reject":
            raise DataValidationError("当前导入切片仅支持 append + reject", details={"import_mode": request.import_mode, "deduplication_policy": domain.deduplication_policy})
        if request.import_mode not in domain.supported_import_modes:
            raise DataValidationError("该数据域不支持此导入模式", details={"import_mode": request.import_mode})
        self._check_mapping(request.field_mapping, domain)
        config_hash = _hash(self._config(request))
        now = _now()
        value = DataImport(
            id=self._id_factory("di"),
            project_id=request.project_id,
            domain_id=request.domain_id,
            artifact_id=request.artifact_id,
            schema_version=request.schema_version,
            import_mode=request.import_mode,
            field_mapping=dict(request.field_mapping),
            source_description=request.source_description,
            revision=1,
            config_hash=config_hash,
            etag=_etag(config_hash, 1),
            status="DRAFT",
            request_snapshot=self._config(request),
            created_at=now,
            updated_at=now,
        )
        return self._repository.save_import(value)

    def get(self, project_id: str, import_id: str) -> DataImport:
        value = self._repository.get_import(project_id, import_id)
        if value is None:
            raise DataNotFoundError("数据导入不存在")
        return value

    @staticmethod
    def _assert_match(value: DataImport, *, expected_revision: int | None, if_match: str | None) -> None:
        if expected_revision is not None and value.revision != expected_revision:
            raise DataConflictError("数据导入 revision 已变化", details={"expected_revision": expected_revision, "revision": value.revision})
        if if_match is not None and value.etag != if_match:
            raise DataConflictError("数据导入 ETag 已变化")

    def patch(
        self,
        project_id: str,
        import_id: str,
        patch: DataImportPatch,
        *,
        expected_revision: int | None = None,
        if_match: str | None = None,
    ) -> DataImport:
        current = self.get(project_id, import_id)
        self._assert_match(current, expected_revision=expected_revision, if_match=if_match)
        if current.status not in {"DRAFT", "INVALID", "READY"}:
            raise DataConflictError("当前状态不可修改导入配置", details={"status": current.status})
        candidate = current.model_copy(update={
            key: value for key, value in {
                "schema_version": patch.schema_version,
                "import_mode": patch.import_mode,
                "field_mapping": patch.field_mapping,
                "source_description": patch.source_description,
            }.items() if value is not None
        })
        domain = self._domain(candidate)
        if candidate.import_mode != "append" or domain.deduplication_policy != "reject":
            raise DataValidationError("当前导入切片仅支持 append + reject", details={"import_mode": candidate.import_mode, "deduplication_policy": domain.deduplication_policy})
        if candidate.import_mode not in domain.supported_import_modes:
            raise DataValidationError("该数据域不支持此导入模式")
        self._check_mapping(candidate.field_mapping, domain)
        revision = current.revision + 1
        config_hash = _hash(self._config(candidate))
        updated = candidate.model_copy(update={
            "revision": revision,
            "config_hash": config_hash,
            "etag": _etag(config_hash, revision),
            "status": "DRAFT",
            "validation_ref": None,
            "new_data_version_id": None,
            "updated_at": _now(),
        })
        return self._repository.patch_import(
            updated,
            expected_status=current.status,
            expected_revision=current.revision,
            expected_etag=current.etag,
            expected_job_id=current.job_id,
        )

    def validate(self, project_id: str, import_id: str, *, job_id: str | None = None) -> DataImportValidation:
        current = self.get(project_id, import_id)
        if self._validation_port is None:
            raise DataUnavailableError("数据解析/校验端口未配置，不能伪造验证成功")
        # The default keeps the small domain-only service usable in unit
        # fixtures.  A production assembly opts into these explicit ports;
        # once opted in, silently skipping artifact/scanner checks is unsafe.
        if self._artifact_port is not _UNSET:
            if self._artifact_port is None or not callable(getattr(self._artifact_port, "ensure_available", None)):
                raise DataUnavailableError("制品可用性端口未配置，不能验证数据导入")
            try:
                available = self._artifact_port.ensure_available(current.project_id, current.artifact_id)
            except DataCenterError:
                raise
            except Exception as exc:
                raise DataUnavailableError("制品可用性检查暂不可用") from exc
            if available is False:
                raise DataUnavailableError("输入制品不可用，不能验证数据导入")
        if self._scanner_port is not _UNSET:
            if self._scanner_port is None or not callable(getattr(self._scanner_port, "ensure_scanned", None)):
                raise DataUnavailableError("制品扫描端口未配置，不能验证数据导入")
            try:
                scanned = self._scanner_port.ensure_scanned(current.project_id, current.artifact_id)
            except DataCenterError:
                raise
            except Exception as exc:
                raise DataUnavailableError("制品扫描检查暂不可用") from exc
            if scanned is False:
                raise DataUnavailableError("输入制品尚未通过扫描，不能验证数据导入")
        accepted_status = current.status in {"DRAFT", "INVALID", "READY"} or (
            current.status == "VALIDATING" and job_id is not None and current.job_id == job_id
        )
        if not accepted_status:
            raise DataConflictError("当前状态不可重新验证导入", details={"status": current.status})
        domain = self._domain(current)
        validating = current.model_copy(update={"status": "VALIDATING", "updated_at": _now()})
        self._repository.save_import(validating)
        result = self._validation_port.validate(validating, domain)
        if result.import_id != current.id:
            raise DataValidationError("校验端口返回了错误的 data_import")
        expected_parser_revision = getattr(self._validation_port, "parser_revision", None)
        expected_parser_hash = getattr(self._validation_port, "parser_hash", None)
        if expected_parser_revision is not None and result.parser_revision != expected_parser_revision:
            result = result.model_copy(update={"status": "STALE"})
        if expected_parser_hash is not None and result.parser_hash != expected_parser_hash:
            result = result.model_copy(update={"status": "STALE"})
        # The parser may have yielded while another request patched the
        # import.  Re-read the authoritative row before accepting the result.
        latest = self.get(project_id, import_id)
        if result.status == "STALE" or result.revision != latest.revision or result.config_hash != latest.config_hash:
            stale = result.model_copy(update={"status": "STALE"})
            self._repository.save_validation(project_id, stale)
            return stale
        self._repository.save_validation(project_id, result)
        status = "READY" if result.status == "VALID" else "INVALID"
        self._repository.save_import(latest.model_copy(update={"status": status, "validation_ref": result.id, "updated_at": _now()}))
        return result

    def validation(self, project_id: str, import_id: str) -> DataImportValidation:
        self.get(project_id, import_id)
        result = self._repository.get_validation(project_id, import_id)
        if result is None:
            raise DataNotFoundError("数据导入尚未产生验证结果")
        current = self.get(project_id, import_id)
        if result.revision != current.revision or result.config_hash != current.config_hash:
            return result.model_copy(update={"status": "STALE"})
        return result

    def commit(
        self,
        project_id: str,
        import_id: str,
        *,
        confirmation_id: str,
        actor_id: str | None = None,
        expected_revision: int | None = None,
        if_match: str | None = None,
    ) -> DataVersion:
        current = self.get(project_id, import_id)
        self._assert_match(current, expected_revision=expected_revision, if_match=if_match)
        if current.status == "COMMITTED" and current.new_data_version_id:
            value = self._repository.get_data_version(project_id, current.new_data_version_id)
            if value is not None:
                return value
            raise DataConflictError("导入已标记 COMMITTED，但关联数据版本缺失")
        validation = self.validation(project_id, import_id)
        if validation.status != "VALID" or validation.revision != current.revision:
            raise DataConflictError("导入验证结果已过期或未通过")
        if any((validation.counts.invalid, validation.counts.duplicate)):
            raise DataValidationError("存在无效或重复记录，不能提交")
        if not confirmation_id:
            raise ConfirmationRequiredError("数据导入提交需要 Confirmation")
        if self._atomic_commit_port is None:
            raise DataUnavailableError("数据提交端口未配置，不能伪造提交成功")
        domain = self._domain(current)
        confirmation = ConfirmationConsumeRequest(
            confirmation_id=confirmation_id,
            project_id=current.project_id,
            actor_id=actor_id,
            resource_ref={"type": "data_import", "id": current.id},
            action="data_import.commit",
            revision=current.revision,
            config_hash=current.config_hash,
        )
        committing = current.model_copy(update={"status": "COMMITTING", "updated_at": _now()})
        published = self._atomic_commit_port.commit(
            DataCommitRequest(data_import=committing, validation=validation, domain=domain), confirmation
        )
        if not isinstance(published, DataVersion):
            raise DataUnavailableError("数据提交端口未返回有效不可变版本")
        if published.project_id != current.project_id or published.domain_id != current.domain_id or published.source_import_id != current.id:
            raise DataValidationError("提交端口返回的版本绑定不一致")
        # AtomicDataCommitPort owns confirmation consumption, version insert,
        # and import update.  The returned row is only a committed result.
        return published


class MoleculeService:
    """Project-scoped molecules and chromatography record associations."""

    def __init__(self, repository: DataRepository, *, id_factory: Callable[[str], str] = _id) -> None:
        self._repository = repository
        self._id_factory = id_factory

    def create(self, request: MoleculeCreate, *, canonical_smiles: str, inchikey: str | None = None) -> Molecule:
        if not canonical_smiles:
            raise DataValidationError("canonical_smiles 不能为空")
        value = Molecule(
            id=self._id_factory("mol"),
            project_id=request.project_id,
            structure_id=request.structure_id,
            canonical_smiles=canonical_smiles,
            inchikey=inchikey,
            display_name=request.display_name,
            external_reference=request.external_reference,
            source=request.source,
            quality_status="UNKNOWN",
            created_at=_now(),
            record_count=0,
        )
        return self._repository.save_molecule(value)

    def add_record(self, record: ExperimentRecord) -> ExperimentRecord:
        molecule = self._repository.get_molecule(record.project_id, record.molecule_id)
        if molecule is None:
            raise DataNotFoundError("分子不存在或不属于当前项目")
        if record.chromatography_type not in ("tlc", "cc", "hplc", "gc", "sfc", "chiral_lc"):
            raise DataValidationError("色谱类型无效")
        return self._repository.save_record(record)

    def records(self, *, project_id: str, molecule_id: str) -> Sequence[ExperimentRecord]:
        molecule = self._repository.get_molecule(project_id, molecule_id)
        if molecule is None:
            raise DataNotFoundError("分子不存在或不属于当前项目")
        return self._repository.list_records(project_id, molecule_id)


class PredictionRecordService:
    """Port-only prediction record/replay/archive boundary."""

    def __init__(
        self,
        records: PredictionRecordPort,
        *,
        archive: PredictionArchivePort | None = None,
        replay_source: PredictionReplaySource | None = None,
        archive_confirmation: Callable[[str, Any, PredictionRecord, Any], None] | None = None,
    ) -> None:
        self._records = records
        self._archive = archive
        self._replay_source = replay_source
        self._archive_confirmation = archive_confirmation

    def get(self, project_id: str, record_id: str) -> PredictionRecord:
        record = self._records.get(project_id, record_id)
        if record is None:
            raise DataNotFoundError("预测记录不存在")
        return record

    def list(
        self,
        project_id: str,
        *,
        chromatography_type: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        after_id: str | None = None,
    ) -> Sequence[PredictionRecord]:
        reader = getattr(self._records, "list", None)
        if not callable(reader):
            raise DataUnavailableError("预测记录读取端口未配置")
        try:
            return reader(project_id, chromatography_type=chromatography_type, status=status, limit=limit, after_id=after_id)
        except TypeError:
            return reader(project_id, chromatography_type=chromatography_type, status=status)

    def replay_draft(self, *, project_id: str, record_id: str, actor_id: str) -> PredictionDraftView:
        record = self.get(project_id, record_id)
        if self._replay_source is None or not record.prediction_id:
            raise DataUnavailableError("预测记录缺少可验证的原始请求快照")
        source = self._replay_source.get_replay_source(project_id, record)
        if source is None:
            raise DataUnavailableError("预测记录缺少可验证的原始请求快照")
        if source.project_id != project_id or source.prediction_id != record.prediction_id:
            raise DataUnavailableError("原始预测快照项目或记录绑定不一致")
        if source.chromatography_type != record.chromatography_type:
            raise DataUnavailableError("原始预测快照类型与记录不一致")
        return self._records.create_replay_draft(record, source, actor_id=actor_id)

    def get_draft(self, *, project_id: str, draft_id: str) -> PredictionDraftView:
        getter = getattr(self._records, "get_replay_draft", None)
        if not callable(getter):
            raise DataUnavailableError("持久草稿读取端口未配置")
        value = getter(project_id, draft_id)
        if value is None:
            raise DataNotFoundError("预测草稿不存在")
        return value

    def archive_record(
        self,
        *,
        project_id: str,
        record_id: str,
        request: ArchiveRequest,
        actor: Any,
    ) -> PredictionRecord:
        if self._archive is None:
            raise DataUnavailableError("预测记录归档端口未配置")
        if actor is None or not getattr(actor, "actor_id", None):
            raise ConfirmationRequiredError("归档必须绑定已认证主体")
        if self._archive_confirmation is None:
            raise DataUnavailableError("归档 Confirmation 服务未配置")
        return self._archive.archive(
            project_id, record_id, confirmation_id=request.confirmation_id,
            actor=actor, consume_confirmation=self._archive_confirmation,
        )


class ExportService:
    """Record export API boundary; execution and storage remain worker ports."""

    def __init__(self, port: ExportPort | None = None) -> None:
        self._port = port

    def create(self, request: ExportCreate, *, actor: Any, request_id: str) -> ExportAccepted:
        if self._port is None:
            raise DataUnavailableError("导出端口未配置，不能伪造导出成功")
        create = getattr(self._port, "create", None)
        if not callable(create):
            raise DataUnavailableError("导出端口未配置，不能伪造导出成功")
        return create(request, actor=actor, request_id=request_id)

    def get(self, export_id: str, *, actor: Any) -> ExportView:
        if self._port is None:
            raise DataUnavailableError("导出端口未配置")
        reader = getattr(self._port, "get", None)
        if not callable(reader):
            raise DataUnavailableError("导出读取端口未配置")
        return reader(export_id, actor=actor)


__all__ = [
    "DataImportService",
    "DataVersionService",
    "ExportService",
    "FeatureVersionService",
    "MoleculeService",
    "PredictionRecordService",
    "StaticDataDomainRegistry",
]
