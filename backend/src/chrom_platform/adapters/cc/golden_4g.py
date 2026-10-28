"""Author-entry replay golden for two distinct, same-condition 4 g compounds."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


CC_GOLDEN_4G: dict[str, Any] = {
    "fixture_version": 1,
    "namespace": "cc-author-4g",
    "catalog_revision": "v1",
    "model_id": "qgeognn_silica_4g",
    "model_version": "cc-qgeognn-1",
    "input_schema_version": "1.0",
    "output_schema_version": "cc-qgeognn-1",
    "artifact_sha256": "d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99",
    "cases": [
        {
            "source_row": 2288,
            "source_record": {
                "CAS": "580-13-2", "name": "2-Bromonaphthalene", "purity": 0.98,
                "density_g_ml": 1.0, "sample_volume_ul": 100, "loading_solvent": "DCM",
                "loading_solvent_volume_ul": 300, "PE_EA": "0/1", "flow_rate_ml_min": 10,
                "ending_time": "2023-09-05_17-50-18.xlsx", "column_specs": "Silica-CS 4g",
                "t1": 492, "t2": 1033, "smiles": "C1=CC=C2C=C(C=CC2=C1)Br",
                "verified_t1": 492, "verified_t2": 1033,
            },
            "name": "2-Bromonaphthalene",
            "smiles": "C1=CC=C2C=C(C=CC2=C1)Br",
            "request": {
                "eluent_system_id": "cc4g-pe-ea-000-100-v1",
                "loading_solvent_id": "cc4g-loading-dcm-v1",
                "sample_mass_mg": 100.0,
                "loading_solvent_volume_ul": 300.0,
                "flow_rate_ml_min": 10.0,
                "column_spec_id": "silica-4g",
            },
            "author_input": {
                "mobile_phase": {"petroleum_ether": 0.0, "ethyl_acetate": 1.0},
                "loading_solvent": "DCM",
                "density_g_ml": 1.0,
                "sample_volume_ul": 100.0,
                "loading_solvent_volume_ul": 300.0,
                "flow_rate_ml_min": 10.0,
                "column_spec": "4g",
            },
            "expected": [
                {"name": "V1", "volume_ml": {"q10": 3.5697367191314697, "q50": 4.157267093658447, "q90": 4.563084125518799}, "time_seconds": {"q10": 21.41842031478882, "q50": 24.943602561950684, "q90": 27.378504753112793}},
                {"name": "V2", "volume_ml": {"q10": 6.821397304534912, "q50": 8.369595527648926, "q90": 10.672127723693848}, "time_seconds": {"q10": 40.92838382720947, "q50": 50.217573165893555, "q90": 64.03276634216309}},
            ],
        },
        {
            "source_row": 2535,
            "source_record": {
                "CAS": "1126-46-1", "name": "METHYL 4-CHLOROBENZOATE", "purity": 0.9995,
                "density_g_ml": 1.0, "sample_volume_ul": 100, "loading_solvent": "DCM",
                "loading_solvent_volume_ul": 300, "PE_EA": "0/1", "flow_rate_ml_min": 10,
                "ending_time": "2023-09-12_09-54-40.xlsx", "column_specs": "Silica-CS 4g",
                "t1": 496, "t2": 928, "smiles": "COC(=O)C1=CC=C(C=C1)Cl",
                "verified_t1": 496, "verified_t2": 928,
            },
            "name": "METHYL 4-CHLOROBENZOATE",
            "smiles": "COC(=O)C1=CC=C(C=C1)Cl",
            "request": {
                "eluent_system_id": "cc4g-pe-ea-000-100-v1",
                "loading_solvent_id": "cc4g-loading-dcm-v1",
                "sample_mass_mg": 100.0,
                "loading_solvent_volume_ul": 300.0,
                "flow_rate_ml_min": 10.0,
                "column_spec_id": "silica-4g",
            },
            "author_input": {
                "mobile_phase": {"petroleum_ether": 0.0, "ethyl_acetate": 1.0},
                "loading_solvent": "DCM",
                "density_g_ml": 1.0,
                "sample_volume_ul": 100.0,
                "loading_solvent_volume_ul": 300.0,
                "flow_rate_ml_min": 10.0,
                "column_spec": "4g",
            },
            "expected": [
                {"name": "V1", "volume_ml": {"q10": 3.408053159713745, "q50": 4.471970558166504, "q90": 5.400991439819336}, "time_seconds": {"q10": 20.44831895828247, "q50": 26.831823348999023, "q90": 32.405948638916016}},
                {"name": "V2", "volume_ml": {"q10": 7.596765995025635, "q50": 9.781908988952637, "q90": 12.106593132019043}, "time_seconds": {"q10": 45.58059597015381, "q50": 58.69145393371582, "q90": 72.63955879211426}},
            ],
        },
    ],
}

CC_GOLDEN_4G_SHA256 = "3fd4854665c4011693c3db2e87e93b9815342565e6e9d721dcd66477fc075a19"


def verify_golden_digest(document: Mapping[str, Any] = CC_GOLDEN_4G) -> bool:
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest() == CC_GOLDEN_4G_SHA256
