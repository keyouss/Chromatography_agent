"""Build-owned candidate schema for the author's historical TLC measurements.

These constructors do not register or activate the domain. Production use still
requires the separately reviewed data-only manifest and deployment seed path.
"""

from __future__ import annotations

from decimal import Decimal

from chrom_platform.modules.data_center.csv_import import NumericBounds, TrustedCsvProfile
from chrom_platform.modules.data_center.schemas import DataDomainSchema, DomainField

DOMAIN_ID = "tlc_author_measurement_v1"
SCHEMA_VERSION = "1"
PROFILE_ID = "tlc_author_measurement_v1"
PROJECT_ID = "project_data_accept_20261026"

SOURCE_ARTIFACT_RELATIVE_PATH = "Services/ML-Prediction-for-Rf-values-main/TLC_newdata/TLC_dataset_old.csv"
SOURCE_ARTIFACT_SHA256 = "35dfff1646780fcd431cd75ce72b9390ef993c9cfacbd2558d2cd4ff5417576f"
SOURCE_ARTIFACT_SIZE_BYTES = 463154
SOURCE_ENCODING = "gb18030"

# Domain field -> exact source header. Only these nine fields become record values.
SOURCE_COLUMN_MAPPING = {
    "measurement_id": "TLC_ID",
    "compound_id": "COMPOUND_ID",
    "smiles": "COMPOUND_SMILES",
    "hexane_ratio": "H",
    "ethyl_acetate_ratio": "EA",
    "dichloromethane_ratio": "DCM",
    "methanol_ratio": "MeOH",
    "diethyl_ether_ratio": "Et2O",
    "rf_observed": "Rf",
}

# These source-only columns are provenance on the original artifact, not record fields.
# `source_row_index` is the local index column; its CSV header is empty and therefore
# needs its own explicit parser/header policy before this original file can be admitted.
SOURCE_ONLY_COLUMNS = {
    "source_row_index": "",
    "compound_english_name": "COMPOUND_ENG_NAME",
    "compound_cas_number": "COMPOUND_CAS.NO",
    "class1": "CLASS1",
    "class2": "CLASS2",
    "class3": "CLASS3",
    "origin_image": "ORIGIN_IMG",
}

RATIO_UNIT_DEFINITION = "author_reported_ratio_basis_unknown"


def make_domain() -> DataDomainSchema:
    """Return a fresh candidate domain; this function has no registry side effect."""
    string_field = lambda: DomainField(type="string", required=True)
    number_field = lambda: DomainField(type="number", required=True)
    return DataDomainSchema(
        id=DOMAIN_ID,
        version=SCHEMA_VERSION,
        chromatography_type="tlc",
        record_schema={
            "measurement_id": string_field(),
            "compound_id": string_field(),
            "smiles": string_field(),
            "hexane_ratio": number_field(),
            "ethyl_acetate_ratio": number_field(),
            "dichloromethane_ratio": number_field(),
            "methanol_ratio": number_field(),
            "diethyl_ether_ratio": number_field(),
            "rf_observed": number_field(),
        },
        unit_definitions={
            "hexane_ratio": RATIO_UNIT_DEFINITION,
            "ethyl_acetate_ratio": RATIO_UNIT_DEFINITION,
            "dichloromethane_ratio": RATIO_UNIT_DEFINITION,
            "methanol_ratio": RATIO_UNIT_DEFINITION,
            "diethyl_ether_ratio": RATIO_UNIT_DEFINITION,
        },
        identity_fields=["measurement_id"],
        deduplication_policy="reject",
        supported_import_modes=["append"],
    )


def make_profile() -> TrustedCsvProfile:
    """Return the candidate strict-decoding profile without registering it."""
    return TrustedCsvProfile(
        profile_id=PROFILE_ID,
        domain_id=DOMAIN_ID,
        schema_version=SCHEMA_VERSION,
        encoding=SOURCE_ENCODING,
        field_bounds={"rf_observed": NumericBounds(minimum=Decimal("0"), maximum=Decimal("1"))},
        measurement_id_field="measurement_id",
        smiles_field="smiles",
    )


__all__ = [
    "DOMAIN_ID",
    "PROFILE_ID",
    "PROJECT_ID",
    "RATIO_UNIT_DEFINITION",
    "SCHEMA_VERSION",
    "SOURCE_ARTIFACT_RELATIVE_PATH",
    "SOURCE_ARTIFACT_SHA256",
    "SOURCE_ARTIFACT_SIZE_BYTES",
    "SOURCE_COLUMN_MAPPING",
    "SOURCE_ENCODING",
    "SOURCE_ONLY_COLUMNS",
    "make_domain",
    "make_profile",
]
