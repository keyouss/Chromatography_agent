from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import TypeAdapter

from chrom_platform.modules.data_center.csv_import import (
    MAX_ISSUES,
    NumericBounds,
    TrustedCsvProfile,
    make_identity_key,
    parse_csv_import,
)
from chrom_platform.modules.data_center.schemas import DataDomainSchema, DataImport, DomainField


def _domain(*, deduplication_policy="reject") -> DataDomainSchema:
    # Synthetic parser fixture only; it is not a production TLC manifest.
    return DataDomainSchema(
        id="fixture-tlc-measurements",
        version="fixture-1",
        chromatography_type="tlc",
        record_schema={
            "measurement_id": DomainField(type="string", required=True),
            "smiles": DomainField(type="string", required=True),
            "rf": DomainField(type="number", required=True),
        },
        unit_definitions={},
        identity_fields=["measurement_id"],
        deduplication_policy=deduplication_policy,
        supported_import_modes=["append", "upsert"],
    )


def _import(*, import_mode="append") -> DataImport:
    now = datetime.now(timezone.utc)
    return DataImport(
        id="import_fixture",
        project_id="project_fixture",
        domain_id="fixture-tlc-measurements",
        artifact_id="artifact_fixture",
        schema_version="fixture-1",
        import_mode=import_mode,
        field_mapping={"measurement_id": "measurement_id", "smiles": "smiles", "rf": "rf"},
        source_description="synthetic unit-test fixture",
        revision=3,
        config_hash="a" * 64,
        etag='"fixture-3"',
        status="DRAFT",
        created_at=now,
        updated_at=now,
    )


def _profile(**updates) -> TrustedCsvProfile:
    values = {
        "profile_id": "fixture-only",
        "domain_id": "fixture-tlc-measurements",
        "schema_version": "fixture-1",
        "encoding": "utf-8-sig",
        "field_bounds": {"rf": NumericBounds(minimum=Decimal("0"), maximum=Decimal("1"))},
        "measurement_id_field": "measurement_id",
        "smiles_field": "smiles",
    }
    values.update(updates)
    return TrustedCsvProfile(**values)


def test_valid_csv_binds_original_bytes_ignores_unmapped_columns_and_preserves_structure_payload():
    content = "measurement_id,smiles,rf,unmapped\nm-1,N[C@@H](C)O,0.42,secret-extra\n".encode("utf-8")
    seen = []

    # Test double only: production stereochemistry semantics belong to the supplied normalizer.
    def normalize(smiles):
        seen.append(smiles)
        return {"normalized_input": smiles, "stereo_preserved": "@@" in smiles}

    result = parse_csv_import(content, _import(), _domain(), _profile(), normalize)

    assert result.report.status == "VALID"
    assert result.report.source_sha256 == hashlib.sha256(content).hexdigest()
    assert result.report.source_artifact_id == "artifact_fixture"
    assert result.report.revision == 3
    assert result.report.counts.new == 1
    assert result.rows[0].source_row_number == 2
    assert result.rows[0].normalized_values == {
        "measurement_id": "m-1", "smiles": "N[C@@H](C)O", "rf": Decimal("0.42"),
    }
    assert result.rows[0].normalized_structure == {"normalized_input": "N[C@@H](C)O", "stereo_preserved": True}
    assert seen == ["N[C@@H](C)O"]
    assert "unmapped" not in result.rows[0].normalized_values
    assert "secret-extra" not in result.report.model_dump_json()
    assert len(result.report.to_json_bytes()) <= 2 * 1024 * 1024


def test_out_of_bounds_value_is_invalid_and_report_retains_raw_value_without_clipping():
    content = b"measurement_id,smiles,rf\nm-1,CC,1.2\n"

    result = parse_csv_import(content, _import(), _domain(), _profile(), lambda smiles: {"input": smiles})

    assert result.report.status == "INVALID"
    assert result.report.counts.invalid == 1
    assert result.rows == ()
    issue = next(issue for issue in result.report.issues if issue.code == "VALUE_OUT_OF_BOUNDS")
    assert issue.field == "rf"
    assert issue.measurement_id == "m-1"
    assert issue.raw_value_preview == "1.2"
    assert issue.raw_value_sha256 == hashlib.sha256(b"1.2").hexdigest()
    assert "1.2" in result.report.model_dump_json()


def test_optional_identity_field_must_still_be_nonempty():
    domain = _domain().model_copy(update={
        "record_schema": {
            **_domain().record_schema,
            "measurement_id": DomainField(type="string", required=False),
        },
    })
    content = b"measurement_id,smiles,rf\n,CC,0.5\n"

    result = parse_csv_import(content, _import(), domain, _profile(), lambda smiles: {"input": smiles})

    assert result.report.status == "INVALID"
    assert result.report.counts.invalid == 1
    assert result.rows == ()
    assert "IDENTITY_VALUE_MISSING" in {issue.code for issue in result.report.issues}


def test_decimal_identity_key_treats_equivalent_numeric_values_as_duplicate():
    domain = _domain().model_copy(update={
        "record_schema": {
            "measurement_id": DomainField(type="number", required=True),
            "value": DomainField(type="string", required=True),
        },
    })
    data_import = _import().model_copy(update={
        "field_mapping": {"measurement_id": "id", "value": "value"},
    })
    profile = _profile(measurement_id_field=None, smiles_field=None, field_bounds={})
    content = b"id,value\n1.0,a\n1,b\n"

    result = parse_csv_import(content, data_import, domain, profile)

    assert result.report.status == "INVALID"
    assert result.report.counts.new == 1
    assert result.report.counts.duplicate == 1
    assert len(result.rows) == 1
    assert "DUPLICATE_IDENTITY" in {issue.code for issue in result.report.issues}


def test_numeric_identity_survives_decimal_json_string_roundtrip_and_blocks_reimport():
    domain = _domain().model_copy(update={
        "record_schema": {
            "measurement_id": DomainField(type="number", required=True),
            "value": DomainField(type="string", required=True),
        },
    })
    data_import = _import().model_copy(update={
        "field_mapping": {"measurement_id": "id", "value": "value"},
    })
    profile = _profile(measurement_id_field=None, smiles_field=None, field_bounds={})
    decimal_values = {"measurement_id": Decimal("1.00")}
    encoded = TypeAdapter(dict[str, object]).dump_json(decimal_values)
    restored = TypeAdapter(dict[str, object]).validate_json(encoded)
    assert restored["measurement_id"] == "1.00"

    persisted_key = make_identity_key(domain, restored)
    imported = parse_csv_import(
        b"id,value\n1,b\n", data_import, domain, profile,
        existing_identity_keys={persisted_key},
    )
    assert imported.report.counts.duplicate == 1
    assert imported.rows == ()

    string_domain = _domain()
    assert make_identity_key(string_domain, {"measurement_id": "1"}) != persisted_key


def test_missing_profile_and_unsupported_modes_fail_closed():
    content = b"measurement_id,smiles,rf\nm-1,CC,0.5\n"
    missing = parse_csv_import(content, _import(), _domain(), None)
    assert missing.report.status == "UNAVAILABLE"
    assert missing.report.issues[0].code == "TRUSTED_PROFILE_REQUIRED"

    upsert = parse_csv_import(content, _import(import_mode="upsert"), _domain(), _profile())
    assert upsert.report.status == "UNSUPPORTED_IMPORT_MODE"
    assert upsert.report.issues[0].code == "UNSUPPORTED_IMPORT_MODE"

    update_existing = parse_csv_import(content, _import(), _domain(deduplication_policy="update_existing"), _profile())
    assert update_existing.report.status == "UNSUPPORTED_IMPORT_MODE"


def test_strict_decode_duplicate_identity_headers_and_row_width_are_reported():
    invalid_encoding = parse_csv_import(
        "measurement_id,smiles,rf\nm-ñ,CC,0.5\n".encode("cp1252"),
        _import(), _domain(), _profile(encoding="utf-8"), lambda smiles: {"input": smiles},
    )
    assert invalid_encoding.report.status == "INVALID"
    assert invalid_encoding.report.issues[0].code == "ENCODING_ERROR"

    duplicate_header = parse_csv_import(
        b"measurement_id,smiles,rf,rf\nm-1,CC,0.5,0.5\n", _import(), _domain(), _profile(), lambda smiles: {"input": smiles},
    )
    assert "DUPLICATE_HEADER" in {issue.code for issue in duplicate_header.report.issues}
    assert duplicate_header.rows == ()

    domain = _domain()
    already_seen = make_identity_key(domain, {"measurement_id": "m-1"})
    duplicate = parse_csv_import(
        b"measurement_id,smiles,rf\nm-1,CC,0.5\nm-2,CCC\n",
        _import(), domain, _profile(), lambda smiles: {"input": smiles}, existing_identity_keys={already_seen},
    )
    codes = {issue.code for issue in duplicate.report.issues}
    assert duplicate.report.counts.duplicate == 1
    assert duplicate.report.counts.invalid == 1
    assert "DUPLICATE_IDENTITY" in codes
    assert "ROW_WIDTH_MISMATCH" in codes

    non_finite = parse_csv_import(
        b"measurement_id,smiles,rf\nm-3,CC,NaN\n",
        _import(), _domain(), _profile(), lambda smiles: {"input": smiles},
    )
    assert "NON_FINITE_NUMBER" in {issue.code for issue in non_finite.report.issues}


def test_issue_cap_is_explicit_and_serialized_report_stays_bounded():
    rows = [f"m-{index},CC,2" for index in range(MAX_ISSUES + 1)]
    content = ("measurement_id,smiles,rf\n" + "\n".join(rows) + "\n").encode("utf-8")

    result = parse_csv_import(content, _import(), _domain(), _profile(), lambda smiles: {"input": smiles})

    assert result.report.status == "INVALID"
    assert result.report.total_issue_count == MAX_ISSUES + 1
    assert len(result.report.issues) == MAX_ISSUES
    assert result.report.issues_truncated is True
    assert result.report.to_json_bytes().startswith(b"{")
    assert len(result.report.to_json_bytes()) <= 2 * 1024 * 1024


def test_csv_byte_and_column_limits_are_explicit():
    oversized = parse_csv_import(b"x" * (16 * 1024 * 1024 + 1), _import(), _domain(), _profile())
    assert oversized.report.issues[0].code == "CSV_TOO_LARGE"

    headers = [f"column_{index}" for index in range(65)]
    too_many_columns = parse_csv_import(
        (",".join(headers) + "\n").encode("utf-8"), _import(), _domain(), _profile(), lambda smiles: {"input": smiles},
    )
    assert "TOO_MANY_COLUMNS" in {issue.code for issue in too_many_columns.report.issues}

    rows = [f"m-{index},CC,0.5" for index in range(10_001)]
    too_many_records = parse_csv_import(
        ("measurement_id,smiles,rf\n" + "\n".join(rows) + "\n").encode("utf-8"),
        _import(), _domain(), _profile(), lambda smiles: {"input": smiles},
    )
    assert too_many_records.report.row_count == 10_001
    assert too_many_records.report.counts.new == 10_000
    assert "TOO_MANY_RECORDS" in {issue.code for issue in too_many_records.report.issues}
