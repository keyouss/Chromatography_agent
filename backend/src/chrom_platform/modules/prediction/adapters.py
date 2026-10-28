"""Explicit prediction adapter contracts and verified capability registry.

There is no module-path discovery and no default registration. A build must
inject a verified handle produced by its model/artifact validation port.
"""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
import json
from types import MappingProxyType
from typing import Literal, Mapping, NamedTuple, Protocol, TypeAlias, runtime_checkable
import weakref

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.core.errors import APIError, InputValidationError

from .schemas import (
    BusinessScopeId,
    Id,
    ModelSnapshot,
    PredictionStrictModel,
    SfcAllColumnDomain,
    SfcDomain,
    SfcSingleColumnDomain,
    SfcFullColumnExecutionV10,
    SfcSingleColumnExecutionV10,
)


ChromatographyType: TypeAlias = str
_SUPPORTED_TYPES = frozenset({"tlc", "cc", "hplc", "sfc", "gc", "chiral_lc"})


class PredictionLimits(PredictionStrictModel):
    max_rows: int = Field(ge=1)
    max_candidates: int = Field(ge=1)
    max_evaluations: int = Field(ge=1)


class StructureCount(PredictionStrictModel):
    min: int = Field(ge=1)
    max: int = Field(ge=1)
    semantics: Literal["single", "pair", "batch"]

    @model_validator(mode="after")
    def validate_bounds(self) -> "StructureCount":
        if self.max < self.min:
            raise ValueError("structure_count.max must be >= min")
        return self


class ToolCapability(PredictionStrictModel):
    id: Id
    schema_version: str = Field(min_length=1, max_length=64)
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    structure_count: StructureCount
    model_constraints: dict[str, object]
    units: dict[str, str]
    supported_visualizations: list[str]
    limits: PredictionLimits
    template_version: str | None = None
    health: CapabilityHealth


class ModelOption(PredictionStrictModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, populate_by_name=False, serialize_by_alias=True
    )

    id: Id
    version: str = Field(min_length=1, max_length=128)
    task_type: str = Field(min_length=1, max_length=96)
    business_scope_id: BusinessScopeId = Field(
        alias="scope",
        validation_alias=AliasChoices("business_scope_id", "scope"),
    )
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    preprocessing_profile_id: Id | None = Field(
        default=None,
        description="模型能力绑定的精确预处理版本标识；与 SHA-256 成对提供。",
    )
    preprocessing_profile_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="模型能力绑定的预处理版本 SHA-256；必须与标识字段成对提供。",
    )
    input_schema_version: str = Field(min_length=1, max_length=64)
    output_schema_version: str = Field(min_length=1, max_length=64)
    score_semantics: str | None = Field(default=None, max_length=96)
    threshold: str | None = Field(default=None, max_length=32)
    sfc_domain: SfcDomain | None = None
    health: CapabilityHealth

    @model_validator(mode="after")
    def validate_preprocessing_profile_pair(self) -> "ModelOption":
        if (self.preprocessing_profile_id is None) != (self.preprocessing_profile_sha256 is None):
            raise ValueError("preprocessing profile id and sha256 must be supplied together")
        if self.task_type != "sfc" and self.sfc_domain is not None:
            raise ValueError("sfc_domain is only valid for SFC models")
        return self

    @property
    def scope(self) -> str:
        """Compatibility view for the frozen capability DTO's ``scope`` key."""

        return self.business_scope_id

    def model_copy(self, *, update=None, deep=False):
        # Existing adapter assembly uses the API name ``scope`` when cloning
        # options; normalize that update to the canonical field first.
        if update and "scope" in update and "business_scope_id" not in update:
            update = {**update, "business_scope_id": update["scope"]}
            del update["scope"]
        return super().model_copy(update=update, deep=deep)


class AdapterCapability(PredictionStrictModel):
    id: Id
    display_name: str = Field(min_length=1, max_length=160)
    tools: list[ToolCapability]
    models: list[ModelOption]
    limits: PredictionLimits
    health: CapabilityHealth


class TypeCapability(AdapterCapability):
    """Public capability view returned by the explicit registry."""


class AdapterManifest(PredictionStrictModel):
    """Metadata only; a bare manifest is never executable or READY."""

    adapter_id: Id
    model_id: Id
    model_version: str = Field(min_length=1, max_length=128)
    business_scope_id: BusinessScopeId
    manifest_version: str = Field(min_length=1, max_length=64)
    input_schema_version: str = Field(min_length=1, max_length=64)
    output_schema_version: str = Field(min_length=1, max_length=64)
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    preprocessing_profile_id: Id | None = None
    preprocessing_profile_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    schema_available: bool = False
    assets_available: bool = False
    data_origin: Literal["production", "fixture"]

    @model_validator(mode="after")
    def validate_preprocessing_profile_pair(self) -> "AdapterManifest":
        if (self.preprocessing_profile_id is None) != (self.preprocessing_profile_sha256 is None):
            raise ValueError("preprocessing profile id and sha256 must be supplied together")
        return self


class ModelVerificationEvidence(PredictionStrictModel):
    """Evidence emitted by the trusted build validation port."""

    adapter_id: Id
    model_id: Id
    model_version: str = Field(min_length=1, max_length=128)
    business_scope_id: BusinessScopeId
    input_schema_version: str = Field(min_length=1, max_length=64)
    output_schema_version: str = Field(min_length=1, max_length=64)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preprocessing_profile_id: Id | None = None
    preprocessing_profile_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    manifest_verified: bool
    schema_verified: bool
    artifact_sha_verified: bool
    environment_verified: bool
    golden_sample_verified: bool
    anomaly_checks_verified: bool
    schema_by_tool: dict[str, dict[str, dict[str, object]]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_preprocessing_profile_pair(self) -> "ModelVerificationEvidence":
        if (self.preprocessing_profile_id is None) != (self.preprocessing_profile_sha256 is None):
            raise ValueError("preprocessing profile id and sha256 must be supplied together")
        return self

    @field_validator("schema_by_tool")
    @classmethod
    def validate_tool_schemas(cls, value: dict[str, dict[str, dict[str, object]]]):
        for tool_id, schemas in value.items():
            if not tool_id or set(schemas) != {"input_schema", "output_schema"}:
                raise ValueError("schema_by_tool must bind input_schema and output_schema for each tool")
        return value


_HANDLE_TOKEN = object()


class VerifiedModelHandle:
    """Opaque, immutable binding of all checks needed for executable health."""

    # Keep no writable state on the instance.  ``object.__setattr__`` can
    # bypass a custom ``__setattr__`` for ordinary slots, so the binding lives
    # in a weak side table and every public value is exposed through a
    # read-only descriptor.
    __slots__ = ("__weakref__",)

    def __init__(self, *, _token: object, evidence: ModelVerificationEvidence) -> None:
        if _token is not _HANDLE_TOKEN:
            raise TypeError("VerifiedModelHandle must come from the verification port")
        frozen_schemas = {
            tool_id: MappingProxyType(
                {
                    key: _canonical_json_bytes(schema)
                    for key, schema in schemas.items()
                }
            )
            for tool_id, schemas in evidence.schema_by_tool.items()
        }
        _HANDLE_STATE[self] = _HandleState(
            adapter_id=evidence.adapter_id,
            model_id=evidence.model_id,
            model_version=evidence.model_version,
            business_scope_id=evidence.business_scope_id,
            input_schema_version=evidence.input_schema_version,
            output_schema_version=evidence.output_schema_version,
            artifact_sha256=evidence.artifact_sha256,
            preprocessing_profile_id=evidence.preprocessing_profile_id,
            preprocessing_profile_sha256=evidence.preprocessing_profile_sha256,
            schema_by_tool=MappingProxyType(frozen_schemas),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("VerifiedModelHandle is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("VerifiedModelHandle is immutable")

    def _state(self) -> "_HandleState":
        try:
            return _HANDLE_STATE[self]
        except KeyError as exc:
            raise AttributeError("VerifiedModelHandle is not initialized") from exc

    @property
    def adapter_id(self) -> str:
        return self._state().adapter_id

    @property
    def model_id(self) -> str:
        return self._state().model_id

    @property
    def model_version(self) -> str:
        return self._state().model_version

    @property
    def business_scope_id(self) -> str:
        return self._state().business_scope_id

    @property
    def input_schema_version(self) -> str:
        return self._state().input_schema_version

    @property
    def output_schema_version(self) -> str:
        return self._state().output_schema_version

    @property
    def artifact_sha256(self) -> str:
        return self._state().artifact_sha256

    @property
    def preprocessing_profile_id(self) -> str | None:
        return self._state().preprocessing_profile_id

    @property
    def preprocessing_profile_sha256(self) -> str | None:
        return self._state().preprocessing_profile_sha256

    @property
    def _schema_by_tool(self) -> MappingProxyType:
        return self._state().schema_by_tool

    def schema_for(self, tool_id: str) -> dict[str, dict[str, object]] | None:
        schemas = self._state().schema_by_tool.get(tool_id)
        return None if schemas is None else {
            key: json.loads(value.decode("utf-8")) for key, value in schemas.items()
        }

    @property
    def schema_by_tool(self) -> dict[str, dict[str, dict[str, object]]]:
        """Return an isolated view; callers cannot mutate the binding."""
        return {
            tool_id: {
                key: json.loads(value.decode("utf-8")) for key, value in schemas.items()
            }
            for tool_id, schemas in self._state().schema_by_tool.items()
        }


class _HandleState(NamedTuple):
    adapter_id: str
    model_id: str
    model_version: str
    business_scope_id: str
    input_schema_version: str
    output_schema_version: str
    artifact_sha256: str
    preprocessing_profile_id: str | None
    preprocessing_profile_sha256: str | None
    schema_by_tool: MappingProxyType


_HANDLE_STATE: weakref.WeakKeyDictionary[VerifiedModelHandle, _HandleState] = weakref.WeakKeyDictionary()


def _canonical_json_bytes(value: object) -> bytes:
    """Encode a schema as strict, deterministic JSON with safe public errors."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise ValueError("schema contains a value that is not valid JSON") from exc


def issue_verified_model_handle(
    manifest: AdapterManifest, evidence: ModelVerificationEvidence
) -> VerifiedModelHandle:
    """Create a handle only after the trusted verification port bound facts."""

    bindings = (
        (manifest.adapter_id, evidence.adapter_id),
        (manifest.model_id, evidence.model_id),
        (manifest.model_version, evidence.model_version),
        (manifest.business_scope_id, evidence.business_scope_id),
        (manifest.input_schema_version, evidence.input_schema_version),
        (manifest.output_schema_version, evidence.output_schema_version),
        (manifest.artifact_sha256, evidence.artifact_sha256),
        (manifest.preprocessing_profile_id, evidence.preprocessing_profile_id),
        (manifest.preprocessing_profile_sha256, evidence.preprocessing_profile_sha256),
    )
    checks = (
        evidence.manifest_verified, evidence.schema_verified, evidence.artifact_sha_verified,
        evidence.environment_verified, evidence.golden_sample_verified, evidence.anomaly_checks_verified,
    )
    if manifest.data_origin != "production" or any(left != right for left, right in bindings) or not all(checks):
        raise ValueError("manifest and verification evidence are not bound")
    return VerifiedModelHandle(_token=_HANDLE_TOKEN, evidence=evidence)


class ModelUnavailableError(APIError):
    """Safe public 503 semantic for a missing or incomplete model contract."""

    def __init__(self, chromatography_type: str, *, message: str = "the prediction model is unavailable") -> None:
        super().__init__(
            503,
            "MODEL_UNAVAILABLE",
            message,
            retryable=True,
            details={"chromatography_type": chromatography_type},
        )


class PredictionContractError(APIError):
    """Public 422 semantic for a request outside an adapter contract."""

    def __init__(self, message: str = "prediction input does not match the adapter contract") -> None:
        super().__init__(422, "PREDICTION_INPUT_INVALID", message, details={})


@runtime_checkable
class PredictionAdapter(Protocol):
    type_id: str
    capability: AdapterCapability
    manifest: AdapterManifest | None
    verified_handle: VerifiedModelHandle | None
    request_types: tuple[type[BaseModel], ...]

    def validate_request(self, request: BaseModel) -> BaseModel:
        """Validate the already type-specific request DTO."""

    def predict(self, request: BaseModel) -> object:
        """Execute the validated request."""


class PredictionPreflightPort(Protocol):
    """Optional adapter port for checks required before atomic persistence."""

    def preflight_request(
        self,
        request: BaseModel,
        *,
        project_id: str,
        model_snapshot: ModelSnapshot,
        authorized_modifier_structures: Mapping[str, str] | None = None,
    ) -> BaseModel: ...


AdapterProtocol = PredictionAdapter
PredictionAdapterProtocol = PredictionAdapter


class CapabilityRegistry:
    """Registry populated only by explicit build assembly calls."""

    def __init__(self) -> None:
        self._adapters: dict[str, PredictionAdapter] = {}
        self._capabilities: dict[str, TypeCapability] = {}

    def register(self, adapter: PredictionAdapter) -> None:
        type_id = getattr(adapter, "type_id", None)
        capability = getattr(adapter, "capability", None)
        if not isinstance(type_id, str) or type_id not in _SUPPORTED_TYPES:
            raise ValueError("adapter type_id is unsupported")
        if not isinstance(capability, AdapterCapability) or capability.id != type_id:
            raise ValueError("adapter capability must match type_id")
        if not capability.tools or not capability.models:
            raise ValueError("adapter capability must declare tools and models")
        if not callable(getattr(adapter, "validate_request", None)):
            raise ValueError("adapter must expose validate_request")
        if not callable(getattr(adapter, "predict", None)):
            raise ValueError("adapter must expose a predict method")
        request_types = getattr(adapter, "request_types", None)
        if not isinstance(request_types, tuple) or not request_types or any(
            not isinstance(item, type) or not issubclass(item, BaseModel) for item in request_types
        ):
            raise ValueError("adapter request_types must declare Pydantic DTO classes")
        if type_id in self._adapters:
            raise ValueError(f"adapter already registered: {type_id}")

        sfc_bindings = getattr(adapter, "sfc_bindings", None) if type_id == "sfc" else None
        if isinstance(sfc_bindings, Mapping):
            self._register_sfc_dispatch(adapter, capability, sfc_bindings)
            return

        manifest = getattr(adapter, "manifest", None)
        handle = getattr(adapter, "verified_handle", None)
        health = self._health_for(type_id, capability, manifest, handle)
        tools = []
        for tool in capability.tools:
            schemas = handle.schema_for(tool.id) if handle is not None else None
            tool_health = health
            if health.status == "READY" and schemas is None:
                tool_health = CapabilityHealth(
                    status="UNAVAILABLE",
                    reason="MODEL_SCHEMA_NOT_VERIFIED",
                    checked_at=datetime.now(timezone.utc),
                )
            tools.append(
                tool.model_copy(
                    update={
                        "health": tool_health,
                        "input_schema": schemas["input_schema"] if schemas else tool.input_schema,
                        "output_schema": schemas["output_schema"] if schemas else tool.output_schema,
                    }
                )
            )
        if health.status == "READY" and any(tool.health.status != "READY" for tool in tools):
            health = CapabilityHealth(
                status="UNAVAILABLE",
                reason="MODEL_SCHEMA_NOT_VERIFIED",
                checked_at=datetime.now(timezone.utc),
            )
            tools = [tool.model_copy(update={"health": health}) for tool in tools]
        models = []
        for model in capability.models:
            model_health = health
            if handle is not None and (
                model.id != handle.model_id
                or model.version != handle.model_version
                or model.input_schema_version != handle.input_schema_version
                or model.output_schema_version != handle.output_schema_version
                or model.artifact_sha256 != handle.artifact_sha256
                or model.preprocessing_profile_id != handle.preprocessing_profile_id
                or model.preprocessing_profile_sha256 != handle.preprocessing_profile_sha256
                or model.scope != handle.business_scope_id
            ):
                model_health = CapabilityHealth(
                    status="UNAVAILABLE",
                    reason=(
                        "MODEL_VERIFICATION_NOT_PERFORMED"
                        if type_id == "sfc" and (model.id, model.version) != (handle.model_id, handle.model_version)
                        else "MODEL_VERIFICATION_MISMATCH"
                    ),
                    checked_at=datetime.now(timezone.utc),
                )
            models.append(model.model_copy(update={"health": model_health}))
        effective = TypeCapability(
            id=capability.id,
            display_name=capability.display_name,
            tools=tools,
            models=models,
            limits=capability.limits,
            health=health,
        )
        self._adapters[type_id] = adapter
        self._capabilities[type_id] = effective

    def _register_sfc_dispatch(
        self,
        adapter: PredictionAdapter,
        capability: AdapterCapability,
        bindings: Mapping[tuple[str, str], object],
    ) -> None:
        """Build independent SFC readiness from each exact leaf proof."""
        leaf_health: dict[tuple[str, str], CapabilityHealth] = {}
        leaf_by_tool: dict[str, list[tuple[object, CapabilityHealth]]] = {}
        for key, leaf in bindings.items():
            manifest = getattr(leaf, "manifest", None)
            handle = getattr(leaf, "verified_handle", None)
            leaf_capability = getattr(leaf, "capability", None)
            if not isinstance(leaf_capability, AdapterCapability):
                raise ValueError("SFC binding capability is invalid")
            health = self._health_for("sfc", leaf_capability, manifest, handle)
            if len(key) != 2 or key != (getattr(handle, "model_id", None), getattr(handle, "model_version", None)):
                health = CapabilityHealth(
                    status="UNAVAILABLE", reason="MODEL_VERIFICATION_MISMATCH", checked_at=datetime.now(timezone.utc)
                )
            leaf_health[key] = health
            runtime = getattr(leaf, "runtime", None)
            tool_id = getattr(runtime, "allowed_tool", None)
            if isinstance(tool_id, str):
                schemas = getattr(handle, "schema_for", lambda _tool_id: None)(tool_id)
                if health.status == "READY" and not (
                    isinstance(schemas, Mapping)
                    and {"input_schema", "output_schema"}.issubset(schemas)
                ):
                    health = CapabilityHealth(
                        status="UNAVAILABLE", reason="MODEL_SCHEMA_NOT_VERIFIED", checked_at=datetime.now(timezone.utc)
                    )
                    leaf_health[key] = health
                leaf_by_tool.setdefault(tool_id, []).append((leaf, health))

        tools: list[ToolCapability] = []
        for tool in capability.tools:
            candidates = leaf_by_tool.get(tool.id, [])
            ready = [(leaf, health) for leaf, health in candidates if health.status == "READY"]
            schema_values = [
                getattr(leaf, "verified_handle").schema_for(tool.id)
                for leaf, _health in ready
            ]
            schema_consistent = (
                bool(schema_values)
                and all(isinstance(value, Mapping) and {"input_schema", "output_schema"}.issubset(value) for value in schema_values)
                and all(value == schema_values[0] for value in schema_values)
            )
            tool_health = CapabilityHealth(
                status="READY" if schema_consistent else "UNAVAILABLE",
                reason=None if schema_consistent else "MODEL_SCHEMA_NOT_VERIFIED",
                checked_at=datetime.now(timezone.utc),
            )
            schemas = schema_values[0] if schema_consistent else None
            tools.append(tool.model_copy(update={
                "health": tool_health,
                "input_schema": schemas["input_schema"] if schemas else tool.input_schema,
                "output_schema": schemas["output_schema"] if schemas else tool.output_schema,
            }))

        ready_type = any(tool.health.status == "READY" for tool in tools)
        type_health = CapabilityHealth(
            status="READY" if ready_type else "UNAVAILABLE",
            reason=None if ready_type else "MODEL_VERIFICATION_REQUIRED",
            checked_at=datetime.now(timezone.utc),
        )

        models: list[ModelOption] = []
        for model in capability.models:
            key = (model.id, model.version)
            health = leaf_health.get(key)
            leaf = bindings.get(key)
            if health is None or leaf is None or health.status != "READY":
                health = CapabilityHealth(
                    status="UNAVAILABLE",
                    reason="MODEL_VERIFICATION_NOT_PERFORMED" if leaf is None else (health.reason if health else "MODEL_VERIFICATION_NOT_PERFORMED"),
                    checked_at=datetime.now(timezone.utc),
                )
            elif not any(tool.health.status == "READY" and tool.id == getattr(leaf.runtime, "allowed_tool", None) for tool in tools):
                health = CapabilityHealth(
                    status="UNAVAILABLE", reason="MODEL_SCHEMA_NOT_VERIFIED", checked_at=datetime.now(timezone.utc)
                )
            handle = getattr(leaf, "verified_handle", None) if leaf is not None else None
            if health.status == "READY" and handle is not None and (
                model.id != handle.model_id
                or model.version != handle.model_version
                or model.scope != handle.business_scope_id
                or model.artifact_sha256 != handle.artifact_sha256
                or model.input_schema_version != handle.input_schema_version
                or model.output_schema_version != handle.output_schema_version
                or model.preprocessing_profile_id != handle.preprocessing_profile_id
                or model.preprocessing_profile_sha256 != handle.preprocessing_profile_sha256
            ):
                health = CapabilityHealth(
                    status="UNAVAILABLE", reason="MODEL_VERIFICATION_MISMATCH", checked_at=datetime.now(timezone.utc)
                )
            models.append(model.model_copy(update={"health": health}))

        effective = TypeCapability(
            id=capability.id,
            display_name=capability.display_name,
            tools=tools,
            models=models,
            limits=capability.limits,
            health=type_health,
        )
        self._adapters["sfc"] = adapter
        self._capabilities["sfc"] = effective

    @staticmethod
    def _health_for(
        type_id: str,
        capability: AdapterCapability,
        manifest: AdapterManifest | None,
        handle: VerifiedModelHandle | None,
    ) -> CapabilityHealth:
        checked_at = datetime.now(timezone.utc)
        if manifest is None:
            return CapabilityHealth(status="UNAVAILABLE", reason="MODEL_CONTRACT_MISSING", checked_at=checked_at)
        if manifest.data_origin != "production":
            return CapabilityHealth(status="UNAVAILABLE", reason="MODEL_CONTRACT_FIXTURE_ONLY", checked_at=checked_at)
        if not manifest.schema_available or not manifest.assets_available:
            return CapabilityHealth(status="UNAVAILABLE", reason="MODEL_CONTRACT_INCOMPLETE", checked_at=checked_at)
        if handle is None:
            return CapabilityHealth(status="UNAVAILABLE", reason="MODEL_VERIFICATION_REQUIRED", checked_at=checked_at)
        if type_id != "sfc" and len({model.scope for model in capability.models}) != len(capability.models):
            return CapabilityHealth(status="UNAVAILABLE", reason="MODEL_SCOPE_NOT_UNIQUE", checked_at=checked_at)
        matching_models = [
            model
            for model in capability.models
            if (
                model.id == handle.model_id
                and model.version == handle.model_version
                and model.input_schema_version == handle.input_schema_version
                and model.output_schema_version == handle.output_schema_version
                and model.artifact_sha256 == handle.artifact_sha256
                and model.preprocessing_profile_id == handle.preprocessing_profile_id
                and model.preprocessing_profile_sha256 == handle.preprocessing_profile_sha256
                and model.scope == handle.business_scope_id
            )
        ]
        conflicting_models = [
            model
            for model in capability.models
            if (
                model.id == handle.model_id
                and model.version == handle.model_version
                and model.input_schema_version == handle.input_schema_version
                and model.output_schema_version == handle.output_schema_version
                and model.artifact_sha256 == handle.artifact_sha256
                and model.preprocessing_profile_id == handle.preprocessing_profile_id
                and model.preprocessing_profile_sha256 == handle.preprocessing_profile_sha256
                and model.scope != handle.business_scope_id
            )
        ]
        if (
            manifest.model_id != handle.model_id
            or manifest.model_version != handle.model_version
            or manifest.business_scope_id != handle.business_scope_id
            or manifest.input_schema_version != handle.input_schema_version
            or manifest.output_schema_version != handle.output_schema_version
            or manifest.artifact_sha256 != handle.artifact_sha256
            or manifest.preprocessing_profile_id != handle.preprocessing_profile_id
            or manifest.preprocessing_profile_sha256 != handle.preprocessing_profile_sha256
            or handle.adapter_id != type_id
            or not matching_models
            or conflicting_models
            or any(tool.schema_version != handle.input_schema_version for tool in capability.tools)
        ):
            return CapabilityHealth(status="UNAVAILABLE", reason="MODEL_VERIFICATION_MISMATCH", checked_at=checked_at)
        return CapabilityHealth(status="READY", reason=None, checked_at=checked_at)

    def registered_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def capabilities(self) -> list[TypeCapability]:
        return [self._capabilities[type_id] for type_id in sorted(self._capabilities)]

    def capability(self, type_id: str) -> TypeCapability:
        try:
            return self._capabilities[type_id]
        except KeyError as exc:
            raise KeyError(f"prediction adapter is not built: {type_id}") from exc

    def adapter(self, type_id: str) -> PredictionAdapter:
        try:
            return self._adapters[type_id]
        except KeyError as exc:
            raise KeyError(f"prediction adapter is not built: {type_id}") from exc

    def get_tool(self, chromatography_type: str, tool_id: str, schema_version: str) -> ToolCapability:
        try:
            capability = self.capability(chromatography_type)
        except KeyError as exc:
            raise InputValidationError("prediction type is not supported") from exc
        for tool in capability.tools:
            if tool.id == tool_id and tool.schema_version == schema_version:
                return tool
        raise InputValidationError("prediction tool or schema is not supported")

    def _resolve_model_option(self, type_id: str, request: object, *, tool: ToolCapability) -> ModelOption:
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
            # Exact requests intentionally omit scope.  A duplicate id/version
            # therefore remains ambiguous and must never pick an arbitrary scope.
            candidates = [
                model for model in capability.models
                if model.id == selection.model_id and model.version == selection.model_version
            ]
            if type_id == "sfc" and len(candidates) != 1:
                raise PredictionContractError("SFC exact model id/version is missing or ambiguous")
            if type_id in {"cc", "hplc", "gc"} and candidates and not any(
                model.preprocessing_profile_id == selection.preprocessing_profile_id
                and model.preprocessing_profile_sha256 == selection.preprocessing_profile_sha256
                for model in candidates
            ):
                raise PredictionContractError("预处理版本已变化，请按当前模型能力重新选择")
            candidates = [
                model for model in candidates
                if model.preprocessing_profile_id == selection.preprocessing_profile_id
                and model.preprocessing_profile_sha256 == selection.preprocessing_profile_sha256
            ]
        else:
            raise InputValidationError("unsupported model selection")
        ready = [model for model in candidates if model.health.status == "READY" and model.artifact_sha256]
        if len(ready) != 1:
            raise ModelUnavailableError(type_id)
        return ready[0]

    @staticmethod
    def _snapshot(model: ModelOption) -> ModelSnapshot:
        if not model.artifact_sha256:
            raise ModelUnavailableError(model.task_type)
        return ModelSnapshot(
            id=model.id,
            version=model.version,
            business_scope_id=model.scope,
            artifact_sha256=model.artifact_sha256,
            input_schema_version=model.input_schema_version,
            output_schema_version=model.output_schema_version,
            preprocessing_profile_id=model.preprocessing_profile_id,
            preprocessing_profile_sha256=model.preprocessing_profile_sha256,
            score_semantics=model.score_semantics,
            threshold=model.threshold,
        )

    def resolve_model(self, _ctx: object, request: object, *, tool: ToolCapability) -> ModelSnapshot:
        """Resolve one READY model and persist its immutable business scope."""

        type_id = getattr(request, "chromatography_type", None)
        return self._snapshot(self._resolve_model_option(type_id, request, tool=tool))

    def _validate_execution_binding(
        self,
        type_id: str,
        request: BaseModel,
        *,
        tool: ToolCapability,
        snapshot: ModelSnapshot,
    ) -> None:
        adapter = self._adapters[type_id]
        if type_id == "sfc":
            binding_for = getattr(adapter, "binding_for", None)
            binding = binding_for(snapshot.id, snapshot.version) if callable(binding_for) else adapter
            handle = getattr(binding, "verified_handle", None)
        else:
            binding = adapter
            handle = getattr(adapter, "verified_handle", None)
        if not isinstance(handle, VerifiedModelHandle):
            raise ModelUnavailableError(type_id)
        legacy_sfc_execution = type_id == "sfc" and isinstance(
            request, (SfcSingleColumnExecutionV10, SfcFullColumnExecutionV10)
        )
        if type_id == "sfc" and request.model_selection.mode != "exact":
            raise PredictionContractError("SFC execution requires an exact frozen model selection")
        if type_id in {"cc", "hplc", "gc"} and (
            snapshot.preprocessing_profile_id is None
            or snapshot.preprocessing_profile_sha256 is None
        ):
            raise ModelUnavailableError(
                type_id,
                message="历史预测未绑定预处理版本，不能重新执行；请重新创建预测。",
            )
        selection = request.model_selection
        if type_id in {"cc", "hplc", "gc"} and selection.mode != "exact":
            raise PredictionContractError("该预测必须使用能力中声明的精确预处理版本")
        if selection.mode == "exact" and (
            selection.preprocessing_profile_id != snapshot.preprocessing_profile_id
            or selection.preprocessing_profile_sha256 != snapshot.preprocessing_profile_sha256
        ):
            raise PredictionContractError("所选预处理版本与已绑定快照不一致，请按当前模型能力重新选择")
        if (
            snapshot.id != handle.model_id
            or snapshot.version != handle.model_version
            or snapshot.business_scope_id != handle.business_scope_id
            or snapshot.artifact_sha256 != handle.artifact_sha256
            or (not legacy_sfc_execution and snapshot.input_schema_version != handle.input_schema_version)
            or (legacy_sfc_execution and (snapshot.input_schema_version != "1.0" or handle.input_schema_version != "1.1"))
            or snapshot.output_schema_version != handle.output_schema_version
            or snapshot.preprocessing_profile_id != handle.preprocessing_profile_id
            or snapshot.preprocessing_profile_sha256 != handle.preprocessing_profile_sha256
            or tool.schema_version != ("1.1" if legacy_sfc_execution else snapshot.input_schema_version)
            or request.schema_version != snapshot.input_schema_version
        ):
            raise ModelUnavailableError(type_id)
        if selection.mode == "champion" and selection.scope_id != snapshot.business_scope_id:
            raise PredictionContractError("prediction scope does not match the bound model")
        if selection.mode == "exact" and (
            selection.model_id != snapshot.id
            or selection.model_version != snapshot.version
            or selection.preprocessing_profile_id != snapshot.preprocessing_profile_id
            or selection.preprocessing_profile_sha256 != snapshot.preprocessing_profile_sha256
        ):
            raise PredictionContractError("prediction selection does not match the bound model")
        model_matches = [
            model for model in self._capabilities[type_id].models
            if (
                model.id == snapshot.id
                and model.version == snapshot.version
                and model.scope == snapshot.business_scope_id
                and model.artifact_sha256 == snapshot.artifact_sha256
                and (model.input_schema_version == snapshot.input_schema_version or (
                    legacy_sfc_execution and model.input_schema_version == "1.1"
                    and snapshot.input_schema_version == "1.0"
                ))
                and model.output_schema_version == snapshot.output_schema_version
                and model.preprocessing_profile_id == snapshot.preprocessing_profile_id
                and model.preprocessing_profile_sha256 == snapshot.preprocessing_profile_sha256
            )
        ]
        if len(model_matches) != 1:
            raise ModelUnavailableError(type_id)
        if type_id == "sfc":
            exact_model = model_matches[0]
            exact_tool = next((item for item in self._capabilities[type_id].tools if item.id == tool.id), None)
            if exact_model.health.status != "READY" or exact_tool is None or exact_tool.health.status != "READY":
                raise ModelUnavailableError(type_id)

    def validate_request(
        self,
        type_id: str,
        request: object,
        *,
        model_snapshot: ModelSnapshot,
        authorized_modifier_structures: Mapping[str, str] | None = None,
        run_preflight: bool = True,
    ) -> BaseModel:
        """Validate a request against its bound adapter without running science."""
        adapter = self._adapters.get(type_id)
        if adapter is None:
            raise KeyError(f"prediction adapter is not built: {type_id}")
        capability = self._capabilities[type_id]
        if capability.health.status != "READY":
            raise ModelUnavailableError(type_id)
        if not isinstance(request, BaseModel):
            raise PredictionContractError()
        request_types = getattr(adapter, "request_types")
        if not isinstance(request, request_types):
            raise PredictionContractError()
        tool_id = getattr(request, "tool", None)
        if getattr(request, "chromatography_type", None) != type_id or tool_id not in {
            tool.id for tool in capability.tools
        }:
            raise PredictionContractError()
        tool = next(tool for tool in capability.tools if tool.id == tool_id)
        legacy_sfc_execution = type_id == "sfc" and isinstance(
            request, (SfcSingleColumnExecutionV10, SfcFullColumnExecutionV10)
        )
        if request.schema_version != tool.schema_version and not (
            legacy_sfc_execution and request.schema_version == "1.0" and tool.schema_version == "1.1"
        ):
            raise PredictionContractError()
        if not isinstance(model_snapshot, ModelSnapshot):
            raise PredictionContractError("a verified model snapshot is required")
        self._validate_execution_binding(type_id, request, tool=tool, snapshot=model_snapshot)
        try:
            validated = adapter.validate_request(request)
        except InputValidationError:
            raise PredictionContractError() from None
        if not isinstance(validated, request_types):
            raise PredictionContractError()
        if (
            getattr(validated, "chromatography_type", None) != type_id
            or getattr(validated, "tool", None) != tool_id
            or getattr(validated, "schema_version", None) != request.schema_version
        ):
            raise PredictionContractError()
        preflight = getattr(adapter, "preflight_request", None)
        if run_preflight and callable(preflight):
            try:
                preflight_kwargs = {
                    "project_id": getattr(request, "project_id", ""),
                    "model_snapshot": model_snapshot,
                }
                if "authorized_modifier_structures" in inspect.signature(preflight).parameters:
                    preflight_kwargs["authorized_modifier_structures"] = authorized_modifier_structures
                validated = preflight(validated, **preflight_kwargs)
            except InputValidationError:
                raise PredictionContractError() from None
            if not isinstance(validated, request_types):
                raise PredictionContractError()
        self._validate_execution_binding(type_id, validated, tool=tool, snapshot=model_snapshot)
        return validated

    def execute_snapshot(
        self,
        type_id: str,
        request: object,
        *,
        model_snapshot: ModelSnapshot,
        normalized_structures: list[str] | None = None,
        normalized_modifier_structures: Mapping[str, str] | None = None,
    ) -> object:
        """Execute only a previously accepted, scope-bound model snapshot.

        Workers must pass the persisted snapshot so a later Champion change
        cannot change the model used by a queued prediction.
        """

        adapter = self._adapters.get(type_id)
        if adapter is None:
            raise KeyError(f"prediction adapter is not built: {type_id}")
        validated = self.validate_request(
            type_id,
            request,
            model_snapshot=model_snapshot,
            run_preflight=False,
        )
        worker_predict = getattr(adapter, "predict_with_normalized_structures", None)
        if normalized_structures is not None and callable(worker_predict):
            worker_kwargs = {}
            if "normalized_modifier_structures" in inspect.signature(worker_predict).parameters:
                worker_kwargs["normalized_modifier_structures"] = normalized_modifier_structures
            return worker_predict(validated, normalized_structures, **worker_kwargs)
        return adapter.predict(validated)

    def execute(
        self,
        type_id: str,
        request: object,
        *,
        model_snapshot: ModelSnapshot | None = None,
    ) -> object:
        """Resolve and execute through the same central binding checks.

        Direct callers get a current snapshot; queued workers should call
        :meth:`execute_snapshot` with their persisted snapshot.
        """
        adapter = self._adapters.get(type_id)
        if adapter is None:
            raise KeyError(f"prediction adapter is not built: {type_id}")
        if model_snapshot is not None:
            return self.execute_snapshot(type_id, request, model_snapshot=model_snapshot)
        if self._capabilities[type_id].health.status != "READY":
            raise ModelUnavailableError(type_id)
        if not isinstance(request, BaseModel):
            raise PredictionContractError()
        if not isinstance(request, getattr(adapter, "request_types", ())) or getattr(
            request, "chromatography_type", None
        ) != type_id:
            raise PredictionContractError()
        tool_id = getattr(request, "tool", None)
        schema_version = getattr(request, "schema_version", None)
        try:
            tool = self.get_tool(type_id, tool_id, schema_version)
            snapshot = self.resolve_model(None, request, tool=tool)
        except ModelUnavailableError:
            raise
        except InputValidationError:
            raise PredictionContractError()
        return self.execute_snapshot(type_id, request, model_snapshot=snapshot)


__all__ = [
    "AdapterCapability",
    "AdapterManifest",
    "AdapterProtocol",
    "CapabilityRegistry",
    "ChromatographyType",
    "ModelOption",
    "ModelUnavailableError",
    "ModelVerificationEvidence",
    "PredictionAdapter",
    "PredictionAdapterProtocol",
    "PredictionPreflightPort",
    "PredictionContractError",
    "PredictionLimits",
    "SfcAllColumnDomain",
    "SfcDomain",
    "SfcSingleColumnDomain",
    "StructureCount",
    "ToolCapability",
    "TypeCapability",
    "VerifiedModelHandle",
    "issue_verified_model_handle",
]
