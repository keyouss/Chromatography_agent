"""Immutable HPLC serving preprocessing identity for the verified v1.1 bundle."""

from __future__ import annotations

import hashlib
import json

from .manifest import (
    HPLC_ASSET_MANIFEST_SHA256,
    HPLC_COLUMN_DESCRIPTOR_SHA256,
    HPLC_MODEL_SHA256,
    HPLC_MULTI_COLUMN_SOURCE_SHA256,
    HPLC_PREDICT_SOURCE_SHA256,
    HPLC_RUNTIME_SOURCE_SHA256,
)

HPLC_SERVING_PROFILE_ID = "hplc-geognn-etkdgv3-seed324-single-mmff-v1"
HPLC_SERVING_PROFILE_DESCRIPTOR = {
    "profile_id": HPLC_SERVING_PROFILE_ID,
    "profile_version": "1",
    "representation": "ordered raw isomeric SMILES pair; no canonical rewrite before author runtime",
    "molecule_preprocessing": [
        "MolFromSmiles(raw)",
        "AddHs",
        "ETKDGv3(randomSeed=324)",
        "EmbedMolecule(single conformer)",
        "MMFFOptimizeMolecule",
        "RemoveHs",
    ],
    "model_id": "hplc-author-geognn-enantioseparation",
    "model_version": "v1.1-author-geognn",
    "model_sha256": HPLC_MODEL_SHA256,
    "column_descriptor_sha256": HPLC_COLUMN_DESCRIPTOR_SHA256,
    "predict_source_sha256": HPLC_PREDICT_SOURCE_SHA256,
    "author_runtime_sha256": HPLC_RUNTIME_SOURCE_SHA256,
    "multi_column_source_sha256": HPLC_MULTI_COLUMN_SOURCE_SHA256,
    "asset_manifest_sha256": HPLC_ASSET_MANIFEST_SHA256,
    "environment": {"torch": "2.6.0+cu124", "device": "cpu"},
    "required_dependency_probe": ["numpy", "torch_geometric", "rdkit", "mordred"],
    "dependency_version_pinning": "only torch version is enforced by current verifier",
    "training_representation_provenance": "unverified",
}
HPLC_SERVING_PROFILE_SHA256 = hashlib.sha256(
    json.dumps(
        HPLC_SERVING_PROFILE_DESCRIPTOR,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


__all__ = [
    "HPLC_SERVING_PROFILE_DESCRIPTOR",
    "HPLC_SERVING_PROFILE_ID",
    "HPLC_SERVING_PROFILE_SHA256",
]
