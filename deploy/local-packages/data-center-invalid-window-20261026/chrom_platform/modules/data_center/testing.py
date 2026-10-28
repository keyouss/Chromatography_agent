"""In-memory repository for domain-state tests only.

This is intentionally kept in a ``testing`` module and is not wired into the
application assembly.  Production deployments must provide a real repository
adapter with transaction and isolation guarantees.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .errors import DataConflictError
from .schemas import DataImport, DataImportValidation, DataVersion, DataVersionDraft, ExperimentRecord, FeatureVersion, Molecule


class InMemoryDataRepository:
    def __init__(self) -> None:
        self.data_versions: dict[str, DataVersion] = {}
        self.feature_versions: dict[str, FeatureVersion] = {}
        self.imports: dict[str, DataImport] = {}
        self.validations: dict[tuple[str, str | None], DataImportValidation] = {}
        self.molecules: dict[str, Molecule] = {}
        self.records: dict[str, ExperimentRecord] = {}
        self.data_version_references: set[tuple[str, str, str]] = set()

    def allocate_and_save_new_data_version(self, draft: DataVersionDraft) -> DataVersion:
        versions = self.list_data_versions(draft.project_id, draft.domain_id)
        version_no = max((item.version for item in versions), default=0) + 1
        value = DataVersion(
            id=f"dv_{len(self.data_versions) + 1}", project_id=draft.project_id,
            domain_id=draft.domain_id, version=version_no, sha256=draft.sha256,
            status="READY", record_count=draft.record_count,
            source_import_id=draft.source_import_id,
            snapshot_artifact_id=draft.snapshot_artifact_id,
            schema_version=draft.schema_version, quality_report_ref=draft.quality_report_ref,
            created_at=datetime.now(timezone.utc), referenced_by_count=0,
        )
        if any(item.version == version_no for item in versions):
            raise DataConflictError("数据版本号冲突")
        self.data_versions[value.id] = value
        return value

    def get_data_version(self, project_id: str, version_id: str) -> DataVersion | None:
        value = self.data_versions.get(version_id)
        return value if value is not None and value.project_id == project_id else None

    def get_data_version_any(self, project_ids, version_id: str) -> DataVersion | None:
        value = self.data_versions.get(version_id)
        return value if value is not None and value.project_id in project_ids else None

    def list_data_versions(self, project_id: str, domain_id: str, *, status: str | None = None) -> list[DataVersion]:
        return [value for value in self.data_versions.values() if value.project_id == project_id and value.domain_id == domain_id and (status is None or value.status == status)]

    def add_data_version_reference(self, project_id: str, version_id: str, reference_id: str) -> DataVersion:
        value = self.get_data_version(project_id, version_id)
        if value is None:
            return None  # type: ignore[return-value]
        if value.status == "ARCHIVED":
            raise DataConflictError("已归档的数据版本不能新增引用")
        key = (project_id, version_id, reference_id)
        if key not in self.data_version_references:
            self.data_version_references.add(key)
            self.data_versions[version_id] = value.model_copy(update={"referenced_by_count": value.referenced_by_count + 1})
        return self.data_versions[version_id]

    def archive_data_version(self, project_id: str, version_id: str) -> DataVersion:
        value = self.get_data_version(project_id, version_id)
        if value is None:
            return None  # type: ignore[return-value]
        if value.referenced_by_count:
            raise DataConflictError("已被训练或评测引用的数据版本不能归档")
        self.data_versions[version_id] = value.model_copy(update={"status": "ARCHIVED"})
        return self.data_versions[version_id]

    def save_feature_version(self, value: FeatureVersion) -> FeatureVersion:
        if value.id in self.feature_versions and self.feature_versions[value.id] != value:
            raise DataConflictError("FeatureVersion 不可覆盖")
        self.feature_versions[value.id] = value
        return value

    def get_feature_version(self, version_id: str) -> FeatureVersion | None:
        return self.feature_versions.get(version_id)

    def save_import(self, value: DataImport) -> DataImport:
        current = self.imports.get(value.id)
        if current is not None and current.project_id != value.project_id:
            raise DataConflictError("data_import 已属于其他项目")
        self.imports[value.id] = value
        return value

    def patch_import(
        self,
        value: DataImport,
        *,
        expected_status: str,
        expected_revision: int,
        expected_etag: str,
        expected_job_id: str | None,
    ) -> DataImport:
        current = self.imports.get(value.id)
        if current is None or current.project_id != value.project_id:
            raise DataConflictError("data_import 在修改期间已变化")
        if (
            current.status != expected_status
            or current.revision != expected_revision
            or current.etag != expected_etag
            or current.job_id != expected_job_id
        ):
            raise DataConflictError("data_import 在修改期间已变化")
        return self.save_import(value)

    def get_import(self, project_id: str, import_id: str) -> DataImport | None:
        value = self.imports.get(import_id)
        return value if value is not None and value.project_id == project_id else None

    def get_import_any(self, project_ids, import_id: str) -> DataImport | None:
        value = self.imports.get(import_id)
        return value if value is not None and value.project_id in project_ids else None

    def save_validation(self, project_id: str, value: DataImportValidation) -> DataImportValidation:
        self.validations[(project_id, value.import_id)] = value
        return value

    def get_validation(self, project_id: str, import_id: str) -> DataImportValidation | None:
        return self.validations.get((project_id, import_id))

    def save_molecule(self, value: Molecule) -> Molecule:
        self.molecules[value.id] = value
        return value

    def get_molecule(self, project_id: str, molecule_id: str) -> Molecule | None:
        value = self.molecules.get(molecule_id)
        return value if value is not None and value.project_id == project_id else None

    def save_record(self, value: ExperimentRecord) -> ExperimentRecord:
        molecule = self.molecules.get(value.molecule_id)
        if molecule is None or molecule.project_id != value.project_id:
            raise DataConflictError("实验记录项目与分子项目不一致")
        self.records[value.id] = value
        return value

    def list_records(self, project_id: str, molecule_id: str) -> list[ExperimentRecord]:
        return [value for value in self.records.values() if value.project_id == project_id and value.molecule_id == molecule_id]


__all__ = ["InMemoryDataRepository"]
