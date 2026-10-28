"""Pure, exact TLC model/template compatibility for batch capability discovery."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import importlib
from collections.abc import Mapping

from chrom_platform.core.dto import CapabilityHealth

from .batch import (
    BatchTemplateDefinition,
    NonTlcBatchCompatibleModel,
    NonTlcBatchModelConstraints,
    TlcBatchCompatibleModel,
    TlcBatchModelConstraints,
)


_TLC_TEMPLATE_ROWS = {
    "tlc-batch-2": (
        "TlcBatchRowV2",
        ("smiles", "solvent_1", "fraction_1", "solvent_2", "fraction_2"),
    ),
}


def build_tlc_batch_model_constraints(
    capability_registry: object | None,
    templates: Sequence[BatchTemplateDefinition],
) -> TlcBatchModelConstraints:
    """Expose only exact TLC model bindings proven compatible with known row projections.

    The returned model health is inherited from the already-built capability
    registry. This function never loads a model, performs inference, or upgrades
    batch-template health.
    """

    template_versions = [item.version for item in templates]
    compatible_versions = [
        item.version
        for item in templates
        if (
            item.chromatography_type == "tlc"
            and item.tool == "batch"
            and item.health.status != "UNAVAILABLE"
            and item.version in _TLC_TEMPLATE_ROWS
            and item.row_schema == _TLC_TEMPLATE_ROWS[item.version][0]
            and tuple(item.fields) == _TLC_TEMPLATE_ROWS[item.version][1]
        )
    ]
    compatible_models: list[TlcBatchCompatibleModel] = []
    if not compatible_versions or not callable(getattr(capability_registry, "capability", None)):
        return TlcBatchModelConstraints(
            template_versions=template_versions,
            compatible_models=compatible_models,
        )

    if getattr(capability_registry, "metadata_only", False):
        return _metadata_tlc_constraints(capability_registry, compatible_versions, template_versions)

    try:
        capability = capability_registry.capability("tlc")
        adapter = capability_registry.adapter("tlc")
    except (KeyError, TypeError, ValueError):
        return TlcBatchModelConstraints(
            template_versions=template_versions,
            compatible_models=compatible_models,
        )
    if getattr(adapter, "type_id", None) != "tlc" or capability.health.status != "READY":
        return TlcBatchModelConstraints(
            template_versions=template_versions,
            compatible_models=compatible_models,
        )

    manifest = adapter.manifest
    handle = adapter.verified_handle
    if manifest is None or handle is None:
        return TlcBatchModelConstraints(
            template_versions=template_versions,
            compatible_models=compatible_models,
        )
    if (
        manifest.adapter_id != "tlc"
        or manifest.data_origin != "production"
        or not manifest.schema_available
        or not manifest.assets_available
        or handle.adapter_id != "tlc"
        or handle.model_id != manifest.model_id
        or handle.model_version != manifest.model_version
        or handle.business_scope_id != manifest.business_scope_id
        or handle.input_schema_version != manifest.input_schema_version
        or handle.output_schema_version != manifest.output_schema_version
        or handle.artifact_sha256 != manifest.artifact_sha256
        or handle.preprocessing_profile_id != manifest.preprocessing_profile_id
        or handle.preprocessing_profile_sha256 != manifest.preprocessing_profile_sha256
    ):
        return TlcBatchModelConstraints(
            template_versions=template_versions,
            compatible_models=compatible_models,
        )

    # Current TLC execution has no model-specific preprocessing profile. Do not
    # infer that an arbitrary future profile applies to these row projections.
    if handle.preprocessing_profile_id is not None or handle.preprocessing_profile_sha256 is not None:
        return TlcBatchModelConstraints(
            template_versions=template_versions,
            compatible_models=compatible_models,
        )

    schema = handle.schema_for("single")
    expected_schema = {
        "input_schema": _schema_type("TlcPredictionCreate").model_json_schema(),
        "output_schema": _schema_type("TlcPredictionResult").model_json_schema(),
    }
    if schema != expected_schema:
        return TlcBatchModelConstraints(
            template_versions=template_versions,
            compatible_models=compatible_models,
        )

    single_tool = next((item for item in capability.tools if item.id == "single"), None)
    if (
        single_tool is None
        or single_tool.health.status != "READY"
        or single_tool.schema_version != handle.input_schema_version
        or single_tool.input_schema != schema["input_schema"]
        or single_tool.output_schema != schema["output_schema"]
    ):
        return TlcBatchModelConstraints(
            template_versions=template_versions,
            compatible_models=compatible_models,
        )

    matching_models = [
        model
        for model in capability.models
        if (
            model.health.status == "READY"
            and model.id == handle.model_id
            and model.version == handle.model_version
            and model.scope == handle.business_scope_id
            and model.input_schema_version == handle.input_schema_version
            and model.output_schema_version == handle.output_schema_version
            and model.artifact_sha256 == handle.artifact_sha256
            and model.preprocessing_profile_id == handle.preprocessing_profile_id
            and model.preprocessing_profile_sha256 == handle.preprocessing_profile_sha256
        )
    ]
    if len(matching_models) == 1:
        model = matching_models[0]
        compatible_models.append(
            TlcBatchCompatibleModel(
                model_id=model.id,
                model_version=model.version,
                input_schema_version=model.input_schema_version,
                template_versions=compatible_versions,
                health=model.health,
            )
        )
    return TlcBatchModelConstraints(
        template_versions=template_versions,
        compatible_models=compatible_models,
    )


def project_tlc_batch_template_health(
    templates: Sequence[BatchTemplateDefinition],
    constraints: TlcBatchModelConstraints,
    capability_registry: object | None,
) -> tuple[list[BatchTemplateDefinition], CapabilityHealth]:
    """Project model proof to only the row-template versions it verifies.

    This is model/template compatibility health only; it does not report
    whether a Worker process is running or currently accepting work.
    """

    capability = None
    if callable(getattr(capability_registry, "capability", None)):
        try:
            capability = capability_registry.capability("tlc")
        except (KeyError, TypeError, ValueError):
            capability = None

    projected: list[BatchTemplateDefinition] = []
    for template in templates:
        if template.version == "tlc-batch-1":
            projected.append(template.model_copy(update={"health": CapabilityHealth(
                status="UNAVAILABLE", reason="LEGACY_TEMPLATE_READ_ONLY", checked_at=datetime.now(timezone.utc),
            )}))
            continue
        if template.health.status == "UNAVAILABLE":
            projected.append(template)
            continue
        matching = [
            binding
            for binding in constraints.compatible_models
            if template.version in binding.template_versions
        ]
        if matching:
            # The constraints DTO enforces unique exact model/version/schema
            # tuples; separate verified models may both support this version.
            if all(binding.health.status == "READY" for binding in matching):
                health = CapabilityHealth(
                    status="READY",
                    reason=None,
                    checked_at=max(binding.health.checked_at for binding in matching),
                )
            else:
                health = CapabilityHealth(
                    status="DEGRADED",
                    reason="MULTIPLE_OR_UNREADY_MODEL_BINDINGS",
                    checked_at=datetime.now(timezone.utc),
                )
            projected.append(template.model_copy(update={"health": health}))
            continue

        models = [] if capability is None else capability.models
        if any(model.health.status == "READY" for model in models):
            health = CapabilityHealth(
                status="UNAVAILABLE",
                reason="TLC_BATCH_TEMPLATE_INCOMPATIBLE",
                checked_at=datetime.now(timezone.utc),
            )
        elif any(
            model.health.status == "UNAVAILABLE"
            and model.health.reason not in {"MODEL_VERIFICATION_REQUIRED", "MODEL_CONTRACT_MISSING"}
            for model in models
        ):
            health = CapabilityHealth(
                status="UNAVAILABLE",
                reason="TLC_BATCH_MODEL_UNAVAILABLE",
                checked_at=datetime.now(timezone.utc),
            )
        else:
            health = CapabilityHealth(
                status="UNKNOWN",
                reason="MODEL_VERIFICATION_REQUIRED",
                checked_at=template.health.checked_at,
            )
        projected.append(template.model_copy(update={"health": health}))

    statuses = [item.health.status for item in projected]
    if statuses and all(item == "READY" for item in statuses):
        ready_times = [item.health.checked_at for item in projected if item.health.checked_at is not None]
        overall = CapabilityHealth(status="READY", reason=None, checked_at=max(ready_times))
    elif any(item == "READY" for item in statuses):
        overall = CapabilityHealth(
            status="DEGRADED",
            reason="PARTIAL_TEMPLATE_COMPATIBILITY",
            checked_at=datetime.now(timezone.utc),
        )
    elif any(item == "UNAVAILABLE" for item in statuses):
        overall = CapabilityHealth(
            status="UNAVAILABLE",
            reason="TLC_BATCH_MODEL_TEMPLATE_UNAVAILABLE",
            checked_at=datetime.now(timezone.utc),
        )
    else:
        checked = [item.health.checked_at for item in projected if item.health.checked_at is not None]
        overall = CapabilityHealth(
            status="UNKNOWN",
            reason="MODEL_VERIFICATION_REQUIRED",
            checked_at=max(checked) if checked else datetime.now(timezone.utc),
        )
    return projected, overall


_NON_TLC_TEMPLATE_ROWS = {
    "cc": {"cc-batch-2": ("CcBatchRowV2", ("structure_ref_a", "structure_ref_b", "eluent_system_id", "loading_solvent_id", "sample_mass_mg", "loading_solvent_volume_ul", "flow_rate_ml_min", "column_spec_id"))},
    "hplc": {"hplc-batch-2": ("HplcBatchRowV2", ("smiles_a", "smiles_b", "isopropanol_fraction", "flow_rate_ml_min", "column_ids"))},
    "sfc": {"sfc-batch-2": ("SfcBatchRowV2", ("structure_ref_a", "structure_ref_b", "modifier_total_fraction", "modifier_components", "candidate_scope_id", "top_k", "tool", "column_id"))},
    "gc": {"gc-batch-1": ("GcBatchRow", ("structure_ref", "temperature_program"))},
}


def _schema_type(name: str):
    return getattr(importlib.import_module("chrom_platform.modules.prediction.schemas"), name)


def _metadata_tlc_constraints(capability_registry, compatible_versions, template_versions):
    compatible_models = []
    try:
        capability = capability_registry.capability("tlc")
    except (KeyError, TypeError, ValueError):
        return TlcBatchModelConstraints(template_versions=template_versions, compatible_models=[])
    adapter = capability_registry.adapter("tlc")
    if capability.health.status != "READY":
        return TlcBatchModelConstraints(template_versions=template_versions, compatible_models=[])
    model_type = _schema_type("TlcPredictionCreate")
    result_type = _schema_type("TlcPredictionResult")
    expected_schema = {"input_schema": model_type.model_json_schema(), "output_schema": result_type.model_json_schema()}
    for model in capability.models:
        if model.health.status != "READY":
            continue
        binding = capability_registry.binding_for("tlc", model.id, model.version)
        if binding is None:
            continue
        manifest = binding.manifest
        attestation = binding.attestation
        schema = binding.tool_schemas.get("single")
        tool = next((item for item in capability.tools if item.id == "single"), None)
        fields = {
            "adapter_id": "tlc", "model_id": model.id, "model_version": model.version,
            "business_scope_id": model.scope, "input_schema_version": model.input_schema_version,
            "output_schema_version": model.output_schema_version, "artifact_sha256": model.artifact_sha256,
            "preprocessing_profile_id": model.preprocessing_profile_id,
            "preprocessing_profile_sha256": model.preprocessing_profile_sha256,
        }
        if (
            manifest.data_origin != "production" or not manifest.schema_available or not manifest.assets_available
            or any(attestation.get(key) != value for key, value in fields.items())
            or model.preprocessing_profile_id is not None or model.preprocessing_profile_sha256 is not None
            or schema is None or {key: schema.get(key) for key in ("input_schema", "output_schema")} != expected_schema
            or tool is None or tool.health.status != "READY"
            or tool.input_schema != schema["input_schema"] or tool.output_schema != schema["output_schema"]
            or tool.schema_version != model.input_schema_version
        ):
            continue
        compatible_models.append(TlcBatchCompatibleModel(
            model_id=model.id, model_version=model.version,
            input_schema_version=model.input_schema_version,
            template_versions=compatible_versions, health=model.health,
        ))
    return TlcBatchModelConstraints(template_versions=template_versions, compatible_models=compatible_models)


def _metadata_non_tlc_constraints(type_id, capability_registry, compatible_versions, template_versions):
    compatible_models = []
    try:
        capability = capability_registry.capability(type_id)
    except (KeyError, TypeError, ValueError):
        return NonTlcBatchModelConstraints(template_versions=template_versions, compatible_models=[])
    adapter = capability_registry.adapter(type_id)
    if type_id == "hplc":
        allowed_tool_ids = {"single"}
        expected_schemas = {"single": _schema_type("HplcPredictionCreate").model_json_schema()}
    elif type_id == "sfc":
        expected_schemas = {
            "single_column": _schema_type("SfcSingleColumnPredictionCreate").model_json_schema(),
            "full_column": _schema_type("SfcFullColumnPredictionCreate").model_json_schema(),
        }
        allowed_tool_ids = {"single_column", "full_column"}
    else:
        expected_schemas = {}
        allowed_tool_ids = {"single"}
    for model in capability.models:
        if model.health.status != "READY":
            continue
        binding = capability_registry.binding_for(type_id, model.id, model.version)
        if binding is None:
            continue
        attestation = binding.attestation
        manifest = binding.manifest
        if manifest.data_origin != "production" or not manifest.schema_available or not manifest.assets_available:
            continue
        fields = {
            "adapter_id": type_id, "model_id": model.id, "model_version": model.version,
            "business_scope_id": model.scope, "input_schema_version": model.input_schema_version,
            "output_schema_version": model.output_schema_version, "artifact_sha256": model.artifact_sha256,
            "preprocessing_profile_id": model.preprocessing_profile_id,
            "preprocessing_profile_sha256": model.preprocessing_profile_sha256,
        }
        if any(attestation.get(key) != value for key, value in fields.items()):
            continue
        if ((model.preprocessing_profile_id is None) != (model.preprocessing_profile_sha256 is None)):
            continue
        if type_id in {"cc", "hplc", "gc"} and not model.preprocessing_profile_id:
            continue
        if type_id in {"hplc", "gc"} and model.input_schema_version != "1.1":
            continue
        if type_id == "sfc":
            tool_ids = {binding.allowed_tool} & allowed_tool_ids
            if binding.candidate_catalog is None or binding.registry_entry is None or binding.sfc_domain is None:
                continue
        else:
            tool_ids = allowed_tool_ids
        verified_tools = []
        for tool_id in tool_ids:
            observed = binding.tool_schemas.get(tool_id)
            tool = next((item for item in capability.tools if item.id == tool_id), None)
            expected_schema = expected_schemas.get(tool_id)
            if type_id in {"cc", "gc"}:
                expected_schema = observed.get("input_schema") if isinstance(observed, Mapping) else None
            if (
                observed is not None and expected_schema is not None
                and observed.get("input_schema") == expected_schema
                and tool is not None and tool.health.status == "READY"
                and tool.schema_version == model.input_schema_version
                and tool.input_schema == observed.get("input_schema")
                and tool.output_schema == observed.get("output_schema")
            ):
                verified_tools.append(tool_id)
        if verified_tools:
            compatible_models.append(NonTlcBatchCompatibleModel(
                model_id=model.id, model_version=model.version,
                input_schema_version=model.input_schema_version,
                preprocessing_profile_id=model.preprocessing_profile_id,
                preprocessing_profile_sha256=model.preprocessing_profile_sha256,
                tools=verified_tools, template_versions=compatible_versions, health=model.health,
            ))
    return NonTlcBatchModelConstraints(template_versions=template_versions, compatible_models=compatible_models)


def build_non_tlc_batch_model_constraints(
    type_id: str,
    capability_registry: object | None,
    templates: Sequence[BatchTemplateDefinition],
) -> NonTlcBatchModelConstraints:
    """Return only exact, schema-checked non-TLC model/template bindings."""

    template_versions = [item.version for item in templates]
    expected_rows = _NON_TLC_TEMPLATE_ROWS.get(type_id, {})
    compatible_versions = [
        item.version for item in templates
        if item.chromatography_type == type_id and item.tool == "batch"
        and item.health.status != "UNAVAILABLE"
        and item.version in expected_rows
        and (item.row_schema, tuple(item.fields)) == expected_rows[item.version]
    ]
    empty = lambda: NonTlcBatchModelConstraints(template_versions=template_versions, compatible_models=[])
    if not compatible_versions or type_id not in {"cc", "hplc", "sfc", "gc"} or not callable(getattr(capability_registry, "capability", None)):
        return empty()
    if getattr(capability_registry, "metadata_only", False):
        return _metadata_non_tlc_constraints(type_id, capability_registry, compatible_versions, template_versions)
    if type_id == "hplc":
        expected_schemas = {"single": _schema_type("HplcPredictionCreate").model_json_schema()}
    elif type_id == "sfc":
        expected_schemas = {
            "single_column": _schema_type("SfcSingleColumnPredictionCreate").model_json_schema(),
            "full_column": _schema_type("SfcFullColumnPredictionCreate").model_json_schema(),
        }
    else:
        expected_schemas = {}
    compatible_models = []
    try:
        capability = capability_registry.capability(type_id)
        adapter = capability_registry.adapter(type_id)
    except (AttributeError, KeyError, TypeError, ValueError):
        return empty()
    for model in capability.models:
        if model.health.status != "READY":
            continue
        if type_id == "sfc":
            binding_for = getattr(adapter, "binding_for", None)
            binding = binding_for(model.id, model.version) if callable(binding_for) else None
            if binding is None:
                continue
            leaf_capability = getattr(binding, "capability", None)
            runtime = getattr(binding, "runtime", None)
            allowed_tool = getattr(runtime, "allowed_tool", None)
            tool_ids = [allowed_tool] if allowed_tool in {"single_column", "full_column"} else []
        else:
            binding = adapter
            leaf_capability = capability
            tool_ids = ["single"]
        if type_id == "cc":
            try:
                from chrom_platform.adapters.cc.adapter import _tool_schemas

                catalog = getattr(binding, "condition_catalog", None)
                if catalog is None:
                    continue
                input_schema, _output_schema = _tool_schemas(catalog)
                binding_schemas = {"single": input_schema}
            except (AttributeError, ImportError, TypeError, ValueError):
                continue
        elif type_id == "gc":
            try:
                from chrom_platform.adapters.gc.adapter import _gc_input_schema

                binding_schemas = {"single": _gc_input_schema()}
            except (AttributeError, ImportError, TypeError, ValueError):
                continue
        else:
            binding_schemas = expected_schemas
        try:
            manifest = getattr(binding, "manifest", None)
            handle = getattr(binding, "verified_handle", None)
        except AttributeError:
            continue
        if (
            manifest is None or handle is None
            or getattr(manifest, "adapter_id", None) != type_id
            or getattr(manifest, "data_origin", None) != "production"
            or not getattr(manifest, "schema_available", False)
            or not getattr(manifest, "assets_available", False)
            or getattr(handle, "adapter_id", None) != type_id
            or getattr(handle, "model_id", None) != getattr(manifest, "model_id", None)
            or getattr(handle, "model_version", None) != getattr(manifest, "model_version", None)
            or getattr(handle, "input_schema_version", None) != getattr(manifest, "input_schema_version", None)
            or getattr(handle, "output_schema_version", None) != getattr(manifest, "output_schema_version", None)
            or getattr(handle, "artifact_sha256", None) != getattr(manifest, "artifact_sha256", None)
            or getattr(handle, "preprocessing_profile_id", None) != getattr(manifest, "preprocessing_profile_id", None)
            or getattr(handle, "preprocessing_profile_sha256", None) != getattr(manifest, "preprocessing_profile_sha256", None)
            or ((handle.preprocessing_profile_id is None) != (handle.preprocessing_profile_sha256 is None))
            or (type_id in {"cc", "hplc", "gc"} and not handle.preprocessing_profile_id)
            or (type_id in {"hplc", "gc"} and handle.input_schema_version != "1.1")
            or model.id != handle.model_id or model.version != handle.model_version
            or model.scope != handle.business_scope_id
            or model.input_schema_version != handle.input_schema_version
            or model.output_schema_version != handle.output_schema_version
            or model.artifact_sha256 != handle.artifact_sha256
            or model.preprocessing_profile_id != handle.preprocessing_profile_id
            or model.preprocessing_profile_sha256 != handle.preprocessing_profile_sha256
        ):
            continue
        verified_tools = []
        for tool_id in tool_ids:
            expected_schema = binding_schemas.get(tool_id)
            observed = handle.schema_for(tool_id)
            tool = next((item for item in getattr(leaf_capability, "tools", ()) if item.id == tool_id), None)
            if (
                expected_schema is not None and observed is not None
                and observed.get("input_schema") == expected_schema
                and tool is not None and tool.health.status == "READY"
                and tool.schema_version == handle.input_schema_version
                and tool.input_schema == expected_schema
                and tool.output_schema == observed.get("output_schema")
            ):
                verified_tools.append(tool_id)
        if verified_tools:
            compatible_models.append(NonTlcBatchCompatibleModel(
                model_id=model.id,
                model_version=model.version,
                input_schema_version=model.input_schema_version,
                preprocessing_profile_id=model.preprocessing_profile_id,
                preprocessing_profile_sha256=model.preprocessing_profile_sha256,
                tools=verified_tools,
                template_versions=compatible_versions,
                health=model.health,
            ))
    return NonTlcBatchModelConstraints(template_versions=template_versions, compatible_models=compatible_models)


def project_non_tlc_batch_template_health(
    type_id: str,
    templates: Sequence[BatchTemplateDefinition],
    constraints: NonTlcBatchModelConstraints,
    capability_registry: object | None,
) -> tuple[list[BatchTemplateDefinition], CapabilityHealth]:
    capability = None
    if callable(getattr(capability_registry, "capability", None)):
        try:
            capability = capability_registry.capability(type_id)
        except (KeyError, TypeError, ValueError):
            pass
    projected = []
    for template in templates:
        if template.health.status == "UNAVAILABLE":
            projected.append(template)
            continue
        matches = [item for item in constraints.compatible_models if template.version in item.template_versions]
        if matches and all(item.health.status == "READY" for item in matches):
            times = [item.health.checked_at for item in matches if item.health.checked_at is not None]
            health = CapabilityHealth(status="READY", reason=None, checked_at=max(times) if times else template.health.checked_at)
            projected.append(template.model_copy(update={"health": health}))
        elif capability is not None and any(item.health.status == "READY" for item in capability.models):
            health = CapabilityHealth(status="UNAVAILABLE", reason="BATCH_MODEL_TEMPLATE_UNAVAILABLE", checked_at=datetime.now(timezone.utc))
            projected.append(template.model_copy(update={"health": health}))
        else:
            projected.append(template)
    statuses = [item.health.status for item in projected]
    if statuses and all(value == "READY" for value in statuses):
        times = [item.health.checked_at for item in projected if item.health.checked_at is not None]
        overall = CapabilityHealth(status="READY", reason=None, checked_at=max(times) if times else datetime.now(timezone.utc))
    elif any(value == "READY" for value in statuses):
        overall = CapabilityHealth(status="DEGRADED", reason="PARTIAL_TEMPLATE_COMPATIBILITY", checked_at=datetime.now(timezone.utc))
    elif any(value == "UNAVAILABLE" for value in statuses):
        overall = CapabilityHealth(status="UNAVAILABLE", reason="BATCH_MODEL_TEMPLATE_UNAVAILABLE", checked_at=datetime.now(timezone.utc))
    else:
        times = [item.health.checked_at for item in projected if item.health.checked_at is not None]
        overall = CapabilityHealth(status="UNKNOWN", reason="MODEL_VERIFICATION_REQUIRED", checked_at=max(times) if times else datetime.now(timezone.utc))
    return projected, overall
