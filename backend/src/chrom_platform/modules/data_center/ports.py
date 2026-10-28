"""Persistence and execution ports for the data-center domain."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .schemas import (
    ConfirmationConsumeRequest,
    DataCommitRequest,
    DataDomainSchema,
    DataImport,
    DataImportValidation,
    DataVersion,
    DataVersionDraft,
    ExperimentRecord,
    ExportAccepted,
    ExportCreate,
    ExportView,
    FeatureVersion,
    Molecule,
    PredictionDraftView,
    PredictionRecord,
)


class DataDomainRegistry(Protocol):
    def get(self, domain_id: str, schema_version: str | None = None) -> DataDomainSchema | None: ...
    def list(self) -> Sequence[DataDomainSchema]: ...


class DataRepository(Protocol):
    """Repository methods always carry project scope and immutable semantics."""

    def allocate_and_save_new_data_version(self, draft: DataVersionDraft, *, version_id: str | None = None) -> DataVersion: ...
    def get_data_version(self, project_id: str, version_id: str) -> DataVersion | None: ...
    def list_data_versions(self, project_id: str, domain_id: str) -> Sequence[DataVersion]: ...
    # The real repository must enforce a unique (project, version, reference)
    # key and atomically return the resulting reference count.
    def add_data_version_reference(self, project_id: str, version_id: str, reference_id: str) -> DataVersion: ...
    def archive_data_version(self, project_id: str, version_id: str) -> DataVersion: ...
    def save_feature_version(self, value: FeatureVersion) -> FeatureVersion: ...
    def get_feature_version(self, version_id: str) -> FeatureVersion | None: ...
    def save_import(self, value: DataImport) -> DataImport: ...
    def patch_import(
        self,
        value: DataImport,
        *,
        expected_status: str,
        expected_revision: int,
        expected_etag: str,
        expected_job_id: str | None,
    ) -> DataImport: ...
    def get_import(self, project_id: str, import_id: str) -> DataImport | None: ...
    def save_validation(self, project_id: str, value: DataImportValidation) -> DataImportValidation: ...
    def get_validation(self, project_id: str, import_id: str) -> DataImportValidation | None: ...
    def save_molecule(self, value: Molecule) -> Molecule: ...
    def get_molecule(self, project_id: str, molecule_id: str) -> Molecule | None: ...
    def save_record(self, value: ExperimentRecord) -> ExperimentRecord: ...
    def list_records(self, project_id: str, molecule_id: str) -> Sequence[ExperimentRecord]: ...


class ValidationPort(Protocol):
    def validate(self, data_import: DataImport, domain: DataDomainSchema) -> DataImportValidation: ...


class ArtifactAvailabilityPort(Protocol):
    """Checks that an input artifact is complete and readable."""

    def ensure_available(self, project_id: str, artifact_id: str) -> object: ...


class ArtifactScannerPort(Protocol):
    """Checks the uploaded artifact before a parser consumes it."""

    def ensure_scanned(self, project_id: str, artifact_id: str) -> object: ...


class AtomicDataCommitPort(Protocol):
    """Consumes confirmation and publishes import/version in one transaction."""

    def commit(self, request: DataCommitRequest, confirmation: ConfirmationConsumeRequest) -> DataVersion: ...


class PredictionRecordPort(Protocol):
    def get(self, project_id: str, record_id: str) -> PredictionRecord | None: ...
    def create_replay_draft(self, record: PredictionRecord, source: "PredictionReplaySourceData", *, actor_id: str) -> PredictionDraftView: ...
    def get_replay_draft(self, project_id: str, draft_id: str) -> PredictionDraftView | None: ...


@dataclass(frozen=True)
class PredictionReplaySourceData:
    """Prediction-owned, validated source fields needed to replay a record."""

    prediction_id: str
    project_id: str
    chromatography_type: str
    tool: str
    schema_version: str
    structure_refs: list[str]
    structure_snapshots: list[dict[str, Any]]
    modifier_structure_snapshots: list[dict[str, Any]]
    input: dict[str, Any]
    model_selection: dict[str, Any]
    model_snapshot: dict[str, Any]


class PredictionReplaySource(Protocol):
    """Public boundary to the prediction module's immutable execution source."""

    def get_replay_source(self, project_id: str, record: PredictionRecord) -> PredictionReplaySourceData | None: ...


@dataclass(frozen=True)
class PredictionRecordDraft:
    """Cross-module write DTO; callers never pass the data-center ORM row."""

    id: str
    prediction_id: str | None
    batch_id: str | None
    project_id: str
    source: str
    actor_id: str
    chromatography_type: str
    model_snapshot: dict[str, Any]
    input_snapshot: dict[str, Any]
    result_ref: dict[str, Any] | None
    warnings: list[str]
    status: str
    created_at: datetime
    artifact_refs: list[dict[str, Any]]
    revision: int
    etag: str


class PredictionRecordStagingPort(Protocol):
    """Stage/update the canonical record in a caller-owned session."""

    def stage_prediction_record(self, draft: PredictionRecordDraft, *, session: Any) -> None: ...

    def update_prediction_record(
        self,
        record_id: str,
        *,
        status: str,
        result_ref: dict[str, Any] | None = None,
        session: Any,
    ) -> None: ...


def default_prediction_record_staging_port() -> PredictionRecordStagingPort:
    """Return the SQL adapter behind the public staging boundary.

    The import stays lazy so prediction depends on this port, while the
    canonical ORM declaration remains owned by data-center.
    """

    from .persistence import SQLAlchemyPredictionRecordWriter

    return SQLAlchemyPredictionRecordWriter()


class PredictionArchivePort(Protocol):
    def archive(
        self,
        project_id: str,
        record_id: str,
        *,
        confirmation_id: str,
        actor: Any,
        consume_confirmation: Any,
    ) -> PredictionRecord: ...


class ExportPort(Protocol):
    def create(self, request: ExportCreate, *, actor: Any, request_id: str) -> ExportAccepted: ...
    def get(self, export_id: str, *, actor: Any) -> ExportView: ...


__all__ = [
    "AtomicDataCommitPort",
    "ArtifactAvailabilityPort",
    "ArtifactScannerPort",
    "DataDomainRegistry",
    "DataRepository",
    "ExportPort",
    "PredictionArchivePort",
    "PredictionRecordPort",
    "PredictionRecordDraft",
    "PredictionRecordStagingPort",
    "PredictionReplaySource",
    "PredictionReplaySourceData",
    "default_prediction_record_staging_port",
    "ValidationPort",
]
