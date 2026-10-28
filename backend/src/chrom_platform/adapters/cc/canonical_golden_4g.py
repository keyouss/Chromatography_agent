"""Independent canonical-input integration golden for the CC 4 g profile.

The values were produced by the author offline CLI from canonical inputs. This
fixture complements, and never replaces, the author raw-SMILES golden.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .preprocessing_profiles import (
    CC_4G_CANONICAL_PROFILE_ID,
    CC_4G_CANONICAL_PROFILE_SHA256,
)


CC_CANONICAL_GOLDEN_4G: dict[str, Any] = {
    "fixture_version": 1,
    "namespace": "cc-canonical-integration-4g",
    "profile_id": CC_4G_CANONICAL_PROFILE_ID,
    "profile_sha256": CC_4G_CANONICAL_PROFILE_SHA256,
    "reference_report_sha256": "30a0a48a53f9a56775e6d9010e922d486a480de8f39c433d2b1046ab213d9b60",
    "model_id": "qgeognn_silica_4g",
    "model_version": "cc-qgeognn-1",
    "input_schema_version": "1.0",
    "output_schema_version": "cc-qgeognn-1",
    "artifact_sha256": "d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99",
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
    "cases": [
        {
            "smiles": "Brc1ccc2ccccc2c1",
            "expected": [
                {"name": "V1", "volume_ml": {"q10": 3.5697362422943115, "q50": 4.157262802124023, "q90": 4.563072204589844}, "time_seconds": {"q10": 21.41841745376587, "q50": 24.94357681274414, "q90": 27.378433227539062}},
                {"name": "V2", "volume_ml": {"q10": 6.821381092071533, "q50": 8.36957836151123, "q90": 10.672107696533203}, "time_seconds": {"q10": 40.9282865524292, "q50": 50.21747016906738, "q90": 64.03264617919922}},
            ],
        },
        {
            "smiles": "COC(=O)c1ccc(Cl)cc1",
            "expected": [
                {"name": "V1", "volume_ml": {"q10": 3.408036947250366, "q50": 4.4719438552856445, "q90": 5.400983810424805}, "time_seconds": {"q10": 20.448221683502197, "q50": 26.831663131713867, "q90": 32.40590286254883}},
                {"name": "V2", "volume_ml": {"q10": 7.596822738647461, "q50": 9.781963348388672, "q90": 12.106624603271484}, "time_seconds": {"q10": 45.580936431884766, "q50": 58.69178009033203, "q90": 72.6397476196289}},
            ],
        },
    ],
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


CC_CANONICAL_GOLDEN_4G_SHA256 = "289f96fe84636720629edd7b0209b783d48152c01522f9dfc3a94a873aba2c45"


def verify_cc_canonical_golden(document: Mapping[str, Any] = CC_CANONICAL_GOLDEN_4G) -> bool:
    try:
        return hashlib.sha256(_canonical_json(document)).hexdigest() == CC_CANONICAL_GOLDEN_4G_SHA256
    except (TypeError, ValueError, OverflowError, UnicodeError):
        return False


__all__ = ["CC_CANONICAL_GOLDEN_4G", "CC_CANONICAL_GOLDEN_4G_SHA256", "verify_cc_canonical_golden"]
