"""PredictionAdapter implementation for the two SFC tools."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.modules.prediction.adapters import (
    AdapterCapability,
    AdapterManifest,
    ModelOption,
    ModelUnavailableError,
    ModelVerificationEvidence,
    PredictionAdapter,
    PredictionContractError,
    PredictionLimits,
    SfcAllColumnDomain,
    SfcSingleColumnDomain,
    StructureCount,
    ToolCapability,
    VerifiedModelHandle,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.schemas import (
    Applicability,
    Metric,
    ModelSnapshot,
    SfcFullColumnExecutionV10,
    SfcFullColumnPredictionCreate,
    SfcPredictionResult,
    SfcSingleColumnExecutionV10,
    SfcSingleColumnPredictionCreate,
    Warning,
)

from .runtime import (
    ALLOWED_NAN_FEATURES,
    SfcAssetUnavailable,
    SfcCandidateCatalog,
    SfcModelRegistry,
    SfcRequestValidationError,
    SfcRuntime,
)


_RS12_FULL_MODEL_ID = "rs12-extra-trees-s80-seed42"
_RS12_FULL_MODEL_VERSION = "1"
_RS12_FULL_FIXTURE_VERSION = "sfc-rs12-full-column-reference-v2"
_RS12_FULL_VERIFICATION_CHECKS = (
    "manifest", "schema", "artifact", "environment", "golden", "anomaly", "preprocessing",
)


@dataclass(frozen=True)
class SfcCatalogSourceProof:
    """Process-local proof that an exact SFC reference verified its catalog source."""

    model_id: str
    model_version: str
    artifact_sha256: str
    business_scope_id: str
    input_schema_version: str
    output_schema_version: str
    catalog_path: str
    catalog_sha256: str
    reference_path: str
    reference_sha256: str
    fixture_version: str
    check_flags: tuple[tuple[str, bool], ...]

    def __post_init__(self) -> None:
        if (
            self.model_id != _RS12_FULL_MODEL_ID
            or self.model_version != _RS12_FULL_MODEL_VERSION
            or self.fixture_version != _RS12_FULL_FIXTURE_VERSION
            or self.input_schema_version != "1.1"
            or self.output_schema_version != "1.0"
            or not self.business_scope_id
            or not Path(self.catalog_path).is_absolute()
            or not Path(self.reference_path).is_absolute()
            or len(self.catalog_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.catalog_sha256)
            or len(self.reference_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.reference_sha256)
            or tuple(name for name, _ in self.check_flags) != _RS12_FULL_VERIFICATION_CHECKS
            or any(value is not True for _, value in self.check_flags)
        ):
            raise SfcAssetUnavailable("MODEL_VERIFICATION_MISMATCH")


class SfcVerificationResult(dict[str, bool]):
    """Boolean gate result with optional process-local reference proof metadata."""

    def __init__(self, values: Mapping[str, bool], *, catalog_source_proof: SfcCatalogSourceProof | None = None) -> None:
        super().__init__(values)
        self.catalog_source_proof = catalog_source_proof


def _health(status: str = "UNAVAILABLE", reason: str | None = None) -> CapabilityHealth:
    return CapabilityHealth(status=status, reason=reason, checked_at=datetime.now(timezone.utc))


def _schemas(tool: str) -> tuple[dict[str, object], dict[str, object]]:
    if tool == "single_column":
        request_model = SfcSingleColumnPredictionCreate
    elif tool == "full_column":
        request_model = SfcFullColumnPredictionCreate
    else:
        raise ValueError("unsupported SFC tool")
    return request_model.model_json_schema(), SfcPredictionResult.model_json_schema()


class SfcAdapter(PredictionAdapter):
    type_id = "sfc"
    request_types = (
        SfcSingleColumnPredictionCreate,
        SfcFullColumnPredictionCreate,
        SfcSingleColumnExecutionV10,
        SfcFullColumnExecutionV10,
    )

    def __init__(
        self,
        *,
        runtime: SfcRuntime,
        model_id: str,
        model_version: str,
        artifact_sha256: str,
        business_scope_id: str,
        verification_evidence: ModelVerificationEvidence | None = None,
        candidate_catalog: SfcCandidateCatalog | None = None,
        catalog_source_proof: SfcCatalogSourceProof | None = None,
        manifest_version: str = "sfc-adapter-1",
    ) -> None:
        self.runtime = runtime
        self.candidate_catalog = candidate_catalog
        self._model_id = model_id
        self._model_version = model_version
        self._artifact_sha256 = artifact_sha256
        if not isinstance(business_scope_id, str) or not business_scope_id.strip():
            raise ValueError("business_scope_id is required")
        if (
            runtime.bound_model_id != model_id
            or runtime.bound_model_version != model_version
            or runtime.bound_artifact_sha256 != artifact_sha256.lower()
            or runtime.business_scope_id != business_scope_id
        ):
            raise ValueError("runtime and adapter model binding do not match")
        self.business_scope_id = business_scope_id
        entry = runtime.registry.entry(model_id, version=model_version)
        rs12_full = (model_id, model_version) == (_RS12_FULL_MODEL_ID, _RS12_FULL_MODEL_VERSION)
        proof = catalog_source_proof
        evidence = verification_evidence
        if proof is not None and not rs12_full:
            raise SfcAssetUnavailable("MODEL_VERIFICATION_MISMATCH")
        if rs12_full:
            if (
                not isinstance(proof, SfcCatalogSourceProof)
                or candidate_catalog is None
                or entry.scope != "all_column"
                or runtime.allowed_tool != "full_column"
                or evidence is None
                or proof.catalog_path != str(Path(candidate_catalog.path).resolve())
                or proof.model_id != model_id
                or proof.model_version != model_version
                or proof.artifact_sha256 != artifact_sha256.lower()
                or proof.business_scope_id != business_scope_id
                or proof.input_schema_version != evidence.input_schema_version
                or proof.output_schema_version != evidence.output_schema_version
                or evidence.input_schema_version != "1.1"
                or evidence.output_schema_version != "1.0"
                or not all((
                    evidence.manifest_verified,
                    evidence.schema_verified,
                    evidence.artifact_sha_verified,
                    evidence.environment_verified,
                    evidence.golden_sample_verified,
                    evidence.anomaly_checks_verified,
                ))
                or evidence.model_id != model_id
                or evidence.model_version != model_version
                or evidence.artifact_sha256 != artifact_sha256.lower()
                or evidence.business_scope_id != business_scope_id
                or not isinstance(evidence.schema_by_tool.get("full_column"), Mapping)
                or not {"input_schema", "output_schema"}.issubset(evidence.schema_by_tool["full_column"])
            ):
                raise SfcAssetUnavailable("MODEL_VERIFICATION_MISMATCH")
        if candidate_catalog is not None:
            candidate_catalog.bind_model_scope(
                model_id=model_id,
                model_version=model_version,
                scope=str(entry.scope),
                allowed_columns=entry.allowed_columns,
                stationary_phase=entry.stationary_phase,
                mode=entry.mode,
            )
            if entry.scope == "all_column":
                if rs12_full:
                    assert isinstance(proof, SfcCatalogSourceProof)
                    candidate_catalog.verify_source_hash(expected_sha256=proof.catalog_sha256)
                else:
                    candidate_catalog.verify_source_hash()
        if runtime.allowed_tool == "single_column":
            self.request_types = (SfcSingleColumnPredictionCreate, SfcSingleColumnExecutionV10)
        elif runtime.allowed_tool == "full_column":
            self.request_types = (SfcFullColumnPredictionCreate, SfcFullColumnExecutionV10)
        else:  # defensive: SfcRuntime validates this before construction
            raise ValueError("unsupported SFC model scope")
        input_schema, output = _schemas(runtime.allowed_tool)
        unavailable = _health(reason="MODEL_VERIFICATION_REQUIRED")
        models = []
        for candidate_entry in runtime.registry.entries:
            if candidate_entry.scope == "single_column":
                domain = SfcSingleColumnDomain(
                    model_scope="single_column",
                    group_id=candidate_entry.single_column_slug,
                    group_label=candidate_entry.single_column_group,
                    stationary_phase=candidate_entry.stationary_phase,
                    mode=candidate_entry.mode,
                    allowed_columns=list(candidate_entry.allowed_columns),
                )
            else:
                domain = SfcAllColumnDomain(model_scope="all_column")
            models.append(ModelOption(
                id=candidate_entry.model_id,
                version=candidate_entry.version,
                task_type="sfc",
                scope=business_scope_id,
                artifact_sha256=candidate_entry.artifact_sha256,
                input_schema_version="1.1",
                output_schema_version="1.0",
                score_semantics=candidate_entry.score_semantics,
                threshold=candidate_entry.threshold,
                sfc_domain=domain,
                health=unavailable,
            ))
        limits = PredictionLimits(max_rows=200, max_candidates=100_000, max_evaluations=100_000)
        self.capability = AdapterCapability(
            id="sfc",
            display_name="Supercritical fluid chromatography",
            tools=[ToolCapability(
                id=runtime.allowed_tool,
                schema_version="1.1",
                input_schema=input_schema,
                output_schema=output,
                structure_count=StructureCount(min=2, max=2, semantics="pair"),
                model_constraints={
                    "feature_count": len(runtime.registry.entry(model_id, version=model_version).feature_names),
                    "feature_order": list(runtime.registry.entry(model_id, version=model_version).feature_names),
                    "target": runtime.registry.entry(model_id, version=model_version).target,
                    "threshold": runtime.registry.entry(model_id, version=model_version).threshold,
                    "score_semantics": "positive_class_probability",
                    "modifier_auto_normalization": False,
                    "feature_value_policy": {
                        "allowed_nan_features": list(ALLOWED_NAN_FEATURES),
                        "allowed_nan_action": "preserve and warn",
                        "infinity": "reject",
                        "other_nan": "reject",
                    },
                    "applicability": (
                        "observed_complete_candidate_tuples"
                        if entry.scope == "all_column" else "bound_model_domain"
                    ),
                    "generalization_claim": "not_established",
                },
                units={"modifier_total_fraction": "fraction", "modifier_components.fraction": "fraction"},
                supported_visualizations=[],
                limits=limits,
                health=unavailable,
            )],
            models=models,
            limits=limits,
            health=unavailable,
        )
        self.manifest = AdapterManifest(
            adapter_id="sfc",
            model_id=model_id,
            model_version=model_version,
            manifest_version=manifest_version,
            input_schema_version="1.1",
            output_schema_version="1.0",
            artifact_sha256=artifact_sha256,
            business_scope_id=business_scope_id,
            schema_available=False,
            assets_available=False,
            data_origin="production",
        )
        self.verified_handle: VerifiedModelHandle | None = None
        self.verification_evidence = verification_evidence
        if verification_evidence is not None and all((
            verification_evidence.manifest_verified,
            verification_evidence.schema_verified,
            verification_evidence.artifact_sha_verified,
            verification_evidence.environment_verified,
            verification_evidence.golden_sample_verified,
            verification_evidence.anomaly_checks_verified,
        )):
            self.verified_handle = issue_verified_model_handle(self.manifest, verification_evidence)
            self.manifest = self.manifest.model_copy(update={"schema_available": True, "assets_available": True})
            ready = _health("READY")
            self.capability = self.capability.model_copy(
                update={
                    "health": ready,
                    "models": [
                        item.model_copy(update={"health": ready})
                        if item.id == model_id and item.version == model_version
                        else item
                        for item in self.capability.models
                    ],
                    "tools": [item.model_copy(update={"health": ready}) for item in self.capability.tools],
                }
            )

    def binding_for(self, model_id: str, model_version: str) -> "SfcAdapter | None":
        """Return this leaf only for its immutable exact model key."""
        if (model_id, model_version) == (self._model_id, self._model_version):
            return self
        return None

    def validate_request(self, request: BaseModel) -> BaseModel:
        if not isinstance(request, self.request_types):
            raise PredictionContractError(f"SFC request must use {self.runtime.allowed_tool} DTO")
        if request.chromatography_type != "sfc":
            raise PredictionContractError("SFC adapter received another chromatography type")
        if request.tool != self.runtime.allowed_tool:
            raise PredictionContractError("SFC request tool is outside the bound model scope")
        return request

    def preflight_request(
        self,
        request: BaseModel,
        *,
        project_id: str,
        model_snapshot: ModelSnapshot,
        authorized_modifier_structures: Mapping[str, str] | None = None,
    ) -> BaseModel:
        """Validate catalog binding and pinned inputs before persistence."""
        request = self.validate_request(request)
        if request.project_id != project_id:
            raise PredictionContractError("SFC request project binding is invalid")
        if (
            model_snapshot.id != self._model_id
            or model_snapshot.version != self._model_version
            or model_snapshot.business_scope_id != self.business_scope_id
        ):
            raise ModelUnavailableError("sfc")
        try:
            self.runtime.preflight(
                request,
                authorized_modifier_structures=authorized_modifier_structures,
            )
        except SfcRequestValidationError:
            raise
        except SfcAssetUnavailable:
            raise ModelUnavailableError("sfc") from None
        return request

    def predict(self, request: BaseModel) -> SfcPredictionResult:
        request = self.validate_request(request)
        if self.verified_handle is None:
            raise SfcAssetUnavailable("MODEL_VERIFICATION_REQUIRED")
        try:
            rows, warning_messages, entry, scope_provenance, evaluated_count = self.runtime.predict(
                request, return_scope_provenance=True
            )
        except SfcAssetUnavailable as exc:
            raise ModelUnavailableError("sfc") from exc
        warnings = [Warning(code="SFC_RUNTIME_WARNING", message=message) for message in warning_messages]
        applicability = self._applicability(entry, warnings, scope_provenance)
        return SfcPredictionResult(
            task_type="sfc",
            schema_version=self.verified_handle.output_schema_version,
            metrics=[Metric(
                key="returned_candidate_count",
                label="Returned candidates",
                value=len(rows),
                unit="candidates",
                origin="derived",
            )],
            rows=rows,
            visualizations=[],
            applicability=applicability,
            candidate_scope_provenance=scope_provenance,
            evaluated_candidate_count=evaluated_count,
            score_semantics=entry.score_semantics,
        )

    def predict_with_normalized_structures(
        self,
        request: BaseModel,
        normalized_structures: Sequence[str] | None,
        *,
        normalized_modifier_structures: Mapping[str, str] | None = None,
    ) -> SfcPredictionResult:
        """Execute a queued request from its immutable input snapshot only."""
        request = self.validate_request(request)
        if self.verified_handle is None:
            raise SfcAssetUnavailable("MODEL_VERIFICATION_REQUIRED")
        try:
            rows, warning_messages, entry, scope_provenance, evaluated_count = self.runtime.predict(
                request,
                normalized_structures=normalized_structures,
                normalized_modifier_structures=normalized_modifier_structures,
                require_snapshots=True,
                return_scope_provenance=True,
            )
        except SfcAssetUnavailable as exc:
            raise ModelUnavailableError("sfc") from exc
        warnings = [Warning(code="SFC_RUNTIME_WARNING", message=message) for message in warning_messages]
        applicability = self._applicability(entry, warnings, scope_provenance)
        return SfcPredictionResult(
            task_type="sfc",
            schema_version=self.verified_handle.output_schema_version,
            metrics=[Metric(key="returned_candidate_count", label="Returned candidates", value=len(rows), unit="candidates", origin="derived")],
            rows=rows,
            visualizations=[],
            applicability=applicability,
            candidate_scope_provenance=scope_provenance,
            evaluated_candidate_count=evaluated_count,
            score_semantics=entry.score_semantics,
        )

    @staticmethod
    def _applicability(
        entry: Any, warnings: list[Warning], scope: Mapping[str, Any] | None = None
    ) -> Applicability:
        policy = scope.get("policy") if scope is not None else None
        coupled = isinstance(policy, Mapping) and policy.get("expansion_mode") == "coupled_discrete_conditions"
        if coupled:
            unobserved = int(scope.get("unobserved_count", 0))
            if unobserved:
                total = int(scope.get("candidate_count", 0))
                warnings.extend((
                    Warning(
                        code="UNOBSERVED_CONDITION_COMBINATION",
                        message=f"完整候选范围共 {total} 个条件组合，其中 {unobserved} 个未在源目录中作为完整条件元组出现。",
                    ),
                    Warning(
                        code="COMBINATION_GENERALIZATION_NOT_ESTABLISHED",
                        message="这些组内离散温度与流速组合的泛化性能尚未验证；以上计数基于完整候选范围，不是返回行数。",
                    ),
                ))
            supported_scope = "coupled discrete conditions within observed six-axis hardware groups; generalization not established"
        elif entry.scope == "all_column":
            warnings.append(Warning(
                code="SFC_OBSERVED_CANDIDATE_DOMAIN",
                message="候选条件来自数据源中实际出现的完整条件元组；轴选项不代表任意组合都有效。",
            ))
            supported_scope = "observed complete candidate tuples; generalization not established"
        else:
            supported_scope = f"SFC model {entry.model_id}"
        if entry.scope == "all_column":
            warnings.append(Warning(
                code="SFC_GENERALIZATION_NOT_ESTABLISHED",
                message="模型泛化及修饰剂的科学适用范围尚未由模型元数据确立。",
            ))
        return Applicability(supported_scope=supported_scope, warnings=warnings)


class SfcDispatchAdapter:
    """One registered SFC adapter dispatching through isolated exact bindings."""

    type_id = "sfc"
    manifest = None
    verified_handle = None
    request_types = SfcAdapter.request_types

    def __init__(self, bindings: Sequence[SfcAdapter]) -> None:
        if not bindings:
            raise ValueError("at least one SFC binding is required")
        by_key: dict[tuple[str, str], SfcAdapter] = {}
        for binding in bindings:
            key = (binding._model_id, binding._model_version)
            if key in by_key:
                raise ValueError("duplicate SFC exact model binding")
            by_key[key] = binding
        self.sfc_bindings = MappingProxyType(by_key)
        self.capability = self._combined_capability(tuple(bindings))

    @staticmethod
    def _combined_capability(bindings: tuple[SfcAdapter, ...]) -> AdapterCapability:
        first = bindings[0].capability
        models_by_key = {(model.id, model.version): model for model in first.models}
        tools_by_id = {tool.id: tool for tool in first.tools}
        for binding in bindings[1:]:
            for model in binding.capability.models:
                key = (model.id, model.version)
                previous = models_by_key.get(key)
                if previous is not None and (
                    previous.artifact_sha256 != model.artifact_sha256
                    or previous.input_schema_version != model.input_schema_version
                    or previous.output_schema_version != model.output_schema_version
                    or previous.sfc_domain != model.sfc_domain
                ):
                    raise ValueError("SFC exact model metadata conflicts between bindings")
                selected = binding.binding_for(*key)
                if selected is not None:
                    models_by_key[key] = model
            for tool in binding.capability.tools:
                previous = tools_by_id.get(tool.id)
                if previous is not None and previous.schema_version != tool.schema_version:
                    raise ValueError("SFC tool schema version conflicts between bindings")
                tools_by_id[tool.id] = tool
        # A model's public binding metadata comes from its own leaf when present.
        ready = any(binding.verified_handle is not None for binding in bindings)
        health = _health("READY") if ready else _health(reason="MODEL_VERIFICATION_REQUIRED")
        return AdapterCapability(
            id="sfc",
            display_name=first.display_name,
            tools=list(tools_by_id.values()),
            models=list(models_by_key.values()),
            limits=first.limits,
            health=health,
        )

    @property
    def runtime(self):
        return next(iter(self.sfc_bindings.values())).runtime if len(self.sfc_bindings) == 1 else None

    @property
    def candidate_catalog(self):
        return next(iter(self.sfc_bindings.values())).candidate_catalog if len(self.sfc_bindings) == 1 else None

    def binding_for(self, model_id: str, model_version: str) -> SfcAdapter | None:
        return self.sfc_bindings.get((model_id, model_version))

    def catalog_for_scope(self, scope_id: str) -> tuple[SfcAdapter, SfcCandidateCatalog, Mapping[str, Any]]:
        matches = []
        for binding in self.sfc_bindings.values():
            catalog = binding.candidate_catalog
            if catalog is None:
                continue
            stored = catalog.scope_binding(scope_id)
            if stored is not None and (stored.get("model_id"), stored.get("model_version")) == (
                binding._model_id, binding._model_version
            ):
                matches.append((binding, catalog, stored))
        if len(matches) != 1:
            raise KeyError(scope_id)
        return matches[0]

    def _binding_for_request(self, request: BaseModel) -> SfcAdapter:
        selection = getattr(request, "model_selection", None)
        if getattr(selection, "mode", None) != "exact":
            raise PredictionContractError("SFC dispatch requires an exact model selection")
        binding = self.binding_for(selection.model_id, selection.model_version)
        if binding is None:
            raise ModelUnavailableError("sfc")
        return binding

    def validate_request(self, request: BaseModel) -> BaseModel:
        return self._binding_for_request(request).validate_request(request)

    def preflight_request(
        self,
        request: BaseModel,
        *,
        project_id: str,
        model_snapshot: ModelSnapshot,
        authorized_modifier_structures: Mapping[str, str] | None = None,
    ) -> BaseModel:
        return self._binding_for_request(request).preflight_request(
            request,
            project_id=project_id,
            model_snapshot=model_snapshot,
            authorized_modifier_structures=authorized_modifier_structures,
        )

    def predict(self, request: BaseModel) -> SfcPredictionResult:
        return self._binding_for_request(request).predict(request)

    def predict_with_normalized_structures(
        self,
        request: BaseModel,
        normalized_structures: Sequence[str] | None,
        *,
        normalized_modifier_structures: Mapping[str, str] | None = None,
    ) -> SfcPredictionResult:
        return self._binding_for_request(request).predict_with_normalized_structures(
            request,
            normalized_structures,
            normalized_modifier_structures=normalized_modifier_structures,
        )


def build_sfc_adapter(
    *,
    registry_path: str,
    model_root: str,
    model_id: str,
    model_version: str,
    artifact_sha256: str,
    business_scope_id: str,
    expected_registry_sha256: str,
    structure_resolver: Mapping[str, str] | Callable[[str], str],
    candidate_resolver: Mapping[str, Sequence[Any]] | Callable[[str], Sequence[Any]],
    verification_evidence: ModelVerificationEvidence | None = None,
    candidate_catalog: SfcCandidateCatalog | None = None,
    catalog_source_proof: SfcCatalogSourceProof | None = None,
) -> SfcAdapter:
    """Build an adapter from explicit trusted handles; no path comes from a request."""
    registry = SfcModelRegistry(
        registry_path=registry_path,
        model_root=model_root,
        expected_registry_sha256=expected_registry_sha256,
    )
    runtime = SfcRuntime(
        registry=registry,
        bound_model_id=model_id,
        bound_model_version=model_version,
        bound_artifact_sha256=artifact_sha256,
        business_scope_id=business_scope_id,
        structure_resolver=structure_resolver,
        candidate_resolver=candidate_resolver,
    )
    return SfcAdapter(
        runtime=runtime,
        model_id=model_id,
        model_version=model_version,
        artifact_sha256=artifact_sha256,
        business_scope_id=business_scope_id,
        verification_evidence=verification_evidence,
        candidate_catalog=candidate_catalog,
        catalog_source_proof=catalog_source_proof,
    )


__all__ = ["SfcAdapter", "build_sfc_adapter"]
