"""Immutable preprocessing identity for the CC 4 g canonical integration path.

This profile binds the deployed weights to the exact canonical input policy,
the author graph feature implementation, scaler metadata and scientific runtime
versions.  It is an integration-compatibility identity; it does not establish
experimental accuracy or training-time equivalence.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


CC_4G_CANONICAL_PROFILE_ID = "cc-qgeognn-4g-canonical-v1"

# Hashes use lowercase SHA-256. The runtime implementation hash is finalized
# after the execution code is frozen; all other values come from the existing
# trusted inventory, project dependency locks, and the independent CLI report.
_PROFILE_DOCUMENT: dict[str, Any] = {
    "profile_id": CC_4G_CANONICAL_PROFILE_ID,
    "purpose": "canonical-integration-compatibility",
    "model": {
        "model_id": "qgeognn_silica_4g",
        "model_version": "cc-qgeognn-1",
        "weights_sha256": "d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99",
        "preprocessing_metadata_sha256": "017a98b926d80f4a3d9cacc6e23a3e1ae32206903d4082c39be7049db06a898d",
        "scaler_file_sha256": "017a98b926d80f4a3d9cacc6e23a3e1ae32206903d4082c39be7049db06a898d",
    },
    "canonicalization": {
        "implementation": "chrom_platform.structure_core.service.normalize_smiles",
        "source_sha256": "aee4e3d233d87f49dbaa3d41214d4ff3c94a9c11823c7f5a1ee5a5abe8745c05",
        "rdkit_version": "2026.3.3",
        "rdkit_runtime_version": "2026.03.3",
        "parse": "Chem.MolFromSmiles",
        "serialize": "Chem.MolToSmiles",
        "canonical": True,
        "isomeric_smiles": True,
        "stereochemistry": "preserve-as-encoded",
        "atom_order": "RDKit canonical SMILES parse order; no additional atom renumbering",
    },
    "graph_preprocessing": {
        "implementation_source_sha256": "b27d578f964f0c29afebee907d0377a37e982bfe0cfe2b9c889e255d48cbaabd",
        "graph_builder_source_sha256": "d77638e4ad615e4b27dc3435d7f1b175ba8c4f3dc139c5043bb2f9d0d37ba896",
        "runtime_source_sha256": "d40703244d6aae03fb29322b0fd76fbdd3455d7c46ed3d5f8e389d7391571a51",
        "operations": ["MolFromSmiles", "AddHs", "EmbedMolecule", "MMFFOptimizeMolecule", "RemoveHs", "author_graph_builder"],
        "embed": {
            "random_seed": 525,
            "max_attempts": 0,
            "clear_conformers": True,
            "use_random_coordinates": False,
            "box_size_multiplier": 2.0,
            "random_negative_eigenvalues": True,
            "zero_eigenvalue_fail_count": 1,
            "force_tolerance": 0.001,
            "ignore_smoothing_failures": False,
            "enforce_chirality": True,
            "experimental_torsion_preferences": True,
            "basic_knowledge": True,
            "print_experimental_torsions": False,
            "small_ring_torsions": False,
            "macrocycle_torsions": True,
            "torsion_definition_version": 2,
            "macrocycle_14_configuration": True,
        },
        "mmff": {
            "variant": "MMFF94",
            "max_iterations": 200,
            "nonbonded_threshold": 100.0,
            "conformer_id": -1,
            "ignore_interfragment_interactions": True,
            "nonzero_status_policy": "currently ignored by the legacy-compatible runtime",
        },
        "embed_failure_policy": "nonzero return raises and prediction fails",
        "torch": "2.6.0+cu124",
        "torch_geometric": "2.8.0",
        "numpy": "1.26.4",
        "mordred": "1.2.0",
        "device": "cpu",
        "evaluation": "model.eval() with torch.no_grad(); no explicit deterministic-algorithms toggle",
    },
    "features": {
        "condition_feature_order": [
            "eluent_0", "eluent_1", "eluent_2", "eluent_3", "eluent_4", "eluent_5",
            "loading_solvent", "sample_mass", "loading_volume",
        ],
        "descriptor_order": [
            "ExactMolWt", "NumRotatableBonds", "NumHDonors", "NumHAcceptors", "MolLogP",
            "Mordred[153]", "Mordred[278]", "Mordred[884]", "Mordred[885]", "Mordred[1273]",
            "Mordred[1594]", "Mordred[431]", "Mordred[1768]", "Mordred[1769]", "Mordred[1288]",
            "Mordred[1521]",
        ],
        "numeric_dtype": "float32",
        "condition_units": {
            "sample_mass": "mg", "loading_volume": "uL", "flow_rate": "mL/min",
            "predicted_volume": "mL", "derived_time": "s",
        },
        "time_derivation": "volume_ml * 60 / flow_rate_ml_min",
    },
    "independent_reference": {
        "evidence_report_sha256": "30a0a48a53f9a56775e6d9010e922d486a480de8f39c433d2b1046ab213d9b60",
        "reference": "author offline CLI with canonical inputs; not live output copied as oracle",
        "comparison": "two canonical molecules; 12 volume and 12 derived-time values; exact match",
        "scientific_limit": "compatibility regression only; training-time canonicalization equivalence and experimental accuracy are not established",
    },
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


# The literal is frozen after all implementation source hashes are finalized.
_PROFILE_CANONICAL_JSON = _canonical_json(_PROFILE_DOCUMENT)
CC_4G_CANONICAL_PROFILE_SHA256 = "1e9ed410450e12e1e0f325dbc03535220a96aae45ffc18afde4a4a2b26e9741e"


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


CC_4G_CANONICAL_PROFILE: Mapping[str, Any] = _freeze(json.loads(_PROFILE_CANONICAL_JSON))


def resolve_cc_preprocessing_profile(profile_id: str | None, profile_sha256: str | None) -> Mapping[str, Any] | None:
    """Resolve only the exact immutable profile pair advertised by this build."""

    if (
        verify_cc_profile_document()
        and profile_id == CC_4G_CANONICAL_PROFILE_ID
        and profile_sha256 == CC_4G_CANONICAL_PROFILE_SHA256
    ):
        return CC_4G_CANONICAL_PROFILE
    return None


def verify_cc_profile_document() -> bool:
    try:
        current = _canonical_json(_PROFILE_DOCUMENT)
    except (TypeError, ValueError, OverflowError, UnicodeError):
        return False
    return (
        current == _PROFILE_CANONICAL_JSON
        and hashlib.sha256(current).hexdigest() == CC_4G_CANONICAL_PROFILE_SHA256
    )


def verify_cc_preprocessing_environment(*, runtime_source: str | Path, canonicalizer_source: str | Path) -> bool:
    """Check the pinned code and scientific dependency versions at build time."""

    profile = CC_4G_CANONICAL_PROFILE
    try:
        if not verify_cc_profile_document():
            return False
        if _sha256_file(runtime_source) != profile["graph_preprocessing"]["runtime_source_sha256"]:
            return False
        if _sha256_file(canonicalizer_source) != profile["canonicalization"]["source_sha256"]:
            return False
        if importlib.metadata.version("rdkit") != profile["canonicalization"]["rdkit_version"]:
            return False
        from rdkit import rdBase

        if rdBase.rdkitVersion != profile["canonicalization"]["rdkit_runtime_version"]:
            return False
        for package, profile_key in (
            ("torch", "torch"),
            ("torch-geometric", "torch_geometric"),
            ("numpy", "numpy"),
            ("mordred", "mordred"),
        ):
            if importlib.metadata.version(package) != profile["graph_preprocessing"][profile_key]:
                return False
    except (OSError, ImportError, importlib.metadata.PackageNotFoundError, TypeError, KeyError):
        return False
    return True


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "CC_4G_CANONICAL_PROFILE",
    "CC_4G_CANONICAL_PROFILE_ID",
    "CC_4G_CANONICAL_PROFILE_SHA256",
    "resolve_cc_preprocessing_profile",
    "verify_cc_preprocessing_environment",
    "verify_cc_profile_document",
]
