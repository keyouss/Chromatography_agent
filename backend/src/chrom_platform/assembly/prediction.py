"""Prediction assembly for real, build-owned SFC assets.

The assembly only enables SFC when every model and candidate asset binding is
explicitly supplied by the deployment environment.  A missing binding leaves
the capability absent and therefore cannot turn into a simulated success.
"""

from __future__ import annotations

import os
import json
import math
import hashlib
import importlib.metadata
import platform
import logging
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from chrom_platform.artifact_core.models import Structure
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest,
    CapabilityRegistry,
    ModelVerificationEvidence,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.ports import PredictionStructurePort, StructureSnapshot
from chrom_platform.adapters.sfc.adapter import (
    SfcCatalogSourceProof,
    SfcVerificationResult,
    build_sfc_adapter,
)
from chrom_platform.adapters.sfc.runtime import (
    _ALLOWED_NAN_FEATURES,
    SfcCandidateCatalog,
    SfcModelRegistry,
    load_sfc_model,
    SfcAssetUnavailable,
)
from chrom_platform.task_core.service import OperationContext
from chrom_platform.adapters.tlc.adapter import TlcPredictionAdapter
from chrom_platform.adapters.tlc.manifest import (
    TLC_ARTIFACT_ID,
    TLC_ARTIFACT_MANIFEST_SHA256,
    TLC_INPUT_SCHEMA_VERSION,
    TLC_MODEL_VERSION,
    TLC_OUTPUT_SCHEMA_VERSION,
    TLC_SCOPE_ID,
    TlcArtifactManifest,
)
from chrom_platform.adapters.tlc.runtime import TlcRuntime
from chrom_platform.adapters.tlc.service import TlcPredictionService


SFC_FULL_REFERENCE_SHA256 = "b317a88d05d1aedb165adbcf1353b0775bb5f5e171e43a763db2a962e5c9e246"
SFC_SINGLE_REFERENCE_SHA256 = "2d7236cff2bb095fec358902ee71aef0a95121b170d57aaf940dae535d122538"
_PACKAGE_RESOURCES = Path(__file__).resolve().parents[1] / "resources"
SFC_SINGLE_REFERENCE_PATH = _PACKAGE_RESOURCES / "sfc-single-reference-2p5-warning-v3.json"
SFC_RS12_FULL_MODEL_ID = "rs12-extra-trees-s80-seed42"
SFC_RS12_FULL_MODEL_VERSION = "1"
SFC_RS12_FULL_FIXTURE_VERSION = "sfc-rs12-full-column-reference-v2"
SFC_RS12_FULL_REFERENCE_CANDIDATE_IDS = {"candidate-cellulose-2", "candidate-oz"}
SFC_RS12_FULL_CANDIDATE_BINDING_POLICY = "reference_local_id_matched_by_complete_observed_tuple"
from chrom_platform.modules.prediction.schemas import (
    TlcConditionComparisonCreate,
    TlcConditionComparisonResult,
    TlcPredictionCreate,
    TlcPredictionResult,
    TlcReverseCreate,
    TlcReverseResult,
)


class SQLAlchemyPredictionStructurePort(PredictionStructurePort):
    def __init__(self, session: Session) -> None:
        self.session = session

    def load_many(self, ctx: OperationContext, project_id: str, structure_refs: list[str]) -> list[StructureSnapshot]:
        rows = self.session.scalars(
            select(Structure).where(
                Structure.project_id == project_id,
                Structure.id.in_(structure_refs),
                Structure.is_valid.is_(True),
            )
        ).all()
        by_id = {row.id: row for row in rows}
        if set(by_id) != set(structure_refs):
            missing = sorted(set(structure_refs) - set(by_id))
            from chrom_platform.core.errors import APIError
            raise APIError(404, "RESOURCE_NOT_FOUND", "结构不存在或不属于当前项目", details={"structure_refs": missing})
        return [StructureSnapshot(id=ref, project_id=project_id, normalized_smiles=by_id[ref].canonical_smiles) for ref in structure_refs]

    def validate_enantiomer_pair(self, ctx: OperationContext, project_id: str, structures: list[StructureSnapshot], *, chromatography_type: str) -> None:
        if len(structures) != 2 or structures[0].id == structures[1].id:
            from chrom_platform.core.errors import APIError
            raise APIError(422, "PREDICTION_INPUT_INVALID", "SFC 需要两个不同的结构")
        rows = self.session.scalars(select(Structure).where(Structure.id.in_([item.id for item in structures]))).all()
        if len(rows) != 2 or any(row.project_id != project_id or not row.has_stereochemistry for row in rows):
            from chrom_platform.core.errors import APIError
            raise APIError(422, "PREDICTION_INPUT_INVALID", "SFC 需要项目内已指定立体化学的结构")
        try:
            _require_enantiomer_pair([row.canonical_smiles for row in rows])
        except (ImportError, ValueError):
            from chrom_platform.core.errors import APIError
            raise APIError(422, "PREDICTION_INPUT_INVALID", "SFC 两个结构必须是互为对映体") from None

    def validate_chiral_proposal(self, ctx: OperationContext, project_id: str, structure: StructureSnapshot, *, proposal_id: str) -> None:
        return None


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _sfc_scope_storage_path(*, candidate_db: str, model_root: str, registry_path: str) -> Path:
    """Resolve the writable, cross-process scope store from deployment config.

    Candidate/model registries are immutable assets.  Requiring a separate
    absolute path prevents a worker restart from silently creating state next
    to a read-only candidate database or inside a delivery asset tree.
    """
    configured = _env("CHROM_SFC_SCOPE_STORAGE_PATH")
    if not configured:
        raise ValueError("SFC_SCOPE_STORAGE_PATH_REQUIRED")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ValueError("SFC_SCOPE_STORAGE_PATH_MUST_BE_ABSOLUTE")
    path = path.resolve()
    candidate = Path(candidate_db).resolve()
    if path == Path(f"{candidate}.scopes.sqlite3").resolve() or path == candidate:
        raise ValueError("SFC_SCOPE_STORAGE_PATH_MUST_BE_SEPARATE")

    def inside(root_value: str) -> bool:
        root = Path(root_value).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    if inside(model_root) or inside(registry_path):
        raise ValueError("SFC_SCOPE_STORAGE_PATH_IN_ASSET_ROOT")
    if any(part.casefold() in {"services", "deliverables"} for part in path.parts):
        raise ValueError("SFC_SCOPE_STORAGE_PATH_IN_READONLY_TREE")
    parent = path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        raise ValueError("SFC_SCOPE_STORAGE_PATH_PARENT_NOT_WRITABLE")
    return path


def _require_enantiomer_pair(smiles: list[str]) -> None:
    try:
        from rdkit import Chem
    except ImportError as exc:
        raise ImportError("RDKit is required for SFC enantiomer validation") from exc
    if len(smiles) != 2:
        raise ValueError("exactly two structures are required")
    molecules = [Chem.MolFromSmiles(value) for value in smiles]
    if any(molecule is None for molecule in molecules):
        raise ValueError("invalid structure")
    molecules = [molecule for molecule in molecules if molecule is not None]
    if Chem.MolToSmiles(molecules[0], isomericSmiles=False) != Chem.MolToSmiles(molecules[1], isomericSmiles=False):
        raise ValueError("structures have different connectivity")
    labels = []
    for molecule in molecules:
        Chem.AssignStereochemistry(molecule, cleanIt=True, force=True)
        ranks = Chem.CanonicalRankAtoms(molecule, breakTies=False)
        centers = Chem.FindMolChiralCenters(molecule, includeUnassigned=True, includeCIP=True)
        by_rank = {ranks[index]: label for index, label in centers}
        if not by_rank or any(label not in {"R", "S"} for label in by_rank.values()):
            raise ValueError("stereochemistry is unspecified")
        labels.append(by_rank)
    if labels[0].keys() != labels[1].keys() or any(labels[0][key] == labels[1][key] for key in labels[0]):
        raise ValueError("structures are not an enantiomeric inversion")


def _single_column_preflight(
    *, model_registry: SfcModelRegistry, entry: object, catalog: SfcCandidateCatalog,
) -> tuple[dict[str, bool], dict[str, object] | None, object | None]:
    """Check the pinned single-column proof and reachable catalog row before loading a model."""
    checks = {"manifest": False, "schema": False, "artifact": False, "environment": False}
    try:
        fixture_path = SFC_SINGLE_REFERENCE_PATH
        if _sha256_file(fixture_path) != SFC_SINGLE_REFERENCE_SHA256:
            return checks, None, None
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        schema = fixture["schema"]
        if (
            set(fixture) != set(schema["required_root_keys"])
            or fixture.get("fixture_version") != "sfc-single-column-reference-v3"
            or set(fixture.get("input", {}).get("conditions", {})) != set(schema["required_condition_keys"])
            or schema.get("input_schema") != "SfcSingleColumnPredictionCreate"
            or schema.get("output_schema") != "SfcPredictionResult"
        ):
            return checks, None, None

        model_info = fixture["model"]
        candidate_source = fixture["candidate_source"]
        reference = fixture["source_reference"]
        input_data = fixture["input"]
        conditions = input_data["conditions"]
        packages = fixture["environment"]["package_versions"]
        if (
            reference.get("kind") != "independent-author-v1.1-reference"
            or reference.get("path") != "backend/.generated/priority-launch-evidence/sfc-2p5-reference/sfc-2p5-reference.json"
            or reference.get("sha256") != "3a8f3b012d6de7383493bd5b2091a78a7fcf42fa3e00ac3c3894845b3b2f06da"
            or reference.get("tolerance_policy_sha256") != "f44756cd3f773d4a283561a40b0358ca0a7cd00c4039b149a9ae36fede7eee5c"
            or set(model_info) != {"id", "version", "scope", "target", "threshold", "artifact_sha256", "registry_sha256", "metadata_sha256", "feature_count", "feature_names"}
            or not isinstance(input_data.get("structures"), list)
            or len(input_data["structures"]) != 2
            or any(not isinstance(value, str) or not value.strip() for value in input_data["structures"])
            or not isinstance(conditions, dict)
            or any(not isinstance(conditions.get(key), str) or not conditions[key].strip() for key in ("column", "stationary_phase", "mode"))
            or any(isinstance(conditions.get(key), bool) or not isinstance(conditions.get(key), (int, float)) or not math.isfinite(float(conditions[key])) for key in schema["required_condition_keys"][3:])
            or not isinstance(fixture.get("expected_score"), (int, float))
            or isinstance(fixture.get("expected_score"), bool)
            or not isinstance(fixture.get("tolerance"), dict)
            or fixture["tolerance"].get("metric") != "positive_class_probability"
            or fixture["tolerance"].get("absolute") != 1e-12
            or fixture["tolerance"].get("relative") != 1e-12
            or fixture["tolerance"].get("finite_and_bounded") is not True
            or not isinstance(fixture.get("expected_warnings"), list)
            or fixture["expected_warnings"] != [
                f"FEATURE_VALUE_MISSING:{name}"
                for name in model_info["feature_names"]
                if name in _ALLOWED_NAN_FEATURES
            ]
            or not isinstance(fixture.get("warning_basis"), str)
            or "MDEC-11" not in fixture["warning_basis"]
            or fixture["environment"].get("python_version") != "3.11.15"
            or packages != {"numpy": "1.26.4", "pandas": "2.3.3", "joblib": "1.5.3", "mordred": "1.2.0", "scikit-learn": "1.6.1", "rdkit": "2026.3.3"}
            or candidate_source.get("runtime_catalog_sha256") != "4a76421324aca6865d5dbd7df1e57d5df5362d231d7e3ee3dd34cf4db48c9ab1"
            or candidate_source.get("table") != "result_with_advanced_descriptors"
        ):
            return checks, None, None

        registry_sha256 = _sha256_file(model_registry.registry_path)
        metadata_path = model_registry.artifact_for(entry).parent / "metadata.json"
        metadata_sha256 = _sha256_file(metadata_path)
        checks["manifest"] = all((
            entry.model_id == model_info["id"] == "sc-rs12-amylose-dmpc-coated",
            entry.version == model_info["version"] == "1",
            entry.scope == model_info["scope"] == "single_column",
            entry.target == model_info["target"] == "label_success_rs12",
            float(entry.threshold) == float(model_info["threshold"]) == 1.2,
            entry.artifact_sha256 == model_info["artifact_sha256"],
            registry_sha256 == model_info["registry_sha256"],
            metadata_sha256 == model_info["metadata_sha256"],
        ))
        entry_feature_names = tuple(entry.feature_names)
        checks["schema"] = (
            model_info["feature_count"] == 66
            and len(entry_feature_names) == 66
            and len(set(entry_feature_names)) == 66
            and list(entry_feature_names) == model_info["feature_names"]
        )
        artifact_path = model_registry.artifact_for(entry)
        checks["artifact"] = (
            _sha256_file(artifact_path) == entry.artifact_sha256 == model_info["artifact_sha256"]
        )

        runtime_catalog_path = _env("CHROM_SFC_CANDIDATE_DB_PATH")
        catalog_path = Path(runtime_catalog_path).resolve() if runtime_catalog_path else None
        if catalog_path is None or catalog.path.resolve() != catalog_path:
            return checks, None, None
        catalog_sha256 = _sha256_file(catalog_path)
        if catalog_sha256 != candidate_source["runtime_catalog_sha256"]:
            return checks, None, None

        domain_candidates = [
            candidate for candidate in catalog.resolve(catalog.DEFAULT_SCOPE_ID)
            if candidate.column in entry.allowed_columns
            and candidate.stationary_phase == entry.stationary_phase
            and candidate.mode == entry.mode
        ]
        expected_conditions = {
            "column": conditions["column"],
            "stationary_phase": conditions["stationary_phase"],
            "mode": conditions["mode"],
            "length_mm": conditions["length_mm"],
            "inner_diameter_mm": conditions["inner_diameter_mm"],
            "particle_size_um": conditions["particle_size_um"],
            "column_temperature_c": conditions["column_temperature_c"],
            "flow_rate_ml_min": conditions["flow_rate_ml_min"],
        }
        matching = [
            candidate for candidate in domain_candidates
            if set(candidate.as_conditions()) == set(schema["required_condition_keys"])
            and candidate.as_conditions() == expected_conditions
        ]
        package_versions_match = all(
            importlib.metadata.version(name) == version
            for name, version in packages.items()
        )
        checks["environment"] = all((
            checks["manifest"], checks["schema"], checks["artifact"],
            platform.python_version() == fixture["environment"]["python_version"],
            package_versions_match,
            candidate_source.get("author_database_sha256") == "266a6d00df80166c3faf64be9dc21748d9a39a9f7106a24b8741fdd628eb9c1d",
            len(matching) == 1,
        ))
        if not all(checks.values()):
            return checks, None, None
        return checks, fixture, matching[0]
    except Exception:
        return checks, None, None


def _rs12_full_preflight(
    *, model_registry: SfcModelRegistry, entry: object, catalog: SfcCandidateCatalog,
    reference_path: str | Path | None, reference_sha256: str | None,
    expected_artifact_sha256: str | None,
) -> tuple[dict[str, bool], dict[str, object] | None, tuple[object, ...]]:
    """Validate a frozen, two-row RS12 author reference before any model load."""
    checks = {"manifest": False, "schema": False, "artifact": False, "environment": False, "golden": False, "anomaly": False, "preprocessing": False}
    candidates: tuple[object, ...] = ()
    try:
        if (
            entry.model_id != SFC_RS12_FULL_MODEL_ID or entry.version != SFC_RS12_FULL_MODEL_VERSION
            or entry.scope != "all_column" or reference_path is None or reference_sha256 is None
            or expected_artifact_sha256 is None
        ):
            return checks, None, candidates
        path = Path(reference_path).expanduser()
        pin = reference_sha256.lower()
        if not path.is_absolute() or len(pin) != 64 or any(char not in "0123456789abcdef" for char in pin):
            return checks, None, candidates
        if not path.is_file() or _sha256_file(path) != pin:
            return checks, None, candidates
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(fixture, dict):
            return checks, None, candidates

        model_info = fixture.get("model")
        author = fixture.get("author_reference")
        source = fixture.get("candidate_source")
        input_data = fixture.get("input")
        environment = fixture.get("environment")
        if not all(isinstance(value, dict) for value in (model_info, author, source, input_data, environment)):
            return checks, None, candidates
        metadata_path = model_registry.artifact_for(entry).parent / "metadata.json"
        registry_digest = _sha256_file(model_registry.registry_path)
        metadata_digest = _sha256_file(metadata_path)
        artifact_digest = _sha256_file(model_registry.artifact_for(entry))
        names = list(entry.feature_names)
        names_digest = hashlib.sha256(json.dumps(names, separators=(",", ":")).encode("utf-8")).hexdigest()
        from chrom_platform.adapters.sfc.adapter import _schemas
        input_schema, output_schema = _schemas("full_column")
        schema_digests = {
            key: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            for key, value in (("input_schema_sha256", input_schema), ("output_schema_sha256", output_schema))
        }
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        source_pins = fixture.get("source_pins")
        if not isinstance(source_pins, list):
            return checks, None, candidates
        source_pin_by_role: dict[str, str] = {}
        for source_pin in source_pins:
            if not isinstance(source_pin, dict):
                return checks, None, candidates
            role, source_path, source_digest = source_pin.get("role"), source_pin.get("path"), source_pin.get("sha256")
            source_file = Path(str(source_path)).expanduser()
            if (
                not isinstance(role, str) or not role or role in source_pin_by_role
                or not source_file.is_absolute() or not source_file.is_file()
                or not isinstance(source_digest, str) or len(source_digest) != 64
                or _sha256_file(source_file) != source_digest.lower()
            ):
                return checks, None, candidates
            source_pin_by_role[role] = source_digest.lower()
        warning_basis = author.get("warning_basis")
        checks["manifest"] = all((
            fixture.get("fixture_status") == "AUTHOR_REFERENCE",
            fixture.get("fixture_version") == SFC_RS12_FULL_FIXTURE_VERSION,
            fixture.get("reference_kind") == "author_v1.1_implementation_software_reference",
            "proposal_revision" not in fixture and fixture.get("status") != "PROPOSAL_NOT_VERIFIED",
            model_info.get("id") == entry.model_id == SFC_RS12_FULL_MODEL_ID,
            model_info.get("version") == entry.version == SFC_RS12_FULL_MODEL_VERSION,
            model_info.get("scope") == entry.scope == "all_column",
            model_info.get("target") == entry.target == "label_success_rs12",
            str(model_info.get("threshold")) == str(entry.threshold) == "1.2",
            model_info.get("score_semantics") == entry.score_semantics == "positive_class_probability",
            model_info.get("artifact_sha256") == entry.artifact_sha256 == expected_artifact_sha256.lower() == artifact_digest,
            model_info.get("registry_sha256") == registry_digest,
            model_info.get("metadata_sha256") == metadata_digest,
            metadata.get("id") == entry.model_id and metadata.get("scope") == "all_column",
            metadata.get("target") == entry.target and str(metadata.get("threshold")) == str(entry.threshold),
            metadata.get("model_sha256", "").lower() == entry.artifact_sha256,
            metadata.get("feature_names") == names,
            isinstance(model_info.get("preprocessing"), dict) and bool(model_info["preprocessing"]),
            source_pin_by_role.get("registry") == registry_digest,
            source_pin_by_role.get("metadata") == metadata_digest,
            source_pin_by_role.get("artifact") == artifact_digest,
            isinstance(warning_basis, dict)
            and warning_basis.get("source_role") == "author_inference_source"
            and warning_basis.get("source_sha256") == source_pin_by_role.get("author_inference_source")
            and warning_basis.get("result_role") == "author_reference_output"
            and warning_basis.get("result_sha256") == source_pin_by_role.get("author_reference_output"),
        ))
        checks["schema"] = all((
            model_info.get("feature_count") == 66 and len(names) == 66 and len(set(names)) == 66,
            model_info.get("feature_names") == names,
            model_info.get("feature_names_sha256") == names_digest,
            all(model_info.get(key) == value for key, value in schema_digests.items()),
            input_data.get("schema_version") == "1.1" and input_data.get("tool") == "full_column",
        ))
        checks["artifact"] = artifact_digest == entry.artifact_sha256 == expected_artifact_sha256.lower()

        candidate_path = Path(str(source.get("platform_catalog_path", ""))).expanduser()
        author_catalog_path = Path(str(source.get("author_catalog_path", ""))).expanduser()
        if not candidate_path.is_absolute() or not author_catalog_path.is_absolute():
            return checks, None, candidates
        if catalog.path.resolve() != candidate_path.resolve() or not author_catalog_path.is_file():
            return checks, None, candidates
        if (
            _sha256_file(candidate_path) != source.get("platform_catalog_sha256")
            or _sha256_file(author_catalog_path) != source.get("author_catalog_sha256")
            or source.get("candidate_binding_policy") != SFC_RS12_FULL_CANDIDATE_BINDING_POLICY
        ):
            return checks, None, candidates
        catalog.bind_model_scope(model_id=entry.model_id, model_version=entry.version, scope="all_column")
        catalog_candidates = tuple(catalog.resolve(catalog.DEFAULT_SCOPE_ID))
        rows = input_data.get("candidates")
        expected_rows = author.get("candidate_results")
        if not isinstance(rows, list) or len(rows) != 2 or not isinstance(expected_rows, list) or len(expected_rows) != 2:
            return checks, None, candidates
        axis_names = ("column", "stationary_phase", "mode", "length_mm", "inner_diameter_mm", "particle_size_um", "column_temperature_c", "flow_rate_ml_min")

        def row_conditions(row: object) -> dict[str, object]:
            return {
                "column": row["column"], "stationary_phase": row["stationary_phase"], "mode": row["mode"],
                "length_mm": row["length_mm"], "inner_diameter_mm": row["inner_diameter_mm"],
                "particle_size_um": row["particle_size_um"], "column_temperature_c": row["column_temperature"],
                "flow_rate_ml_min": row["flow_rate"],
            }

        def condition_key(conditions: dict[str, object]) -> tuple[object, ...]:
            return tuple(
                str(conditions[name]).strip() if index < 3 else float(conditions[name])
                for index, name in enumerate(axis_names)
            )

        ids = [row.get("candidate_id") for row in rows if isinstance(row, dict)]
        expected_ids = [row.get("candidate_id") for row in expected_rows if isinstance(row, dict)]
        if (
            len(ids) != 2 or len(set(ids)) != 2 or set(ids) != set(expected_ids)
            or set(ids) != SFC_RS12_FULL_REFERENCE_CANDIDATE_IDS or len(set(expected_ids)) != 2
        ):
            return checks, None, candidates
        if len({candidate.candidate_id for candidate in catalog_candidates}) != len(catalog_candidates):
            return checks, None, candidates
        by_condition: dict[tuple[object, ...], list[object]] = {}
        for candidate in catalog_candidates:
            key = condition_key(candidate.as_conditions())
            by_condition.setdefault(key, []).append(candidate)
        selected: list[tuple[str, object]] = []
        native_by_reference_id: dict[str, dict[str, object]] = {}
        normalized_conditions = []
        for row in rows:
            if not isinstance(row, dict) or set(row_conditions(row)) != set(axis_names):
                return checks, None, candidates
            conditions = row_conditions(row)
            values = list(conditions.values())
            if any(not isinstance(value, str) or not value.strip() for value in values[:3]):
                return checks, None, candidates
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in values[3:]):
                return checks, None, candidates
            matches = by_condition.get(condition_key(conditions), [])
            candidate_id = row.get("candidate_id")
            if len(matches) != 1 or candidate_id in {item.candidate_id for item in catalog_candidates}:
                return checks, None, candidates
            candidate = matches[0]
            if candidate.candidate_id in {item["candidate_id"] for item in native_by_reference_id.values()}:
                return checks, None, candidates
            selected.append((candidate_id, candidate))
            native_by_reference_id[candidate_id] = {
                "candidate_id": candidate.candidate_id,
                "conditions": candidate.as_conditions(),
            }
            normalized_conditions.append(conditions)
        if len({condition_key(item) for item in normalized_conditions}) != 2:
            return checks, None, candidates
        expected_by_id = {row["candidate_id"]: row for row in expected_rows}
        if any(expected_by_id[row["candidate_id"]].get("conditions") != conditions for row, conditions in zip(rows, normalized_conditions)):
            return checks, None, candidates
        warnings = author.get("expected_warnings")
        nan_features = author.get("observed_author_feature_nan_columns")
        tolerance = author.get("tolerance")
        probabilities = [expected_by_id[candidate_id].get("expected_probability") for candidate_id in ids]
        ranks = [expected_by_id[candidate_id].get("author_rank") for candidate_id in ids]
        checks["environment"] = all((
            platform.python_version() == environment.get("python_version"),
            isinstance(environment.get("package_versions"), dict)
            and {"numpy", "pandas", "joblib", "mordred", "scikit-learn", "rdkit", "pydantic"}.issubset(environment["package_versions"])
            and all(importlib.metadata.version(name) == version for name, version in environment["package_versions"].items()),
            isinstance(warning_basis, dict)
            and warning_basis.get("observed_nan_features") == nan_features
            and isinstance(warning_basis.get("explanation"), str) and bool(warning_basis["explanation"].strip()),
            isinstance(nan_features, list) and len(nan_features) > 0 and set(warnings or []) == {f"FEATURE_VALUE_MISSING:{name}" for name in nan_features},
            isinstance(tolerance, dict) and tolerance.get("absolute") == 1e-12 and tolerance.get("relative") == 1e-12
            and tolerance.get("rule") == "Python math.isclose(actual, reference, abs_tol=1e-12, rel_tol=1e-12)",
            all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and 0.0 <= float(value) <= 1.0 for value in probabilities),
            sorted(ranks) == [1, 2],
            [candidate_id for candidate_id, _ in sorted(expected_by_id.items(), key=lambda item: item[1].get("author_rank"))]
            == [candidate_id for candidate_id, _ in sorted(expected_by_id.items(), key=lambda item: item[1].get("expected_probability"), reverse=True)],
            input_data.get("top_k") == 2 and input_data.get("candidate_scope_id"),
            isinstance(input_data.get("structure_smiles"), dict) and all(isinstance(input_data["structure_smiles"].get(key), str) and input_data["structure_smiles"][key].strip() for key in ("a", "b", "modifier")),
            isinstance(input_data.get("modifier_total_fraction"), (int, float)) and not isinstance(input_data.get("modifier_total_fraction"), bool) and math.isfinite(float(input_data["modifier_total_fraction"])) and 0.0 <= float(input_data["modifier_total_fraction"]) <= 1.0,
            isinstance(input_data.get("modifier_components"), list) and len(input_data["modifier_components"]) == 1
            and input_data["modifier_components"][0].get("structure_ref") == "modifier"
            and input_data["modifier_components"][0].get("fraction") == 1.0,
        ))
        # Static checks establish only that a reference is usable. Runtime
        # golden/anomaly evidence stays false until prediction is inspected.
        checks["golden"] = False
        checks["anomaly"] = False
        if not all(checks[name] for name in ("manifest", "schema", "artifact", "environment")):
            return checks, None, candidates
        fixture["_expected_by_id"] = expected_by_id
        fixture["_selected_candidates"] = selected
        fixture["_native_by_reference_id"] = native_by_reference_id
        return checks, fixture, tuple(selected)
    except Exception:
        return checks, None, candidates


def _verification_result(
    *,
    model_registry: SfcModelRegistry,
    entry: object,
    catalog: SfcCandidateCatalog,
    reference_path: str | Path | None = None,
    reference_sha256: str | None = None,
    expected_artifact_sha256: str | None = None,
    business_scope_id: str = "sfc-rs12-author-reference",
) -> dict[str, bool]:
    """Run asset/schema/anomaly/golden checks; false keeps the adapter unavailable."""
    base = {"manifest": False, "schema": False, "artifact": False, "environment": False, "golden": False, "anomaly": False}
    if entry.model_id == SFC_RS12_FULL_MODEL_ID:
        rs12 = {**base, "preprocessing": False}
        try:
            preflight, fixture, selected = _rs12_full_preflight(
                model_registry=model_registry, entry=entry, catalog=catalog,
                reference_path=reference_path, reference_sha256=reference_sha256,
                expected_artifact_sha256=expected_artifact_sha256,
            )
            rs12.update(preflight)
            if fixture is None or not all(rs12[name] for name in ("manifest", "schema", "artifact", "environment")):
                return rs12
            input_data = fixture["input"]
            structures = input_data["structure_smiles"]
            structure_map = {"a": structures["a"], "b": structures["b"], "modifier": structures["modifier"]}
            _require_enantiomer_pair([structure_map["a"], structure_map["b"]])
            from chrom_platform.modules.prediction.schemas import SfcFullColumnPredictionCreate
            request = SfcFullColumnPredictionCreate.model_validate({
                "project_id": "sfc-rs12-author-reference", "schema_version": "1.1",
                "model_selection": {"mode": "exact", "model_id": entry.model_id, "model_version": entry.version},
                "structure_refs": ["a", "b"], "chromatography_type": "sfc", "tool": "full_column",
                "input": {
                    "modifier_total_fraction": input_data["modifier_total_fraction"],
                    "modifier_components": input_data["modifier_components"],
                    "candidate_scope_id": input_data["candidate_scope_id"], "top_k": 2,
                },
            })
            from chrom_platform.adapters.sfc.runtime import SfcRuntime
            resolver_candidates = [
                {"candidate_id": reference_id, **candidate.as_features()}
                for reference_id, candidate in selected
            ]
            runtime = SfcRuntime(
                registry=model_registry, bound_model_id=entry.model_id, bound_model_version=entry.version,
                bound_artifact_sha256=entry.artifact_sha256, business_scope_id="sfc-rs12-author-reference",
                structure_resolver=structure_map,
                candidate_resolver={input_data["candidate_scope_id"]: resolver_candidates},
            )
            # Use the runtime's own bound cache for both inspection and scoring.
            # This keeps startup verification to one exact artifact load per binding.
            model, loaded = runtime._load_bound_model()
            names = tuple(loaded.feature_names)
            rs12["manifest"] = rs12["manifest"] and all((
                loaded.model_id == SFC_RS12_FULL_MODEL_ID,
                loaded.version == SFC_RS12_FULL_MODEL_VERSION,
                loaded.scope == "all_column",
                loaded.target == "label_success_rs12",
                loaded.artifact_sha256 == expected_artifact_sha256.lower(),
            ))
            rs12["schema"] = rs12["schema"] and (
                len(names) == 66 and tuple(getattr(model, "feature_names_in_", ())) == names
                and getattr(model, "n_features_in_", None) == 66
            )
            steps = dict(getattr(model, "named_steps", {}))
            preprocessor = steps.get("preprocess")
            classifier = steps.get("model")
            transformers = dict(getattr(preprocessor, "named_transformers_", {}))
            numeric_pipeline = transformers.get("num")
            categorical_pipeline = transformers.get("cat")
            actual_preprocessing = {
                "pipeline_class": f"{type(model).__module__}.{type(model).__name__}",
                "numeric_imputer": getattr(dict(getattr(numeric_pipeline, "named_steps", {})).get("imputer"), "strategy", None),
                "categorical_imputer": getattr(dict(getattr(categorical_pipeline, "named_steps", {})).get("imputer"), "strategy", None),
                "classifier": f"{type(classifier).__module__}.{type(classifier).__name__}",
                "n_estimators": getattr(classifier, "n_estimators", None),
                "random_state": getattr(classifier, "random_state", None),
            }
            rs12["preprocessing"] = actual_preprocessing == fixture["model"]["preprocessing"]
            rs12["artifact"] = rs12["artifact"] and loaded.artifact_sha256 == expected_artifact_sha256.lower()
            if not all(rs12[name] for name in ("manifest", "schema", "artifact", "preprocessing")):
                return rs12

            rows, warnings, _ = runtime.predict(request)
            expected_by_id = fixture["_expected_by_id"]
            tolerance = fixture["author_reference"]["tolerance"]
            actual_by_id = {row["candidate_id"]: row for row in rows}
            ordered_expected_ids = [
                candidate_id
                for candidate_id, _ in sorted(expected_by_id.items(), key=lambda item: item[1]["author_rank"])
            ]
            row_checks = (
                len(rows) == 2 and set(actual_by_id) == set(expected_by_id)
                and [row.get("candidate_id") for row in rows] == ordered_expected_ids
                and [row.get("rank") for row in rows] == [1, 2]
            )
            if row_checks:
                for candidate_id, expected in expected_by_id.items():
                    actual = actual_by_id[candidate_id]
                    row_checks = row_checks and actual.get("conditions") == expected["conditions"]
                    row_checks = row_checks and actual.get("rank") == expected["author_rank"]
                    score = float(actual.get("separation"))
                    row_checks = row_checks and math.isfinite(score) and math.isclose(
                        score, float(expected["expected_probability"]),
                        abs_tol=float(tolerance["absolute"]), rel_tol=float(tolerance["relative"]),
                    )
            rs12["golden"] = bool(row_checks)
            rs12["anomaly"] = bool(row_checks and (
                warnings == fixture["author_reference"]["expected_warnings"]
                and all(math.isfinite(float(row["separation"])) and 0.0 <= float(row["separation"]) <= 1.0 for row in rows)
            ))
            if all(rs12.values()):
                source = fixture["candidate_source"]
                proof = SfcCatalogSourceProof(
                    model_id=entry.model_id,
                    model_version=entry.version,
                    artifact_sha256=entry.artifact_sha256.lower(),
                    business_scope_id=business_scope_id,
                    input_schema_version="1.1",
                    output_schema_version="1.0",
                    catalog_path=str(Path(source["platform_catalog_path"]).expanduser().resolve()),
                    catalog_sha256=str(source["platform_catalog_sha256"]).lower(),
                    reference_path=str(Path(reference_path).expanduser().resolve()),
                    reference_sha256=str(reference_sha256).lower(),
                    fixture_version=str(fixture["fixture_version"]),
                    check_flags=tuple((name, bool(rs12[name])) for name in (
                        "manifest", "schema", "artifact", "environment", "golden", "anomaly", "preprocessing",
                    )),
                )
                return SfcVerificationResult(rs12, catalog_source_proof=proof)
            return SfcVerificationResult(rs12)
        except Exception:
            rs12["golden"] = False
            rs12["anomaly"] = False
            return rs12
    elif reference_sha256 is not None:
        # A reference pin cannot be replayed against a different full-model identity.
        # Keep the existing RS15 pin path exact and reject cross-model reuse before load.
        if (
            entry.model_id != "rs15-extra-trees-s80-seed2024" or entry.version != "1"
            or entry.scope != "all_column" or reference_sha256.lower() != SFC_FULL_REFERENCE_SHA256
            or (expected_artifact_sha256 is not None and expected_artifact_sha256.lower() != entry.artifact_sha256)
        ):
            return base
    try:
        single_fixture = None
        single_candidate = None
        if entry.scope == "single_column":
            preflight, single_fixture, single_candidate = _single_column_preflight(
                model_registry=model_registry, entry=entry, catalog=catalog,
            )
            base.update(preflight)
            if not all(preflight.values()):
                return base
        model, loaded = load_sfc_model(model_registry, entry.model_id, model_version=entry.version)
        base["manifest"] = loaded.model_id == entry.model_id and loaded.version == entry.version and loaded.scope in {"all_column", "single_column"}
        base["schema"] = len(loaded.feature_names) == 66 and tuple(getattr(model, "feature_names_in_", ())) == loaded.feature_names and getattr(model, "n_features_in_", None) == 66
        base["artifact"] = loaded.artifact_sha256 == entry.artifact_sha256
        catalog_candidates = catalog.resolve(catalog.DEFAULT_SCOPE_ID)
        if loaded.scope == "all_column":
            fixture_path = (
                Path(reference_path)
                if reference_path is not None
                else _PACKAGE_RESOURCES / "sfc-full-reference.json"
            )
            if not fixture_path.is_absolute():
                return base
            if _sha256_file(fixture_path) != SFC_FULL_REFERENCE_SHA256:
                return base
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            model_info = fixture["model"]
            metadata_path = model_registry.artifact_for(loaded).parent / "metadata.json"
            names_digest = hashlib.sha256(json.dumps(list(loaded.feature_names), separators=(",", ":")).encode("utf-8")).hexdigest()
            steps = dict(getattr(model, "named_steps", {}))
            preprocessor = steps.get("preprocess")
            classifier = steps.get("model")
            transformers = dict(getattr(preprocessor, "named_transformers_", {}))
            numeric_pipeline = transformers.get("num")
            categorical_pipeline = transformers.get("cat")
            numeric_imputer = dict(getattr(numeric_pipeline, "named_steps", {})).get("imputer")
            categorical_imputer = dict(getattr(categorical_pipeline, "named_steps", {})).get("imputer")
            preprocessing = {
                "pipeline_class": f"{type(model).__module__}.{type(model).__name__}",
                "numeric_imputer": getattr(numeric_imputer, "strategy", None),
                "categorical_imputer": getattr(categorical_imputer, "strategy", None),
                "classifier": f"{type(classifier).__module__}.{type(classifier).__name__}",
                "n_estimators": getattr(classifier, "n_estimators", None),
                "random_state": getattr(classifier, "random_state", None),
            }
            base["manifest"] = base["manifest"] and all((
                model_info["id"] == loaded.model_id,
                model_info["version"] == loaded.version,
                model_info["scope"] == loaded.scope,
                model_info["target"] == loaded.target,
                model_info["threshold"] == loaded.threshold,
                model_info["artifact_sha256"] == loaded.artifact_sha256,
                model_info["registry_sha256"] == _sha256_file(model_registry.registry_path),
                model_info["metadata_sha256"] == _sha256_file(metadata_path),
                model_info["preprocessing"] == preprocessing,
            ))
            base["schema"] = base["schema"] and all((
                model_info["feature_count"] == len(loaded.feature_names),
                model_info["feature_names"] == list(loaded.feature_names),
                model_info["feature_names_sha256"] == names_digest,
                set(fixture["author_reference"]["missing_feature_policy"]["allowed_nan_features"]) == _ALLOWED_NAN_FEATURES,
                set(fixture["author_reference"]["observed_nan_features"]) == _ALLOWED_NAN_FEATURES,
            ))
            from chrom_platform.adapters.sfc.adapter import _schemas
            input_schema, output_schema = _schemas("full_column")
            contract_digests = {
                key: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                for key, value in (("input_schema_sha256", input_schema), ("output_schema_sha256", output_schema))
            }
            base["schema"] = base["schema"] and all(
                model_info[key] == digest for key, digest in contract_digests.items()
            )
            reference = fixture["author_reference"]
            missing_policy = reference["missing_feature_policy"]
            packages_match = all(
                importlib.metadata.version(name) == version
                for name, version in reference["packages"].items()
            )
            candidate_source = fixture["candidate_source"]
            input_data = fixture["input"]
            expected_conditions = {
                "column": input_data["candidate"]["column"],
                "stationary_phase": input_data["candidate"]["stationary_phase"],
                "mode": input_data["candidate"]["mode"],
                "length_mm": input_data["candidate"]["length_mm"],
                "inner_diameter_mm": input_data["candidate"]["inner_diameter_mm"],
                "particle_size_um": input_data["candidate"]["particle_size_um"],
                "column_temperature_c": input_data["candidate"]["column_temperature"],
                "flow_rate_ml_min": input_data["candidate"]["flow_rate"],
            }
            matches = [candidate for candidate in catalog_candidates if candidate.as_conditions() == expected_conditions]
            base["environment"] = all((
                base["manifest"], base["schema"], base["artifact"], packages_match,
                missing_policy["infinity"] == "reject",
                missing_policy["unexpected_nan"] == "reject",
                missing_policy["allowed_nan_action"] == "preserve NaN at adapter boundary and emit a per-feature warning; fitted author pipeline applies its recorded median imputer",
                platform.python_version() == reference["python"],
                candidate_source["sha256"] == _sha256_file(Path(candidate_source["path"])),
                len(catalog_candidates) > 0, len(matches) == 1,
            ))
            if not all((base["manifest"], base["schema"], base["artifact"], base["environment"])):
                return base
            source = input_data["structure_smiles"]
            structure_map = {key: source[key] for key in ("a", "b", "modifier")}
            _require_enantiomer_pair([structure_map["a"], structure_map["b"]])
            from chrom_platform.modules.prediction.schemas import SfcFullColumnPredictionCreate
            payload = {
                "project_id": "sfc-full-reference", "schema_version": "1.1",
                "model_selection": {"mode": "exact", "model_id": loaded.model_id, "model_version": loaded.version},
                "structure_refs": ["a", "b"], "chromatography_type": "sfc", "tool": "full_column",
                "input": {
                    "modifier_total_fraction": input_data["modifier_total_fraction"],
                    "modifier_components": [{"structure_ref": "modifier", "fraction": input_data["modifier_components"][0]["fraction"]}],
                    "candidate_scope_id": input_data["candidate_scope_id"], "top_k": input_data["top_k"],
                },
            }
            request = SfcFullColumnPredictionCreate.model_validate(payload)
            from chrom_platform.adapters.sfc.runtime import SfcRuntime
            candidate = input_data["candidate"]
            runtime = SfcRuntime(
                registry=model_registry, bound_model_id=loaded.model_id, bound_model_version=loaded.version,
                bound_artifact_sha256=loaded.artifact_sha256, business_scope_id="sfc-full-reference",
                structure_resolver=structure_map,
                candidate_resolver={input_data["candidate_scope_id"]: [{
                    "candidate_id": candidate["candidate_id"],
                    **{key: candidate[key] for key in ("column", "stationary_phase", "mode", "length_mm", "inner_diameter_mm", "particle_size_um")},
                    "column_temperature": candidate["column_temperature"], "flow_rate": candidate["flow_rate"],
                }]},
            )
            rows, warnings, _ = runtime.predict(request)
            score = float(rows[0]["separation"])
            policy = reference["tolerance"]
            scores = reference["same_process_runs"] + [reference["additional_observed_run"]]
            base["golden"] = bool(rows) and all(
                math.isfinite(score) and 0.0 <= score <= 1.0
                and abs(score - float(expected)) <= float(policy["absolute"]) + float(policy["relative"]) * abs(float(expected))
                for expected in scores
            )
            base["anomaly"] = warnings == reference["expected_warnings"] and all(
                math.isfinite(float(row["separation"])) and 0.0 <= float(row["separation"]) <= 1.0
                for row in rows
            )
            return base

        if single_fixture is None or single_candidate is None:
            return base
        conditions = single_fixture["input"]["conditions"]
        structure_map = {
            "a": single_fixture["input"]["structures"][0],
            "b": single_fixture["input"]["structures"][1],
            "m": single_fixture["input"]["modifier_smiles"],
        }
        _require_enantiomer_pair([structure_map["a"], structure_map["b"]])
        from chrom_platform.modules.prediction.schemas import SfcSingleColumnPredictionCreate
        payload = {
            "project_id": "golden", "schema_version": "1.1",
            "model_selection": {"mode": "exact", "model_id": loaded.model_id, "model_version": loaded.version},
            "structure_refs": ["a", "b"], "chromatography_type": "sfc",
            "tool": "single_column",
            "input": {"column_id": conditions["column"], "modifier_total_fraction": single_fixture["input"]["modifier_total_fraction"], "modifier_components": [{"structure_ref": "m", "fraction": single_fixture["input"]["modifier_component_fraction"]}], "candidate_scope_id": "golden", "top_k": 1},
        }
        request = SfcSingleColumnPredictionCreate.model_validate(payload)
        from chrom_platform.adapters.sfc.runtime import SfcRuntime
        runtime = SfcRuntime(registry=model_registry, bound_model_id=loaded.model_id, bound_model_version=loaded.version, bound_artifact_sha256=loaded.artifact_sha256, business_scope_id="golden", structure_resolver=structure_map, candidate_resolver={"golden": [single_candidate]})
        rows, warnings, _ = runtime.predict(request)
        score = float(rows[0]["separation"])
        tolerance = single_fixture["tolerance"]
        base["anomaly"] = bool(rows) and math.isfinite(score) and 0.0 <= score <= 1.0 and warnings == single_fixture["expected_warnings"]
        base["golden"] = (
            bool(rows) and math.isfinite(score)
            and abs(score - float(single_fixture["expected_score"]))
            <= float(tolerance["absolute"]) + float(tolerance["relative"]) * abs(float(single_fixture["expected_score"]))
        )
    except Exception:
        return base
    return base


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _register_verified_tlc(registry: CapabilityRegistry, *, session_factory: Callable[[], Session] | None) -> None:
    """Register TLC only after asset, environment, golden and anomaly checks."""

    required = {
        "registry_path": _env("CHROM_TLC_REGISTRY_PATH"),
        "model_root": _env("CHROM_TLC_MODEL_ROOT"),
        "manifest_sha256": _env("CHROM_TLC_ARTIFACT_MANIFEST_SHA256"),
        "model_id": _env("CHROM_TLC_MODEL_ID"),
        "model_version": _env("CHROM_TLC_MODEL_VERSION"),
        "business_scope_id": _env("CHROM_TLC_BUSINESS_SCOPE_ID"),
    }
    if not all(required.values()):
        return
    if (
        required["model_id"] != TLC_ARTIFACT_ID
        or required["model_version"] != TLC_MODEL_VERSION
        or required["business_scope_id"] != TLC_SCOPE_ID
        or required["manifest_sha256"].lower() != TLC_ARTIFACT_MANIFEST_SHA256
    ):
        return
    try:
        root = TlcRuntime.from_registered_artifact(
            registry_root=required["registry_path"], model_root=required["model_root"], artifact_id=TLC_ARTIFACT_ID
        )
        if not (root.status.checked_assets and root.status.dependencies_available and root.status.feature_schema_verified):
            return
        import torch

        environment = torch.__version__ == "2.6.0+cu124"
        fixture_path = Path(
            _env("CHROM_TLC_GOLDEN_FIXTURE")
            or (_PACKAGE_RESOURCES / "tlc.smoke.json")
        )
        if not fixture_path.is_absolute():
            fixture_path = Path.cwd() / fixture_path
        fixture = json.loads(fixture_path.read_text(encoding="utf-8", errors="replace"))
        request = fixture["request"] if "request" in fixture else {
            "smiles": fixture["smiles"], "mobile_phase": fixture["mobile_phase"]
        }
        golden = root.verify_golden(smiles=request["smiles"], mobile_phase=request["mobile_phase"])
        golden_ok = abs(float(golden["rf"]) - float(fixture["predicted_rf"])) <= 1e-6
        anomaly = (
            math.isfinite(float(golden["rf"]))
            and 0.0 <= float(golden["rf"]) <= 1.0
            and set(golden["component_predictions"]) == {"lgb", "xgb", "random_forest", "ann"}
            and all(math.isfinite(float(value)) and 0.0 <= float(value) <= 1.0 for value in golden["component_predictions"].values())
            and abs(sum(golden["ensemble_weights"].values()) - 1.0) <= 1e-9
        )
        if not (environment and golden_ok and anomaly):
            return
        manifest = AdapterManifest(
            adapter_id="tlc", model_id=TLC_ARTIFACT_ID, model_version=TLC_MODEL_VERSION,
            business_scope_id=TLC_SCOPE_ID, manifest_version="1",
            input_schema_version=TLC_INPUT_SCHEMA_VERSION, output_schema_version=TLC_OUTPUT_SCHEMA_VERSION,
            artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256, schema_available=True,
            assets_available=True, data_origin="production",
        )
        evidence = ModelVerificationEvidence(
            adapter_id="tlc", model_id=TLC_ARTIFACT_ID, model_version=TLC_MODEL_VERSION,
            business_scope_id=TLC_SCOPE_ID, input_schema_version=TLC_INPUT_SCHEMA_VERSION,
            output_schema_version=TLC_OUTPUT_SCHEMA_VERSION, artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
            manifest_verified=True, schema_verified=True, artifact_sha_verified=True,
            environment_verified=environment, golden_sample_verified=golden_ok,
            anomaly_checks_verified=anomaly,
            schema_by_tool={
                "single": {
                    "input_schema": TlcPredictionCreate.model_json_schema(),
                    "output_schema": TlcPredictionResult.model_json_schema(),
                },
                "condition_compare": {
                    "input_schema": TlcConditionComparisonCreate.model_json_schema(),
                    "output_schema": TlcConditionComparisonResult.model_json_schema(),
                },
                "reverse_solvent": {
                    "input_schema": TlcReverseCreate.model_json_schema(),
                    "output_schema": TlcReverseResult.model_json_schema(),
                },
            },
        )
        handle = issue_verified_model_handle(manifest, evidence)
        resolver = None if session_factory is None else lambda structure_ref: _resolve_structure(session_factory, structure_ref)
        adapter = TlcPredictionAdapter(
            root, structure_resolver=resolver, manifest=manifest, verified_handle=handle
        )
        adapter.utility_service = TlcPredictionService(root)
        registry.register(adapter)
    except Exception:
        # A failed optional adapter must not make another verified adapter vanish.
        return


def _optional_asset_root(env_name: str, folder_name: str) -> str | None:
    """Resolve a build-owned optional bundle without changing process env.

    The local universal deployment keeps the audited bundles beside the
    repository.  An explicit environment binding still wins, while the
    checked-in delivery tree makes the development runtime assemble the same
    real adapters as the universal manifest.
    """

    configured = _env(env_name)
    if configured:
        return configured
    candidate = Path(__file__).resolve().parents[4] / "deliverables" / "chromatography_tools_windows_cc_hplc_gc_tlc" / folder_name
    return str(candidate) if candidate.is_dir() else None


def _register_optional_adapters(
    registry: CapabilityRegistry, *, session_factory: Callable[[], Session] | None
) -> None:
    """Register real CC/HPLC/GC runtimes when their audited bundles exist.

    These adapters remain unavailable until their central verification handle
    is issued.  Registering their real manifests is still necessary for
    universal capability discovery; it must never turn a missing proof into a
    successful prediction.
    """

    resolver = None if session_factory is None else lambda structure_ref: _resolve_structure(session_factory, structure_ref)
    cc_root = _optional_asset_root("CHROM_CC_MODEL_ROOT", "cc_column_chromatography")
    if cc_root:
        try:
            from chrom_platform.adapters.cc.adapter import build_cc_adapter

            registry.register(build_cc_adapter(model_root=cc_root, structure_resolver=resolver))
        except Exception:
            pass

    hplc_root = _optional_asset_root("CHROM_HPLC_MODEL_ROOT", "hplc_enantioseparation")
    if hplc_root:
        try:
            from chrom_platform.adapters.hplc.adapter import build_hplc_adapter

            registry.register(build_hplc_adapter(registry_root=hplc_root, model_root=hplc_root, structure_resolver=resolver or {}))
        except Exception:
            pass

    gc_root = _optional_asset_root("CHROM_GC_MODEL_ROOT", "gc_retention_time")
    if gc_root:
        try:
            from chrom_platform.adapters.gc.adapter import build_gc_adapter

            gc_golden_fixture = _PACKAGE_RESOURCES / "gc-reference-preprocessing-v2.json"
            gc_adapter = build_gc_adapter(
                registry_root=gc_root, model_root=gc_root,
                structure_resolver=resolver or {}, golden_fixture=gc_golden_fixture,
            )
            from chrom_platform.assembly.gc_candidate_policy import maybe_build_reviewed_gc_candidate

            registry.register(maybe_build_reviewed_gc_candidate(gc_adapter))
        except Exception:
            pass


def _register_unverified_sfc(
    registry: CapabilityRegistry, *, session_factory: Callable[[], Session] | None
) -> None:
    """Expose the real SFC contract while keeping failed proof unavailable."""

    required = {
        "registry_path": _env("CHROM_SFC_REGISTRY_PATH"),
        "model_root": _env("CHROM_SFC_MODEL_ROOT"),
        "registry_sha256": _env("CHROM_SFC_REGISTRY_SHA256"),
        "candidate_db": _env("CHROM_SFC_CANDIDATE_DB_PATH"),
        "scope_storage_path": _env("CHROM_SFC_SCOPE_STORAGE_PATH"),
        "model_id": _env("CHROM_SFC_MODEL_ID"),
        "artifact_sha256": _env("CHROM_SFC_ARTIFACT_SHA256"),
        "business_scope_id": _env("CHROM_SFC_BUSINESS_SCOPE_ID"),
    }
    if not all(required.values()):
        return
    try:
        from chrom_platform.adapters.sfc.adapter import build_sfc_adapter

        version = _env("CHROM_SFC_MODEL_VERSION") or "1"
        catalog = SfcCandidateCatalog(
            required["candidate_db"],
            scope_storage_path=_sfc_scope_storage_path(
                candidate_db=required["candidate_db"], model_root=required["model_root"], registry_path=required["registry_path"]
            ),
        )
        resolver = None if session_factory is None else lambda structure_ref: _resolve_structure(session_factory, structure_ref)
        registry.register(build_sfc_adapter(
            registry_path=required["registry_path"], model_root=required["model_root"],
            model_id=required["model_id"], model_version=version,
            artifact_sha256=required["artifact_sha256"], business_scope_id=required["business_scope_id"],
            expected_registry_sha256=required["registry_sha256"], structure_resolver=resolver or {},
            candidate_resolver=catalog.resolve, candidate_catalog=catalog,
        ))
    except Exception:
        return


def build_prediction_registry(*, session_factory: Callable[[], Session] | None = None) -> CapabilityRegistry:
    """Build the selected registry; SFC is real only with complete bindings."""
    registry = CapabilityRegistry()
    required = {
        "registry_path": _env("CHROM_SFC_REGISTRY_PATH"),
        "model_root": _env("CHROM_SFC_MODEL_ROOT"),
        "registry_sha256": _env("CHROM_SFC_REGISTRY_SHA256"),
        "candidate_db": _env("CHROM_SFC_CANDIDATE_DB_PATH"),
        "scope_storage_path": _env("CHROM_SFC_SCOPE_STORAGE_PATH"),
        "model_id": _env("CHROM_SFC_MODEL_ID"),
        "artifact_sha256": _env("CHROM_SFC_ARTIFACT_SHA256"),
        "business_scope_id": _env("CHROM_SFC_BUSINESS_SCOPE_ID"),
    }
    if not all(required.values()):
        _register_unverified_sfc(registry, session_factory=session_factory)
        _register_optional_adapters(registry, session_factory=session_factory)
        _register_verified_tlc(registry, session_factory=session_factory)
        return registry
    if session_factory is None:
        _register_unverified_sfc(registry, session_factory=session_factory)
        _register_optional_adapters(registry, session_factory=session_factory)
        _register_verified_tlc(registry, session_factory=session_factory)
        return registry
    try:
        model_registry = SfcModelRegistry(
            registry_path=required["registry_path"], model_root=required["model_root"],
            expected_registry_sha256=required["registry_sha256"],
        )
        scope_storage_path = _sfc_scope_storage_path(
            candidate_db=required["candidate_db"],
            model_root=required["model_root"],
            registry_path=required["registry_path"],
        )
        resolver = lambda structure_ref: _resolve_structure(session_factory, structure_ref)
        from chrom_platform.adapters.sfc.adapter import SfcDispatchAdapter, _schemas

        binding_configs = [{
            "model_id": required["model_id"],
            "model_version": _env("CHROM_SFC_MODEL_VERSION") or "1",
            "artifact_sha256": required["artifact_sha256"],
            "business_scope_id": required["business_scope_id"],
            "reference_path": None,
        }]
        full_config = {
            "model_id": _env("CHROM_SFC_FULL_MODEL_ID"),
            "model_version": _env("CHROM_SFC_FULL_MODEL_VERSION"),
            "artifact_sha256": _env("CHROM_SFC_FULL_ARTIFACT_SHA256"),
            "reference_path": _env("CHROM_SFC_FULL_REFERENCE_PATH"),
            "reference_sha256": _env("CHROM_SFC_FULL_REFERENCE_SHA256"),
        }
        if any(full_config.values()) and all(full_config.values()):
            binding_configs.append({
                **full_config,
                "business_scope_id": required["business_scope_id"],
            })

        bindings = []
        for config_index, config in enumerate(binding_configs):
            binding_stage = "lookup"
            try:
                entry = model_registry.entry(config["model_id"], version=config["model_version"])
                # A configured FULL slot must identify a full-column entry;
                # it never scans for a replacement when the exact key is wrong.
                is_full_slot = config_index == 1
                if is_full_slot and entry.scope != "all_column":
                    continue
                binding_stage = "catalog"
                catalog = SfcCandidateCatalog(required["candidate_db"], scope_storage_path=scope_storage_path)
                expected_artifact = str(config["artifact_sha256"]).lower()
                verification = {name: False for name in ("manifest", "schema", "artifact", "environment", "golden", "anomaly")}
                if entry.artifact_sha256 == expected_artifact:
                    binding_stage = "verification"
                    verification = _verification_result(
                        model_registry=model_registry,
                        entry=entry,
                        catalog=catalog,
                        reference_path=config["reference_path"],
                        reference_sha256=config.get("reference_sha256"),
                        expected_artifact_sha256=expected_artifact,
                        business_scope_id=config["business_scope_id"],
                    )
                evidence = None
                catalog_source_proof = getattr(verification, "catalog_source_proof", None)
                if catalog_source_proof is not None and (
                    catalog_source_proof.reference_path != str(Path(config["reference_path"]).expanduser().resolve())
                    or catalog_source_proof.reference_sha256 != config.get("reference_sha256")
                ):
                    raise SfcAssetUnavailable("MODEL_VERIFICATION_MISMATCH")
                if all(verification.values()):
                    tool_id = "full_column" if entry.scope == "all_column" else "single_column"
                    input_schema, output_schema = _schemas(tool_id)
                    evidence = ModelVerificationEvidence(
                        adapter_id="sfc", model_id=config["model_id"], model_version=entry.version,
                        business_scope_id=config["business_scope_id"], input_schema_version="1.1",
                        output_schema_version="1.0", artifact_sha256=entry.artifact_sha256,
                        manifest_verified=verification["manifest"], schema_verified=verification["schema"], artifact_sha_verified=verification["artifact"],
                        environment_verified=verification["environment"], golden_sample_verified=verification["golden"], anomaly_checks_verified=verification["anomaly"],
                        schema_by_tool={tool_id: {"input_schema": input_schema, "output_schema": output_schema}},
                    )
                binding_stage = "adapter"
                bindings.append(build_sfc_adapter(
                    registry_path=required["registry_path"], model_root=required["model_root"],
                    model_id=config["model_id"], model_version=config["model_version"],
                    artifact_sha256=entry.artifact_sha256,
                    business_scope_id=config["business_scope_id"], expected_registry_sha256=required["registry_sha256"],
                    structure_resolver=resolver, candidate_resolver=catalog.resolve,
                    verification_evidence=evidence, candidate_catalog=catalog,
                    catalog_source_proof=catalog_source_proof,
                ))
            except Exception as exc:
                # A broken optional binding cannot suppress a healthy sibling.
                reason = getattr(exc, "reason", None)
                if reason not in {"CANDIDATE_DATABASE_HASH_MISMATCH", "MODEL_VERIFICATION_MISMATCH", "CANDIDATE_SCOPE_MODEL_BINDING_MISMATCH"}:
                    reason = "BINDING_ASSEMBLY_FAILED"
                logging.getLogger(__name__).warning(
                    "SFC_BINDING_UNAVAILABLE slot=%s stage=%s reason=%s",
                    "full" if config_index == 1 else "primary", binding_stage, reason,
                )
                continue
        if bindings:
            if len(bindings) == 1:
                registry.register(bindings[0])
            else:
                try:
                    registry.register(SfcDispatchAdapter(bindings))
                except Exception:
                    # An optional second binding must not erase the primary
                    # explicitly configured binding when its public contract conflicts.
                    registry.register(bindings[0])
    except Exception:
        # Capability discovery must report an unavailable build rather than
        # expose partial SFC model state or a fallback predictor. TLC remains
        # independently verifiable when SFC bindings are absent or invalid.
        _register_optional_adapters(registry, session_factory=session_factory)
        _register_verified_tlc(registry, session_factory=session_factory)
        return registry
    _register_optional_adapters(registry, session_factory=session_factory)
    _register_verified_tlc(registry, session_factory=session_factory)
    return registry


def build_prediction_worker_registry(*, session_factory: Callable[[], Session] | None = None) -> CapabilityRegistry:
    """Build the same verified asset registry for a standalone Worker process.

    Worker execution uses normalized structures from the persisted snapshot, so
    TLC does not need to open a second synchronous database port at bootstrap.
    SFC deployments with a live candidate/structure port may still inject the
    fully assembled registry through ``ProductionWorkerDependencies``.
    """

    return build_prediction_registry(session_factory=session_factory)


def _resolve_structure(session_factory: Callable[[], Session], structure_ref: str) -> str:
    session = session_factory()
    try:
        row = session.get(Structure, structure_ref)
        if row is None or not row.is_valid:
            raise KeyError(structure_ref)
        return row.canonical_smiles
    finally:
        session.close()


__all__ = [
    "SQLAlchemyPredictionStructurePort",
    "build_prediction_registry",
    "build_prediction_worker_registry",
]
