from decimal import Decimal

from chrom_platform.modules.data_center.service import StaticDataDomainRegistry
from chrom_platform.modules.data_center.tlc_author_domain import (
    DOMAIN_ID,
    PROFILE_ID,
    PROJECT_ID,
    RATIO_UNIT_DEFINITION,
    SCHEMA_VERSION,
    SOURCE_ARTIFACT_SHA256,
    SOURCE_ARTIFACT_SIZE_BYTES,
    SOURCE_COLUMN_MAPPING,
    SOURCE_ENCODING,
    SOURCE_ONLY_COLUMNS,
    make_domain,
    make_profile,
)


def test_candidate_domain_contains_only_approved_measurement_schema():
    domain = make_domain()

    assert (DOMAIN_ID, SCHEMA_VERSION, PROJECT_ID) == (
        "tlc_author_measurement_v1", "1", "project_data_accept_20261026",
    )
    assert domain.id == DOMAIN_ID and domain.version == SCHEMA_VERSION
    assert domain.chromatography_type == "tlc"
    assert domain.identity_fields == ["measurement_id"]
    assert domain.deduplication_policy == "reject"
    assert domain.supported_import_modes == ["append"]
    assert set(domain.record_schema) == set(SOURCE_COLUMN_MAPPING)
    assert domain.record_schema["measurement_id"].type == "string"
    assert domain.record_schema["compound_id"].type == "string"
    assert "compound_id" not in domain.identity_fields
    assert domain.record_schema["smiles"].type == "string"
    assert all(
        domain.record_schema[field].type == "number"
        for field in (
            "hexane_ratio", "ethyl_acetate_ratio", "dichloromethane_ratio",
            "methanol_ratio", "diethyl_ether_ratio", "rf_observed",
        )
    )
    assert set(domain.unit_definitions.values()) == {RATIO_UNIT_DEFINITION}
    assert "rf_observed" not in domain.unit_definitions


def test_profile_is_strict_and_bounds_only_rf_without_ratio_normalization():
    profile = make_profile()

    assert profile.profile_id == PROFILE_ID == DOMAIN_ID
    assert profile.domain_id == DOMAIN_ID
    assert profile.schema_version == SCHEMA_VERSION
    assert profile.encoding == SOURCE_ENCODING == "gb18030"
    assert profile.measurement_id_field == "measurement_id"
    assert profile.smiles_field == "smiles"
    assert set(profile.field_bounds) == {"rf_observed"}
    assert profile.field_bounds["rf_observed"].minimum == Decimal("0")
    assert profile.field_bounds["rf_observed"].maximum == Decimal("1")
    assert not any("ratio" in field for field in profile.field_bounds)


def test_candidate_constructors_do_not_activate_registry_and_bind_source_identity():
    registry = StaticDataDomainRegistry()

    make_domain()
    make_profile()

    assert registry.list() == ()
    assert SOURCE_ARTIFACT_SIZE_BYTES == 463154
    assert SOURCE_ARTIFACT_SHA256 == "35dfff1646780fcd431cd75ce72b9390ef993c9cfacbd2558d2cd4ff5417576f"
    assert SOURCE_COLUMN_MAPPING["measurement_id"] == "TLC_ID"
    assert SOURCE_COLUMN_MAPPING["compound_id"] == "COMPOUND_ID"
    assert SOURCE_ONLY_COLUMNS["source_row_index"] == ""
    assert "source_row_index" not in make_domain().record_schema
