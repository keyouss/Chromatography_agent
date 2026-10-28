from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from chrom_platform.modules.data_center.schemas import (
    DataDomainSchema,
    DataImportCreate,
    DataImportPatch,
    DomainField,
    FeatureVersion,
)


def schema() -> DataDomainSchema:
    return DataDomainSchema(
        id="sfc-experiment",
        version="1.0",
        chromatography_type="sfc",
        record_schema={
            "sample_id": DomainField(type="string", required=True),
            "retention_time": DomainField(type="number", required=True, unit="min"),
        },
        unit_definitions={"retention_time": "min"},
        identity_fields=["sample_id"],
        deduplication_policy="update_existing",
        supported_import_modes=["append", "upsert"],
    )


def test_public_contracts_forbid_unknown_fields_and_user_schema():
    with pytest.raises(ValidationError):
        DataImportCreate(
            project_id="p1",
            domain_id="sfc-experiment",
            artifact_id="a1",
            schema_version="1.0",
            import_mode="append",
            field_mapping={"sample_id": "sample"},
            source_description="fixture",
            record_schema={"drop_me": "string"},
        )
    with pytest.raises(ValidationError):
        DataImportCreate(
            project_id="p1",
            domain_id="sfc-experiment",
            artifact_id="a1",
            schema_version="1.0",
            import_mode="append",
            field_mapping={"sample.foo": "sample"},
            source_description="fixture",
        )
    with pytest.raises(ValidationError):
        DataImportPatch()


def test_upload_column_names_are_exact_headers_but_reject_blank_or_del():
    base = dict(
        project_id="p1", domain_id="sfc-experiment", artifact_id="a1",
        schema_version="1.0", import_mode="append", source_description="fixture",
    )
    accepted = DataImportCreate(**base, field_mapping={"sample_id": "Sample / A.1"})
    assert accepted.field_mapping["sample_id"] == "Sample / A.1"
    with pytest.raises(ValidationError):
        DataImportCreate(**base, field_mapping={"sample_id": "   "})
    with pytest.raises(ValidationError):
        DataImportCreate(**base, field_mapping={"sample_id": "column\x7f"})


def test_feature_version_is_frozen_and_schema_is_strict():
    value = FeatureVersion(
        id="fv_sfc_1",
        version="1.0",
        task_types=["retention_time"],
        implementation_version="impl-1",
        schema={"name": "standard"},
        dependency_lock_hash="a" * 64,
        created_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )
    with pytest.raises(ValidationError):
        value.version = "2.0"
    with pytest.raises(ValidationError):
        FeatureVersion(**{**value.model_dump(), "unknown": True})
