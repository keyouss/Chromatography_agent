"""PredictionAdapter implementation for the controlled CC runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.core.errors import InputValidationError
from chrom_platform.modules.prediction.adapters import (
    AdapterCapability, AdapterManifest, ModelOption, ModelVerificationEvidence,
    PredictionAdapterProtocol, PredictionLimits, StructureCount, ToolCapability,
    VerifiedModelHandle, issue_verified_model_handle,
)
from chrom_platform.modules.prediction.schemas import (
    Applicability, CcPredictionCreate, CcPredictionResult, CcResultRow, Metric, Warning,
)

from .runtime import (
    BUNDLE_NAMES,
    CC_MODEL_SHA256,
    CC_SCOPE_ID,
    CcRuntime,
    CcAssetUnavailable,
    CcModelRegistry,
    derive_time_seconds,
    _spec,
)
from .condition_catalog import CC_CONDITION_CATALOG, CC_NUMERIC_INPUT_RANGES, CcConditionCatalog
from .validation import CC_INPUT_SCHEMA_VERSION, validate_cc_request
from .preprocessing_profiles import (
    CC_4G_CANONICAL_PROFILE_ID,
    CC_4G_CANONICAL_PROFILE_SHA256,
    resolve_cc_preprocessing_profile,
)
from .golden_4g import CC_GOLDEN_4G, verify_golden_digest
from .canonical_golden_4g import CC_CANONICAL_GOLDEN_4G, verify_cc_canonical_golden

CC_OUTPUT_SCHEMA_VERSION = "cc-qgeognn-1"
CC_MODEL_VERSION = "cc-qgeognn-1"
CC_MODEL_HASH_BY_ID = {BUNDLE_NAMES[spec]: CC_MODEL_SHA256[spec] for spec in BUNDLE_NAMES}
CC_4G_MODEL_ID = BUNDLE_NAMES["4g"]


def _tool_schemas(catalog: CcConditionCatalog) -> tuple[dict[str, Any], dict[str, Any]]:
    ranges = catalog.numeric_input_ranges
    input_schema = {
        "title": "CcPredictionCreate",
        "type": "object",
        "properties": {
            "structure_refs": {"type": "array", "minItems": 2, "maxItems": 2},
            "input": {
                "type": "object",
                "properties": {
                    "eluent_system_id": {"type": "string", "enum": sorted(catalog.eluent_systems)},
                    "loading_solvent_id": {"type": "string", "enum": sorted(catalog.loading_solvents)},
                    **{
                        name: {
                            "type": "number", "minimum": bounds["minimum"],
                            "maximum": bounds["maximum"], "unit": bounds["unit"],
                        }
                        for name, bounds in ranges.items()
                    },
                    "column_spec_id": {"type": "string", "enum": list(catalog.column_spec_ids)},
                },
            },
        },
    }
    output_schema = {
        "title": "CcPredictionResult",
        "type": "object",
        "rows": {"volume_ml": "model q50; unchanged", "time_seconds": "derived"},
        "metrics": {"windows": ["V1", "V2"], "quantiles": ["q10", "q50", "q90"]},
    }
    return input_schema, output_schema


def _golden_request(case_pair: Sequence[Mapping[str, Any]]) -> CcPredictionCreate:
    shared = dict(case_pair[0]["request"])
    if any(dict(case["request"]) != shared or dict(case["author_input"]) != dict(case_pair[0]["author_input"]) for case in case_pair):
        raise CcAssetUnavailable("MODEL_GOLDEN_CONDITION_MISMATCH")
    return CcPredictionCreate.model_validate({
        "project_id": "cc-verification",
        "schema_version": CC_INPUT_SCHEMA_VERSION,
        "model_selection": {
            "mode": "exact", "model_id": CC_4G_MODEL_ID, "model_version": CC_MODEL_VERSION,
            "preprocessing_profile_id": CC_4G_CANONICAL_PROFILE_ID,
            "preprocessing_profile_sha256": CC_4G_CANONICAL_PROFILE_SHA256,
        },
        "structure_refs": [f"golden-{index}" for index in range(len(case_pair))],
        "chromatography_type": "cc",
        "tool": "single",
        "input": shared,
    })


def _verify_cc_evidence(
    *, manifest: AdapterManifest, runtime: CcRuntime, catalog: CcConditionCatalog,
    golden: Mapping[str, Any] = CC_GOLDEN_4G,
) -> ModelVerificationEvidence:
    if not verify_golden_digest(golden):
        raise CcAssetUnavailable("MODEL_GOLDEN_HASH_MISMATCH")
    if not verify_cc_canonical_golden():
        raise CcAssetUnavailable("MODEL_CANONICAL_GOLDEN_HASH_MISMATCH")
    if (
        catalog.document.get("model", {}).get("artifact_sha256") != CC_MODEL_SHA256["4g"]
        or
        golden.get("namespace") != CC_CONDITION_CATALOG["namespace"]
        or golden.get("catalog_revision") != CC_CONDITION_CATALOG["revision"]
        or golden.get("model_id") != CC_4G_MODEL_ID
        or golden.get("model_version") != CC_MODEL_VERSION
        or golden.get("artifact_sha256") != CC_MODEL_SHA256["4g"]
        or golden.get("input_schema_version") != CC_INPUT_SCHEMA_VERSION
        or golden.get("output_schema_version") != CC_OUTPUT_SCHEMA_VERSION
        or manifest.model_id != CC_4G_MODEL_ID
        or manifest.model_version != CC_MODEL_VERSION
        or manifest.artifact_sha256 != CC_MODEL_SHA256["4g"]
        or manifest.preprocessing_profile_id != CC_4G_CANONICAL_PROFILE_ID
        or manifest.preprocessing_profile_sha256 != CC_4G_CANONICAL_PROFILE_SHA256
        or resolve_cc_preprocessing_profile(
            manifest.preprocessing_profile_id, manifest.preprocessing_profile_sha256
        ) is None
        or golden.get("cases") is None
        or len(golden["cases"]) != 2
    ):
        raise CcAssetUnavailable("MODEL_GOLDEN_BINDING_INVALID")
    bundle = runtime.registry.bundle(CC_4G_MODEL_ID)
    if bundle.artifact_sha256 != golden["artifact_sha256"]:
        raise CcAssetUnavailable("MODEL_ARTIFACT_BINDING_INVALID")
    cases = golden["cases"]
    request = _golden_request(cases)
    smiles = [case["smiles"] for case in cases]
    actual = runtime.predict(request, smiles)
    repeat = runtime.predict(request, smiles)
    if actual != repeat:
        raise CcAssetUnavailable("MODEL_DETERMINISM_CHECK_FAILED")
    if (
        actual.get("artifact_sha256") != bundle.artifact_sha256
        or actual.get("model", {}).get("name") != CC_4G_MODEL_ID
        or actual.get("model", {}).get("column_spec") != "Silica-CS 4g"
    ):
        raise CcAssetUnavailable("MODEL_OUTPUT_BINDING_INVALID")
    predicted = actual.get("predicted_elution_windows")
    expected = [
        {"smiles": case["smiles"], "predicted_elution_windows": case["expected"]}
        for case in cases
    ]
    if predicted != expected:
        raise CcAssetUnavailable("MODEL_GOLDEN_MISMATCH")
    canonical = CC_CANONICAL_GOLDEN_4G
    if (
        canonical["profile_id"] != manifest.preprocessing_profile_id
        or canonical["profile_sha256"] != manifest.preprocessing_profile_sha256
        or canonical["model_id"] != manifest.model_id
        or canonical["model_version"] != manifest.model_version
        or canonical["artifact_sha256"] != manifest.artifact_sha256
        or canonical["request"] != CC_GOLDEN_4G["cases"][0]["request"]
        or canonical["author_input"] != CC_GOLDEN_4G["cases"][0]["author_input"]
        or len(canonical["cases"]) != 2
    ):
        raise CcAssetUnavailable("MODEL_CANONICAL_GOLDEN_BINDING_INVALID")
    canonical_request = CcPredictionCreate.model_validate({
        "project_id": "cc-verification",
        "schema_version": CC_INPUT_SCHEMA_VERSION,
        "model_selection": {
            "mode": "exact", "model_id": CC_4G_MODEL_ID, "model_version": CC_MODEL_VERSION,
            "preprocessing_profile_id": CC_4G_CANONICAL_PROFILE_ID,
            "preprocessing_profile_sha256": CC_4G_CANONICAL_PROFILE_SHA256,
        },
        "structure_refs": ["canonical-golden-0", "canonical-golden-1"],
        "chromatography_type": "cc", "tool": "single", "input": canonical["request"],
    })
    canonical_actual = runtime.predict(canonical_request, [case["smiles"] for case in canonical["cases"]])
    if canonical_actual.get("predicted_elution_windows") != [
        {"smiles": case["smiles"], "predicted_elution_windows": case["expected"]}
        for case in canonical["cases"]
    ]:
        raise CcAssetUnavailable("MODEL_CANONICAL_GOLDEN_MISMATCH")
    for case_windows in predicted:
        if len(case_windows["predicted_elution_windows"]) != 2:
            raise CcAssetUnavailable("MODEL_OUTPUT_SHAPE_INVALID")
        for window in case_windows["predicted_elution_windows"]:
            volume = window["volume_ml"]
            times = window["time_seconds"]
            values = [float(volume[key]) for key in ("q10", "q50", "q90")]
            if any(not math.isfinite(value) or value <= 0 for value in values) or values != sorted(values):
                raise CcAssetUnavailable("MODEL_OUTPUT_INVALID")
            for key, value in zip(("q10", "q50", "q90"), values):
                expected_time = derive_time_seconds(volume_ml=value, flow_rate_ml_min=float(cases[0]["request"]["flow_rate_ml_min"]))
                if not math.isfinite(float(times[key])) or float(times[key]) != expected_time:
                    raise CcAssetUnavailable("MODEL_TIME_DERIVATION_INVALID")
    input_schema, output_schema = _tool_schemas(catalog)
    return ModelVerificationEvidence(
        adapter_id="cc", model_id=manifest.model_id, model_version=manifest.model_version,
        business_scope_id=manifest.business_scope_id,
        input_schema_version=manifest.input_schema_version, output_schema_version=manifest.output_schema_version,
        artifact_sha256=manifest.artifact_sha256 or "",
        preprocessing_profile_id=manifest.preprocessing_profile_id,
        preprocessing_profile_sha256=manifest.preprocessing_profile_sha256,
        manifest_verified=True, schema_verified=True, artifact_sha_verified=True,
        environment_verified=True, golden_sample_verified=True, anomaly_checks_verified=True,
        schema_by_tool={"single": {"input_schema": input_schema, "output_schema": output_schema}},
    )


def _health(reason: str) -> CapabilityHealth:
    return CapabilityHealth(status="UNAVAILABLE", reason=reason, checked_at=datetime.now(timezone.utc))


class CcPredictionAdapter(PredictionAdapterProtocol):
    type_id = "cc"
    request_types = (CcPredictionCreate,)
    manifest: AdapterManifest | None
    verified_handle: VerifiedModelHandle | None = None

    def __init__(
        self,
        runtime: CcRuntime | None = None,
        *,
        structure_resolver: Callable[[str], str] | None = None,
        model_id: str = "qgeognn_silica_4g",
        model_version: str = CC_MODEL_VERSION,
        artifact_sha256: str | None = None,
        manifest: AdapterManifest | None = None,
        verified_handle: VerifiedModelHandle | None = None,
        condition_catalog: CcConditionCatalog | None = None,
    ) -> None:
        self.runtime = runtime
        self.structure_resolver = structure_resolver
        runtime_catalog = getattr(runtime, "condition_catalog", None)
        self.condition_catalog = condition_catalog or runtime_catalog or CcConditionCatalog()
        self.manifest = manifest
        self.verified_handle = verified_handle
        if model_id not in CC_MODEL_HASH_BY_ID:
            raise ValueError("CC model_id is not an audited bundle")
        if artifact_sha256 is not None and artifact_sha256 != CC_MODEL_HASH_BY_ID[model_id]:
            raise ValueError("CC artifact hash is not the audited bundle hash")
        if model_version != CC_MODEL_VERSION:
            raise ValueError("CC model_version is not the audited schema version")
        if verified_handle is not None and manifest is None:
            raise ValueError("CC verified handle requires its central adapter manifest")
        if manifest is not None and (
            manifest.adapter_id != "cc"
            or manifest.model_id != model_id
            or manifest.model_version != model_version
            or manifest.business_scope_id != CC_SCOPE_ID
            or manifest.manifest_version != CC_OUTPUT_SCHEMA_VERSION
            or manifest.input_schema_version != CC_INPUT_SCHEMA_VERSION
            or manifest.output_schema_version != CC_OUTPUT_SCHEMA_VERSION
            or manifest.artifact_sha256 != CC_MODEL_HASH_BY_ID[model_id]
            or manifest.preprocessing_profile_id != CC_4G_CANONICAL_PROFILE_ID
            or manifest.preprocessing_profile_sha256 != CC_4G_CANONICAL_PROFILE_SHA256
            or manifest.data_origin != "production"
        ):
            raise ValueError("CC adapter manifest does not bind the audited artifact")
        if verified_handle is not None:
            if model_id != CC_4G_MODEL_ID:
                raise ValueError("CC verified handle is restricted to the 4 g launch model")
            if any(
                getattr(verified_handle, field) != getattr(manifest, field)
                for field in (
                    "adapter_id", "model_id", "model_version", "input_schema_version",
                    "business_scope_id", "output_schema_version", "artifact_sha256",
                    "preprocessing_profile_id", "preprocessing_profile_sha256",
                )
            ) or verified_handle.business_scope_id != CC_SCOPE_ID or verified_handle.schema_for("single") is None:
                raise ValueError("CC verified handle does not bind the audited tool")
            binder = getattr(runtime, "bind_verified_handle", None)
            if callable(binder):
                binder(verified_handle)
        health = _health("MODEL_VERIFICATION_REQUIRED")
        limits = PredictionLimits(max_rows=4, max_candidates=1, max_evaluations=2)
        input_schema, output_schema = _tool_schemas(self.condition_catalog)
        tool = ToolCapability(
            id="single", schema_version=CC_INPUT_SCHEMA_VERSION,
            input_schema=input_schema,
            output_schema=output_schema,
            structure_count=StructureCount(min=2, max=2, semantics="pair"),
            model_constraints={
                "outputs": ["V1", "V2"], "quantiles": ["q10", "q50", "q90"],
                "catalog_namespace": self.condition_catalog.document["namespace"],
                "catalog_revision": self.condition_catalog.document["revision"],
                "eluent_system_ids": sorted(self.condition_catalog.eluent_systems),
                "loading_solvent_ids": sorted(self.condition_catalog.loading_solvents),
                "column_spec_ids": list(self.condition_catalog.column_spec_ids),
                "numeric_input_ranges": self.condition_catalog.numeric_input_ranges,
                "numeric_input_range_semantics": (
                    "Observed marginal bounds from 462 matching source rows; reject extrapolation only, "
                    "not verified joint support or model generalization."
                ),
            },
            units={"sample_mass_mg": "mg", "loading_solvent_volume_ul": "ul", "flow_rate_ml_min": "ml/min", "volume_ml": "ml", "time_seconds": "s"},
            supported_visualizations=[], limits=limits, health=health,
        )
        self.capability = AdapterCapability(
            id="cc", display_name="Column chromatography", tools=[tool],
            models=[ModelOption(
                id=model_id, version=model_version, task_type="cc", scope=CC_SCOPE_ID,
                artifact_sha256=artifact_sha256,
                preprocessing_profile_id=CC_4G_CANONICAL_PROFILE_ID,
                preprocessing_profile_sha256=CC_4G_CANONICAL_PROFILE_SHA256,
                input_schema_version=CC_INPUT_SCHEMA_VERSION,
                output_schema_version=CC_OUTPUT_SCHEMA_VERSION, health=health,
            )],
            limits=limits, health=health,
        )

    def validate_request(self, request: Any) -> CcPredictionCreate:
        return validate_cc_request(request, self.condition_catalog)

    def _validate_selection_binding(self, request: CcPredictionCreate) -> None:
        assert self.manifest is not None
        assert self.verified_handle is not None
        selection = request.model_selection
        if selection.mode == "exact":
            if (
                selection.model_id != self.verified_handle.model_id
                or selection.model_version != self.verified_handle.model_version
                or selection.preprocessing_profile_id != self.verified_handle.preprocessing_profile_id
                or selection.preprocessing_profile_sha256 != self.verified_handle.preprocessing_profile_sha256
            ):
                raise CcAssetUnavailable("MODEL_SELECTION_NOT_VERIFIED")
        else:
            raise CcAssetUnavailable("MODEL_SELECTION_NOT_VERIFIED")
        expected_spec = _spec(self.verified_handle.model_id)
        if _spec(request.input.column_spec_id) != expected_spec:
            raise CcAssetUnavailable("MODEL_COLUMN_SPEC_MISMATCH")

    def _resolve_structures(self, refs: Sequence[str]) -> list[str]:
        if self.structure_resolver is None:
            raise CcAssetUnavailable("STRUCTURE_RESOLVER_REQUIRED")
        normalized: list[str] = []
        for ref in refs:
            try:
                value = self.structure_resolver(ref)
            except (KeyError, LookupError, ValueError) as exc:
                raise CcAssetUnavailable("STRUCTURE_UNAVAILABLE") from exc
            if not isinstance(value, str) or not value.strip():
                raise CcAssetUnavailable("STRUCTURE_UNAVAILABLE")
            normalized.append(value.strip())
        if len(normalized) != 2:
            raise CcAssetUnavailable("STRUCTURE_PAIR_INVALID")
        return normalized

    @staticmethod
    def _validate_runtime_output(raw: Any, normalized_smiles: Sequence[str]) -> list[dict[str, Any]]:
        if not isinstance(raw, Mapping):
            raise CcAssetUnavailable("MODEL_OUTPUT_INVALID")
        windows = raw.get("predicted_elution_windows")
        if not isinstance(windows, list) or len(windows) != 2:
            raise CcAssetUnavailable("MODEL_OUTPUT_SHAPE_INVALID")
        for index, structure in enumerate(windows):
            if not isinstance(structure, Mapping) or structure.get("smiles") != normalized_smiles[index]:
                raise CcAssetUnavailable("MODEL_OUTPUT_ORDER_INVALID")
            entries = structure.get("predicted_elution_windows")
            if not isinstance(entries, list) or len(entries) != 2:
                raise CcAssetUnavailable("MODEL_OUTPUT_SHAPE_INVALID")
            if [entry.get("name") for entry in entries if isinstance(entry, Mapping)] != ["V1", "V2"]:
                raise CcAssetUnavailable("MODEL_OUTPUT_ORDER_INVALID")
            for entry in entries:
                if not isinstance(entry, Mapping):
                    raise CcAssetUnavailable("MODEL_OUTPUT_SHAPE_INVALID")
                volumes = entry.get("volume_ml")
                if not isinstance(volumes, Mapping) or set(volumes) != {"q10", "q50", "q90"}:
                    raise CcAssetUnavailable("MODEL_OUTPUT_QUANTILES_INVALID")
                ordered = []
                for quantile in ("q10", "q50", "q90"):
                    try:
                        value = float(volumes[quantile])
                    except (TypeError, ValueError) as exc:
                        raise CcAssetUnavailable("MODEL_OUTPUT_QUANTILES_INVALID") from exc
                    if not math.isfinite(value) or value <= 0:
                        raise CcAssetUnavailable("MODEL_OUTPUT_QUANTILES_INVALID")
                    ordered.append(value)
                if ordered != sorted(ordered):
                    raise CcAssetUnavailable("MODEL_OUTPUT_QUANTILES_INVALID")
        return windows

    def predict(self, request: CcPredictionCreate) -> CcPredictionResult:
        validated = self.validate_request(request)
        if self.runtime is None:
            raise CcAssetUnavailable("MODEL_RUNTIME_UNAVAILABLE")
        if self.verified_handle is None:
            raise CcAssetUnavailable("MODEL_VERIFICATION_REQUIRED")
        if self.manifest is None:
            raise CcAssetUnavailable("MODEL_MANIFEST_REQUIRED")
        self._validate_selection_binding(validated)
        normalized_smiles = self._resolve_structures(validated.structure_refs)
        raw = self.runtime.predict(validated, normalized_smiles)
        if not isinstance(raw, Mapping) or raw.get("artifact_sha256") != self.verified_handle.artifact_sha256:
            raise CcAssetUnavailable("MODEL_ARTIFACT_BINDING_INVALID")
        model_info = raw.get("model")
        if not isinstance(model_info, Mapping) or _spec(str(model_info.get("column_spec", ""))) != _spec(self.verified_handle.model_id):
            raise CcAssetUnavailable("MODEL_COLUMN_SPEC_MISMATCH")
        windows = self._validate_runtime_output(raw, normalized_smiles)
        metrics: list[Metric] = []
        rows: list[CcResultRow] = []
        flow = float(validated.input.flow_rate_ml_min)
        for index, structure in enumerate(windows, 1):
            for item in structure["predicted_elution_windows"]:
                for name in ("q10", "q50", "q90"):
                    quantile = float(item["volume_ml"][name])
                    metrics.append(Metric(key=f"structure_{index}_{item['name']}_volume_{name}", label=f"Structure {index} {item['name']} volume {name}", value=quantile, unit="ml", origin="model"))
                    metrics.append(Metric(key=f"structure_{index}_{item['name']}_time_{name}", label=f"Structure {index} {item['name']} time {name}", value=derive_time_seconds(volume_ml=quantile, flow_rate_ml_min=flow), unit="s", origin="derived"))
                rows.append(CcResultRow(volume_ml=float(item["volume_ml"]["q50"]), flow_rate_ml_min=flow))
        return CcPredictionResult(
            task_type="cc", schema_version=self.verified_handle.output_schema_version,
            rows=rows, metrics=metrics, visualizations=[],
            applicability=Applicability(
                supported_scope=f"{raw['model']['column_spec']} QGeoGNN; PE/EA and loading rules bound to cc-qgeognn-1",
                warnings=[
                    Warning(code="EXPERIMENTAL_CONFIRMATION_REQUIRED", message="结果仅用于实验规划，须以实验确认。"),
                    Warning(code="NO_INDEPENDENT_GENERALIZATION_EVIDENCE", message="当前没有独立标签集证据，结果不代表独立科学泛化验收。"),
                    Warning(
                        code="TRAINING_PREPROCESSING_EQUIVALENCE_UNVERIFIED",
                        message="当前 canonical 预处理仅验证软件集成基线，训练端预处理等价性尚未验证。",
                    ),
                ],
            ),
        )


def build_cc_adapter(
    *,
    model_root: str,
    structure_resolver: Callable[[str], str] | None = None,
    model_id: str = "qgeognn_silica_4g",
    model_version: str = CC_MODEL_VERSION,
) -> CcPredictionAdapter:
    if model_id != CC_4G_MODEL_ID or model_version != CC_MODEL_VERSION:
        raise ValueError("CC launch binding is fixed to the verified 4 g model")
    catalog = CcConditionCatalog()
    runtime = None
    assets_available = False
    artifact_sha256 = CC_MODEL_SHA256["4g"]
    try:
        registry = CcModelRegistry(model_root)
        bundle = registry.bundle(CC_4G_MODEL_ID)
        runtime = CcRuntime(registry=registry, condition_catalog=catalog)
        artifact_sha256 = bundle.artifact_sha256
        assets_available = True
    except Exception:
        registry = None
    manifest = AdapterManifest(
        adapter_id="cc", model_id=CC_4G_MODEL_ID, model_version=model_version,
        business_scope_id=CC_SCOPE_ID,
        manifest_version=CC_OUTPUT_SCHEMA_VERSION, input_schema_version=CC_INPUT_SCHEMA_VERSION,
        output_schema_version=CC_OUTPUT_SCHEMA_VERSION, artifact_sha256=artifact_sha256,
        preprocessing_profile_id=CC_4G_CANONICAL_PROFILE_ID,
        preprocessing_profile_sha256=CC_4G_CANONICAL_PROFILE_SHA256,
        schema_available=True, assets_available=assets_available, data_origin="production",
    )
    handle = None
    if runtime is not None and registry is not None:
        try:
            evidence = _verify_cc_evidence(manifest=manifest, runtime=runtime, catalog=catalog)
            handle = issue_verified_model_handle(manifest, evidence)
        except Exception:
            handle = None
    return CcPredictionAdapter(
        runtime,
        structure_resolver=structure_resolver,
        model_id=CC_4G_MODEL_ID,
        model_version=model_version,
        artifact_sha256=artifact_sha256,
        manifest=manifest,
        verified_handle=handle,
        condition_catalog=catalog,
    )


__all__ = ["CcPredictionAdapter", "build_cc_adapter"]
