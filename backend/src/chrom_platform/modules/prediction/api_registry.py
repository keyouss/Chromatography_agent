"""API-side registry backed only by a live Worker metadata attestation.

This module never creates ``VerifiedModelHandle`` objects and never owns a
scientific runtime. The Worker snapshot is re-read at each public operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from pydantic import BaseModel, ValidationError

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.core.errors import APIError, InputValidationError
from chrom_platform.modules.prediction.schemas import ModelSnapshot

from .adapters import (
    AdapterCapability,
    AdapterManifest,
    ModelOption,
    ModelUnavailableError,
    PredictionContractError,
    ToolCapability,
    TypeCapability,
)


_DTO_CLASS_NAMES: dict[str, tuple[str, ...]] = {
    "tlc": ("TlcPredictionCreate", "TlcConditionComparisonCreate", "TlcReverseCreate"),
    "cc": ("CcPredictionCreate",),
    "hplc": ("HplcPredictionCreate",),
    "gc": ("GcPredictionCreate",),
    "sfc": ("SfcSingleColumnPredictionCreate", "SfcFullColumnPredictionCreate"),
}
_ATTESTATION_FIELDS = frozenset({
    "adapter_id", "model_id", "model_version", "business_scope_id",
    "input_schema_version", "output_schema_version", "artifact_sha256",
    "preprocessing_profile_id", "preprocessing_profile_sha256",
})
_STATE_LOCK = threading.RLock()
_STATE_CACHE: tuple[str, dict[str, TypeCapability], dict[tuple[str, str, str], WorkerAttestedBinding]] | None = None


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _health(status: str, reason: str | None = None) -> CapabilityHealth:
    from datetime import datetime, timezone

    return CapabilityHealth(status=status, reason=reason, checked_at=datetime.now(timezone.utc))


def _dto_types(type_id: str) -> tuple[type[BaseModel], ...]:
    names = _DTO_CLASS_NAMES.get(type_id, ())
    schemas = importlib.import_module("chrom_platform.modules.prediction.schemas")
    return tuple(getattr(schemas, name) for name in names if isinstance(getattr(schemas, name, None), type))


@dataclass(frozen=True)
class WorkerAttestedBinding:
    """Typed view of Worker-proven metadata; deliberately not an execution handle."""

    adapter_id: str
    model_id: str
    model_version: str
    manifest: AdapterManifest
    attestation: Mapping[str, Any]
    tool_schemas: Mapping[str, Mapping[str, Any]]
    allowed_tool: str | None = None
    sfc_domain_data: Mapping[str, Any] | None = None
    registry_entry: Mapping[str, Any] | None = None
    catalog_data: Mapping[str, Any] | None = None
    catalog_source_proof: Mapping[str, Any] | None = None
    _catalog: Any = field(default=None, compare=False, repr=False)

    @property
    def candidate_catalog(self) -> Any:
        return self._catalog

    @property
    def sfc_domain(self) -> Any:
        data = self.sfc_domain_data
        if not isinstance(data, Mapping):
            return None
        try:
            runtime = importlib.import_module("chrom_platform.adapters.sfc.runtime")
            return runtime.SfcModelDomain(
                model_id=self.model_id,
                model_version=self.model_version,
                scope=str(data["model_scope"]),
                allowed_columns=tuple(data.get("allowed_columns", ())),
                stationary_phase=data.get("stationary_phase"),
                mode=data.get("mode"),
            )
        except (KeyError, TypeError, ValueError):
            return None


class _ApiAdapterFacade:
    metadata_only = True

    def __init__(self, registry: "ApiPredictionRegistry", type_id: str) -> None:
        self._registry = registry
        self.type_id = type_id

    @property
    def capability(self) -> TypeCapability:
        return self._registry.capability(self.type_id)

    def binding_for(self, model_id: str, model_version: str) -> WorkerAttestedBinding | None:
        return self._registry.binding_for(self.type_id, model_id, model_version)

    @property
    def manifest(self) -> AdapterManifest | None:
        bindings = self._registry.bindings_for(self.type_id)
        return bindings[0].manifest if len(bindings) == 1 else None

    @property
    def attestation(self) -> Mapping[str, Any] | None:
        bindings = self._registry.bindings_for(self.type_id)
        return bindings[0].attestation if len(bindings) == 1 else None

    @property
    def tool_schemas(self) -> Mapping[str, Mapping[str, Any]]:
        bindings = self._registry.bindings_for(self.type_id)
        return bindings[0].tool_schemas if len(bindings) == 1 else {}

    @property
    def condition_catalog(self) -> Any:
        if self.type_id != "cc":
            return None
        return importlib.import_module("chrom_platform.adapters.cc.condition_catalog").CcConditionCatalog()

    @property
    def candidate_catalog(self) -> Any:
        bindings = self._registry.bindings_for(self.type_id)
        return bindings[0].candidate_catalog if len(bindings) == 1 else None

    def catalog_for_scope(self, scope_id: str):
        for binding in self._registry.bindings_for("sfc"):
            catalog = binding.candidate_catalog
            if catalog is None:
                continue
            stored = catalog.scope_binding(scope_id)
            if stored is not None:
                match = self._registry.binding_for("sfc", stored["model_id"], stored["model_version"])
                if match is not None:
                    return match, catalog, stored
        raise KeyError(scope_id)


class ApiPredictionRegistry:
    """Read and validate a Worker snapshot without assembling scientific adapters."""

    metadata_only = True

    def __init__(self, *, manifest: Any, session_factory: Any = None) -> None:
        self._manifest = manifest
        self._session_factory = session_factory

    def _read_state(self) -> tuple[dict[str, TypeCapability], dict[tuple[str, str, str], WorkerAttestedBinding]]:
        global _STATE_CACHE
        from chrom_platform.assembly.prediction_metadata import read_prediction_metadata

        envelope = read_prediction_metadata()
        if not isinstance(envelope, dict):
            with _STATE_LOCK:
                _STATE_CACHE = None
            return {}, {}
        # The reader performs live PID, ready-marker, config, build and asset checks
        # on every call. Only the expensive JSON/SFC catalog parse is cached.
        digest = _canonical_sha256({
            "envelope": envelope,
            "expected_build_id": getattr(self._manifest, "build_id", None),
            "expected_modules": list(getattr(self._manifest, "modules", ()) or ()),
            "selected_types": list(getattr(self._manifest, "chromatography_types", ()) or ()),
        })
        with _STATE_LOCK:
            if _STATE_CACHE is not None and _STATE_CACHE[0] == digest:
                return _STATE_CACHE[1], _STATE_CACHE[2]
        build = envelope.get("build")
        if not isinstance(build, dict) or (
            build.get("build_id") != getattr(self._manifest, "build_id", None)
            or set(build.get("modules", ())) != set(getattr(self._manifest, "modules", ()))
        ):
            with _STATE_LOCK:
                _STATE_CACHE = None
            return {}, {}
        registry = envelope.get("registry")
        if not isinstance(registry, Mapping):
            with _STATE_LOCK:
                _STATE_CACHE = None
            return {}, {}
        raw_capabilities = registry.get("capabilities")
        raw_bindings = registry.get("bindings")
        if not isinstance(raw_capabilities, list) or not isinstance(raw_bindings, list):
            with _STATE_LOCK:
                _STATE_CACHE = None
            return {}, {}
        capabilities: dict[str, TypeCapability] = {}
        for raw in raw_capabilities:
            try:
                capability = TypeCapability.model_validate_json(json.dumps(raw, ensure_ascii=False))
            except (ValidationError, TypeError):
                continue
            selected = set(getattr(self._manifest, "chromatography_types", ()) or ())
            if capability.id not in selected:
                continue
            if capability.id in capabilities:
                return {}, {}
            capabilities[capability.id] = capability
        bindings: dict[tuple[str, str, str], WorkerAttestedBinding] = {}
        for raw in raw_bindings:
            if not isinstance(raw, Mapping) or raw.get("adapter_id") not in capabilities:
                continue
            try:
                item = self._parse_binding(raw)
            except Exception:
                continue
            key = (item.adapter_id, item.model_id, item.model_version)
            if key in bindings or item.adapter_id not in capabilities:
                with _STATE_LOCK:
                    _STATE_CACHE = None
                return {}, {}
            bindings[key] = item
        # A READY declaration without an exact, independently matched worker binding is
        # downgraded locally. This keeps model listings useful while failing closed.
        for type_id, capability in list(capabilities.items()):
            models: list[ModelOption] = []
            for model in capability.models:
                binding = bindings.get((type_id, model.id, model.version))
                if model.health.status == "READY" and not self._binding_matches_model(binding, capability, model):
                    models.append(model.model_copy(update={"health": _health("UNAVAILABLE", "WORKER_METADATA_BINDING_MISMATCH")}))
                else:
                    models.append(model)
            any_ready = any(model.health.status == "READY" for model in models)
            health = capability.health if any_ready else _health("UNAVAILABLE", "WORKER_METADATA_UNAVAILABLE")
            tools = [
                tool.model_copy(update={"health": health if not any_ready and tool.health.status == "READY" else tool.health})
                for tool in capability.tools
            ]
            capabilities[type_id] = capability.model_copy(update={"models": models, "tools": tools, "health": health})
        with _STATE_LOCK:
            _STATE_CACHE = (digest, capabilities, bindings)
        return capabilities, bindings

    @staticmethod
    def _parse_binding(raw: object) -> WorkerAttestedBinding:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid binding")
        adapter_id = raw.get("adapter_id")
        model_id = raw.get("model_id")
        model_version = raw.get("model_version")
        if not all(isinstance(value, str) and value for value in (adapter_id, model_id, model_version)):
            raise ValueError("binding identity missing")
        attestation = raw.get("attestation")
        tool_schemas = raw.get("tool_schemas")
        if not isinstance(attestation, Mapping) or set(attestation) != _ATTESTATION_FIELDS or not isinstance(tool_schemas, Mapping):
            raise ValueError("binding attestation invalid")
        manifest = AdapterManifest.model_validate(raw.get("manifest"))
        if (manifest.adapter_id, manifest.model_id, manifest.model_version) != (adapter_id, model_id, model_version):
            raise ValueError("binding manifest identity mismatch")
        normalized_schemas: dict[str, Mapping[str, Any]] = {}
        for tool_id, schema_pair in tool_schemas.items():
            if not isinstance(tool_id, str) or not isinstance(schema_pair, Mapping):
                raise ValueError("tool schema entry invalid")
            if set(schema_pair) != {"input_schema", "output_schema", "input_schema_sha256", "output_schema_sha256"}:
                raise ValueError("tool schema fields invalid")
            for schema_name in ("input_schema", "output_schema"):
                schema = schema_pair[schema_name]
                if not isinstance(schema, dict) or schema_pair[f"{schema_name}_sha256"] != _canonical_sha256(schema):
                    raise ValueError("tool schema digest mismatch")
            normalized_schemas[tool_id] = dict(schema_pair)
        catalog = raw.get("catalog")
        proof = raw.get("catalog_source_proof")
        entry = raw.get("registry_entry")
        domain = raw.get("sfc_domain")
        parsed_catalog = None
        if adapter_id == "sfc":
            parsed_catalog = ApiPredictionRegistry._load_sfc_catalog(catalog, entry, proof, model_id, model_version, attestation)
        return WorkerAttestedBinding(
            adapter_id=adapter_id,
            model_id=model_id,
            model_version=model_version,
            manifest=manifest,
            attestation=dict(attestation),
            tool_schemas=normalized_schemas,
            allowed_tool=raw.get("allowed_tool"),
            sfc_domain_data=dict(domain) if isinstance(domain, Mapping) else None,
            registry_entry=dict(entry) if isinstance(entry, Mapping) else None,
            catalog_data=dict(catalog) if isinstance(catalog, Mapping) else None,
            catalog_source_proof=dict(proof) if isinstance(proof, Mapping) else None,
            _catalog=parsed_catalog,
        )

    @staticmethod
    def _load_sfc_catalog(catalog: object, entry: object, proof: object, model_id: str, model_version: str, attestation: Mapping[str, Any]) -> Any:
        if not isinstance(catalog, Mapping) or not isinstance(entry, Mapping):
            raise ValueError("SFC catalog or registry entry missing")
        raw_path = catalog.get("path")
        raw_scope_path = catalog.get("scope_storage_path")
        digest = catalog.get("sha256")
        if not isinstance(raw_path, str) or not isinstance(raw_scope_path, str) or not isinstance(digest, str):
            raise ValueError("SFC catalog binding incomplete")
        configured_path = os.environ.get("CHROM_SFC_CANDIDATE_DB_PATH", "").strip()
        configured_scope = os.environ.get("CHROM_SFC_SCOPE_STORAGE_PATH", "").strip()
        if not configured_path or not configured_scope:
            raise ValueError("SFC trusted catalog and scope storage paths are required")
        configured_catalog = Path(configured_path).expanduser()
        configured_scope_path = Path(configured_scope).expanduser()
        snapshot_catalog = Path(raw_path)
        snapshot_scope = Path(raw_scope_path)
        if (
            not configured_catalog.is_absolute() or not configured_scope_path.is_absolute()
            or ".." in configured_catalog.parts or ".." in configured_scope_path.parts
        ):
            raise ValueError("SFC catalog path is not configured")
        # Resolve only locally configured paths first. Snapshot path strings are
        # compared below without filesystem access before any snapshot-derived IO.
        from chrom_platform.assembly.prediction_metadata import _reject_reparse_chain

        _reject_reparse_chain(configured_catalog)
        _reject_reparse_chain(configured_scope_path)
        path = configured_catalog.resolve(strict=True)
        scope_path = configured_scope_path.resolve(strict=False)
        _reject_reparse_chain(path)
        _reject_reparse_chain(scope_path)
        def path_identity(value: Path) -> str:
            return os.path.normcase(os.path.abspath(str(value)))

        def matches_configured_identity(snapshot: Path, configured: Path, canonical: Path) -> bool:
            return (
                snapshot.is_absolute()
                and ".." not in snapshot.parts
                and path_identity(snapshot) in {path_identity(configured), path_identity(canonical)}
            )

        if not (
            matches_configured_identity(snapshot_catalog, configured_catalog, path)
            and matches_configured_identity(snapshot_scope, configured_scope_path, scope_path)
        ):
            raise ValueError("SFC catalog path is not configured")
        # The untrusted strings now name only the configured logical or canonical
        # paths; reject reparse chains and confirm both aliases reach that target.
        _reject_reparse_chain(snapshot_catalog)
        _reject_reparse_chain(snapshot_scope)
        snapshot_catalog_canonical = snapshot_catalog.resolve(strict=True)
        snapshot_scope_canonical = snapshot_scope.resolve(strict=False)
        if (
            path_identity(snapshot_catalog_canonical) != path_identity(path)
            or path_identity(snapshot_scope_canonical) != path_identity(scope_path)
        ):
            raise ValueError("SFC catalog path is not configured")
        for key in ("CHROM_SFC_MODEL_ROOT", "CHROM_SFC_REGISTRY_PATH"):
            asset = os.environ.get(key, "").strip()
            if asset:
                root = Path(asset).expanduser().resolve(strict=False)
                try:
                    scope_path.relative_to(root)
                except ValueError:
                    pass
                else:
                    raise ValueError("SFC scope storage path is inside an asset tree")
        if path == scope_path or scope_path == Path(f"{path}.scopes.sqlite3") or any(part.casefold() in {"services", "deliverables"} for part in scope_path.parts):
            raise ValueError("SFC scope storage path is not an independent writable store")
        if not scope_path.parent.is_dir() or not os.access(scope_path.parent, os.W_OK):
            raise ValueError("SFC scope storage parent is unavailable")
        actual = _sha256_file(path)
        if actual != digest:
            raise ValueError("SFC catalog hash mismatch")
        if entry.get("model_id") != model_id or str(entry.get("version")) != model_version:
            raise ValueError("SFC registry entry identity mismatch")
        if entry.get("artifact_sha256") != attestation.get("artifact_sha256"):
            raise ValueError("SFC registry entry artifact mismatch")
        if isinstance(proof, Mapping):
            proof_path = proof.get("catalog_path")
            reference_path = proof.get("reference_path")
            checks = proof.get("check_flags")
            if not isinstance(proof_path, str) or not isinstance(reference_path, str) or not isinstance(checks, list):
                raise ValueError("SFC catalog proof fields invalid")
            proof_catalog = Path(proof_path)
            if (
                not matches_configured_identity(proof_catalog, configured_catalog, path)
                or not Path(reference_path).is_absolute()
                or ".." in Path(reference_path).parts
            ):
                raise ValueError("SFC proof paths invalid")
            full_reference = os.environ.get("CHROM_SFC_FULL_REFERENCE_PATH", "").strip()
            full_reference_sha = os.environ.get("CHROM_SFC_FULL_REFERENCE_SHA256", "").strip()
            if not full_reference or not full_reference_sha:
                raise ValueError("SFC proof reference is not locally configured")
            trusted_reference = Path(full_reference).expanduser()
            proof_reference = Path(reference_path)
            if (
                not trusted_reference.is_absolute()
                or ".." in trusted_reference.parts
            ):
                raise ValueError("SFC proof reference is not locally configured")
            _reject_reparse_chain(proof_catalog)
            _reject_reparse_chain(trusted_reference)
            trusted_reference_canonical = trusted_reference.resolve(strict=True)
            _reject_reparse_chain(trusted_reference_canonical)
            if not matches_configured_identity(proof_reference, trusted_reference, trusted_reference_canonical):
                raise ValueError("SFC proof reference is not locally configured")
            _reject_reparse_chain(proof_reference)
            if (
                path_identity(proof_catalog.resolve(strict=True)) != path_identity(path)
                or path_identity(proof_reference.resolve(strict=True)) != path_identity(trusted_reference_canonical)
            ):
                raise ValueError("SFC proof paths invalid")
            if (
                not isinstance(proof.get("reference_sha256"), str)
            ):
                raise ValueError("SFC proof paths invalid")
            reference = Path(reference_path)
            if not reference.is_file() or _sha256_file(reference) != proof["reference_sha256"] or proof["reference_sha256"] != full_reference_sha:
                raise ValueError("SFC proof reference hash mismatch")
            from chrom_platform.adapters.sfc.adapter import SfcCatalogSourceProof

            try:
                parsed_proof = SfcCatalogSourceProof(
                    model_id=proof["model_id"], model_version=proof["model_version"],
                    artifact_sha256=proof["artifact_sha256"], business_scope_id=proof["business_scope_id"],
                    input_schema_version=proof["input_schema_version"], output_schema_version=proof["output_schema_version"],
                    catalog_path=proof_path, catalog_sha256=proof["catalog_sha256"],
                    reference_path=reference_path, reference_sha256=proof["reference_sha256"],
                    fixture_version=proof["fixture_version"],
                    check_flags=tuple((item[0], item[1]) for item in checks if isinstance(item, list) and len(item) == 2),
                )
            except Exception as exc:
                raise ValueError("SFC catalog proof mismatch") from exc
            if (
                parsed_proof.model_id != model_id or parsed_proof.model_version != model_version
                or parsed_proof.artifact_sha256 != attestation.get("artifact_sha256")
                or parsed_proof.business_scope_id != attestation.get("business_scope_id")
                or parsed_proof.input_schema_version != attestation.get("input_schema_version")
                or parsed_proof.output_schema_version != attestation.get("output_schema_version")
                or path_identity(Path(parsed_proof.catalog_path).resolve(strict=False)) != path_identity(path)
                or parsed_proof.catalog_sha256 != actual
            ):
                raise ValueError("SFC catalog proof binding mismatch")
        elif model_id == "rs12-extra-trees-s80-seed42" and model_version == "1":
            raise ValueError("SFC pinned full-column proof missing")
        max_candidates = catalog.get("max_candidates", 100_000)
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= 100_000:
            raise ValueError("SFC candidate budget invalid")
        runtime = importlib.import_module("chrom_platform.adapters.sfc.runtime")
        value = runtime.SfcCandidateCatalog(path, max_candidates=max_candidates, scope_storage_path=scope_path)
        if entry.get("scope") not in {"single_column", "all_column"}:
            raise ValueError("SFC registry scope invalid")
        if entry.get("scope") == "single_column":
            if (
                not isinstance(entry.get("single_column_group"), str) or not entry["single_column_group"]
                or not isinstance(entry.get("single_column_slug"), str) or not entry["single_column_slug"]
                or not isinstance(entry.get("stationary_phase"), str) or not entry["stationary_phase"]
                or not isinstance(entry.get("mode"), str) or not entry["mode"]
                or not isinstance(entry.get("allowed_columns"), list) or not entry["allowed_columns"]
            ):
                raise ValueError("SFC single-column domain incomplete")
        elif entry.get("allowed_columns") not in ([], None):
            raise ValueError("SFC all-column domain contains single-column restrictions")
        value.bind_model_scope(
            model_id=model_id,
            model_version=model_version,
            scope=str(entry.get("scope")),
            allowed_columns=entry.get("allowed_columns") or (),
            stationary_phase=entry.get("stationary_phase"),
            mode=entry.get("mode"),
        )
        if entry.get("scope") == "all_column":
            value.verify_source_hash(expected_sha256=str(proof.get("catalog_sha256")) if isinstance(proof, Mapping) else digest)
        return value

    @staticmethod
    def _binding_matches_model(binding: WorkerAttestedBinding | None, capability: TypeCapability, model: ModelOption) -> bool:
        if binding is None:
            return False
        values = binding.attestation
        expected = {
            "adapter_id": capability.id,
            "model_id": model.id,
            "model_version": model.version,
            "business_scope_id": model.scope,
            "input_schema_version": model.input_schema_version,
            "output_schema_version": model.output_schema_version,
            "artifact_sha256": model.artifact_sha256,
            "preprocessing_profile_id": model.preprocessing_profile_id,
            "preprocessing_profile_sha256": model.preprocessing_profile_sha256,
        }
        if dict(values) != expected:
            return False
        manifest_values = binding.manifest.model_dump(mode="python")
        if any(manifest_values.get(name) != expected[name] for name in _ATTESTATION_FIELDS):
            return False
        if (
            manifest_values.get("data_origin") != "production"
            or manifest_values.get("schema_available") is not True
            or manifest_values.get("assets_available") is not True
        ):
            return False
        known_tools = {tool.id: tool for tool in capability.tools}
        for tool_id, schemas in binding.tool_schemas.items():
            tool = known_tools.get(tool_id)
            if tool is None:
                return False
            if tool.health.status == "READY" and (
                tool.schema_version != model.input_schema_version
                or tool.input_schema != schemas["input_schema"]
                or tool.output_schema != schemas["output_schema"]
            ):
                return False
        if not binding.tool_schemas:
            return False
        if capability.id == "sfc" and (
            binding.allowed_tool not in {"single_column", "full_column"}
            or binding.allowed_tool not in binding.tool_schemas
            or binding.candidate_catalog is None or binding.registry_entry is None or binding.sfc_domain is None
        ):
            return False
        if capability.id == "sfc":
            entry = binding.registry_entry or {}
            domain = model.sfc_domain
            domain_data = domain.model_dump(mode="python") if hasattr(domain, "model_dump") else {}
            entry_scope = entry.get("scope")
            entry_domain = {"model_scope": entry_scope}
            if entry_scope == "single_column":
                entry_domain.update({
                    "group_id": entry.get("single_column_slug"),
                    "group_label": entry.get("single_column_group"),
                    "stationary_phase": entry.get("stationary_phase"),
                    "mode": entry.get("mode"),
                    "allowed_columns": list(entry.get("allowed_columns") or ()),
                })
            domain_match = (
                isinstance(domain_data, Mapping)
                and all(domain_data.get(key) == value for key, value in entry_domain.items())
                and (entry_scope != "single_column" or isinstance(domain_data.get("group_label"), str) and bool(domain_data.get("group_label")))
            )
            if (
                not domain_match
                or entry_scope != getattr(domain, "model_scope", None)
                or entry.get("artifact_sha256") != model.artifact_sha256
                or binding.allowed_tool != ("single_column" if getattr(domain, "model_scope", None) == "single_column" else "full_column")
            ):
                return False
        return True

    def registered_types(self) -> tuple[str, ...]:
        capabilities, _ = self._read_state()
        return tuple(sorted(capabilities))

    def capabilities(self) -> list[TypeCapability]:
        capabilities, _ = self._read_state()
        return [capabilities[key] for key in sorted(capabilities)]

    def capability(self, type_id: str) -> TypeCapability:
        capabilities, _ = self._read_state()
        try:
            return capabilities[type_id]
        except KeyError as exc:
            raise KeyError(f"prediction adapter is not built: {type_id}") from exc

    def adapter(self, type_id: str) -> _ApiAdapterFacade:
        self.capability(type_id)
        return _ApiAdapterFacade(self, type_id)

    def binding_for(self, type_id: str, model_id: str, model_version: str) -> WorkerAttestedBinding | None:
        _, bindings = self._read_state()
        return bindings.get((type_id, model_id, model_version))

    def bindings_for(self, type_id: str) -> list[WorkerAttestedBinding]:
        _, bindings = self._read_state()
        return [value for key, value in bindings.items() if key[0] == type_id]

    def get_tool(self, chromatography_type: str, tool_id: str, schema_version: str) -> ToolCapability:
        try:
            capability = self.capability(chromatography_type)
        except KeyError as exc:
            if chromatography_type in set(getattr(self._manifest, "chromatography_types", ()) or ()):
                raise ModelUnavailableError(chromatography_type) from exc
            raise InputValidationError("prediction type is not supported") from exc
        tool = next((item for item in capability.tools if item.id == tool_id and item.schema_version == schema_version), None)
        if tool is None:
            raise InputValidationError("prediction tool or schema is not supported")
        if capability.health.status != "READY" or tool.health.status != "READY":
            raise ModelUnavailableError(chromatography_type)
        return tool

    def resolve_model(self, _ctx: object, request: object, *, tool: ToolCapability) -> ModelSnapshot:
        if not isinstance(request, BaseModel):
            raise PredictionContractError()
        type_id = getattr(request, "chromatography_type", None)
        capability = self.capability(type_id)
        if capability.health.status != "READY" or tool.health.status != "READY":
            raise ModelUnavailableError(type_id)
        selection = getattr(request, "model_selection", None)
        if type_id == "sfc" and getattr(selection, "mode", None) != "exact":
            raise PredictionContractError("SFC v1.1 requires exact model selection")
        if type_id in {"cc", "hplc", "gc"} and (
            getattr(selection, "mode", None) != "exact"
            or not getattr(selection, "preprocessing_profile_id", None)
            or not getattr(selection, "preprocessing_profile_sha256", None)
        ):
            raise PredictionContractError("该预测请求必须携带能力中声明的精确预处理版本")
        if type_id not in {"cc", "hplc", "gc"} and (
            getattr(selection, "preprocessing_profile_id", None) is not None
            or getattr(selection, "preprocessing_profile_sha256", None) is not None
        ):
            raise PredictionContractError("所选模型不支持该预处理版本")
        if getattr(selection, "mode", None) == "champion":
            if type_id in {"cc", "hplc", "gc"}:
                raise PredictionContractError("该预测必须使用能力中声明的精确预处理版本")
            candidates = [model for model in capability.models if model.scope == selection.scope_id]
        elif getattr(selection, "mode", None) == "exact":
            candidates = [model for model in capability.models if model.id == selection.model_id and model.version == selection.model_version]
            if type_id == "sfc" and len(candidates) != 1:
                raise PredictionContractError("SFC exact model id/version is missing or ambiguous")
            if type_id in {"cc", "hplc", "gc"} and candidates and not any(
                model.preprocessing_profile_id == selection.preprocessing_profile_id
                and model.preprocessing_profile_sha256 == selection.preprocessing_profile_sha256
                for model in candidates
            ):
                raise PredictionContractError("预处理版本已变化，请按当前模型能力重新选择")
            candidates = [model for model in candidates if (
                model.preprocessing_profile_id == selection.preprocessing_profile_id
                and model.preprocessing_profile_sha256 == selection.preprocessing_profile_sha256
            )]
        else:
            raise InputValidationError("unsupported model selection")
        ready = [model for model in candidates if model.health.status == "READY" and model.artifact_sha256]
        if len(ready) != 1:
            raise ModelUnavailableError(type_id)
        model = ready[0]
        binding = self.binding_for(type_id, model.id, model.version)
        if not self._binding_matches_model(binding, capability, model):
            raise ModelUnavailableError(type_id)
        return ModelSnapshot(
            id=model.id, version=model.version, business_scope_id=model.scope,
            artifact_sha256=model.artifact_sha256,
            input_schema_version=model.input_schema_version,
            output_schema_version=model.output_schema_version,
            preprocessing_profile_id=model.preprocessing_profile_id,
            preprocessing_profile_sha256=model.preprocessing_profile_sha256,
            score_semantics=model.score_semantics, threshold=model.threshold,
        )

    def validate_request(
        self,
        type_id: str,
        request: object,
        *,
        model_snapshot: ModelSnapshot,
        authorized_modifier_structures: Mapping[str, str] | None = None,
        run_preflight: bool = True,
    ) -> BaseModel:
        capability = self.capability(type_id)
        if capability.health.status != "READY":
            raise ModelUnavailableError(type_id)
        dto_types = _dto_types(type_id)
        if not isinstance(request, BaseModel) or not dto_types or not isinstance(request, dto_types):
            raise PredictionContractError()
        tool_id = getattr(request, "tool", None)
        if getattr(request, "chromatography_type", None) != type_id:
            raise PredictionContractError()
        tool = next((item for item in capability.tools if item.id == tool_id), None)
        if tool is None or tool.health.status != "READY":
            raise ModelUnavailableError(type_id)
        if not isinstance(model_snapshot, ModelSnapshot):
            raise PredictionContractError("a verified model snapshot is required")
        binding = self.binding_for(type_id, model_snapshot.id, model_snapshot.version)
        model = next((item for item in capability.models if item.id == model_snapshot.id and item.version == model_snapshot.version), None)
        if model is None or not self._binding_matches_model(binding, capability, model):
            raise ModelUnavailableError(type_id)
        expected = (
            model.id, model.version, model.scope, model.artifact_sha256,
            model.input_schema_version, model.output_schema_version,
            model.preprocessing_profile_id, model.preprocessing_profile_sha256,
        )
        actual = (
            model_snapshot.id, model_snapshot.version, model_snapshot.business_scope_id,
            model_snapshot.artifact_sha256, model_snapshot.input_schema_version,
            model_snapshot.output_schema_version, model_snapshot.preprocessing_profile_id,
            model_snapshot.preprocessing_profile_sha256,
        )
        if expected != actual or request.schema_version != model_snapshot.input_schema_version:
            raise ModelUnavailableError(type_id)
        if request.model_selection.mode == "exact" and (
            request.model_selection.model_id != model.id
            or request.model_selection.model_version != model.version
            or request.model_selection.preprocessing_profile_id != model.preprocessing_profile_id
            or request.model_selection.preprocessing_profile_sha256 != model.preprocessing_profile_sha256
        ):
            raise PredictionContractError("所选模型与当前 Worker 快照不一致")
        if request.model_selection.mode == "champion" and request.model_selection.scope_id != model.scope:
            raise PredictionContractError("prediction scope does not match the bound model")
        try:
            if type_id == "tlc":
                validated = importlib.import_module("chrom_platform.adapters.tlc.validation").validate_tlc_request(request)
            elif type_id == "cc":
                catalog = importlib.import_module("chrom_platform.adapters.cc.condition_catalog").CcConditionCatalog()
                validated = importlib.import_module("chrom_platform.adapters.cc.validation").validate_cc_request(request, catalog)
            elif type_id == "hplc":
                validated = importlib.import_module("chrom_platform.adapters.hplc.validation").validate_hplc_request(request)
            elif type_id == "gc":
                validated = importlib.import_module("chrom_platform.adapters.gc.validation").validate_gc_request(request)
            elif type_id == "sfc":
                validated = importlib.import_module("chrom_platform.adapters.sfc.validation").validate_sfc_request(request, allowed_tool=str(binding.allowed_tool))
                if not run_preflight:
                    return validated
                if request.project_id is None or not request.project_id.strip():
                    raise APIError(422, "PREDICTION_INPUT_INVALID", "SFC request project binding is invalid")
                catalog = binding.candidate_catalog
                entry = binding.registry_entry
                if catalog is None or entry is None:
                    raise ModelUnavailableError("sfc")
                runtime = importlib.import_module("chrom_platform.adapters.sfc.runtime")
                try:
                    resolved = catalog.resolve_with_policy(
                        request.input.candidate_scope_id,
                        project_id=request.project_id,
                        model_id=model.id,
                        model_version=model.version,
                    )
                except runtime.SfcCandidateScopeError as exc:
                    raise runtime.SfcRequestValidationError("SFC candidate scope is unknown or not bound to this request") from exc
                importlib.import_module("chrom_platform.adapters.sfc.validation").validate_sfc_preflight(
                    request,
                    entry=SimpleNamespace(**entry),
                    candidates=resolved["candidates"],
                    allowed_tool=str(binding.allowed_tool),
                    authorized_modifier_structures=authorized_modifier_structures,
                )
            else:
                raise InputValidationError("prediction type is not supported")
        except APIError:
            raise
        except (OSError, KeyError) as exc:
            raise ModelUnavailableError(type_id) from exc
        except RuntimeError as exc:
            if exc.__class__.__module__.startswith("chrom_platform.adapters.sfc"):
                raise ModelUnavailableError(type_id) from exc
            raise
        return validated

    def execute_snapshot(self, *_args: object, **_kwargs: object) -> object:
        raise ModelUnavailableError("api")

