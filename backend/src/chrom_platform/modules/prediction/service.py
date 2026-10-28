"""Prediction creation application service.

The service validates business boundaries and delegates persistence to one
atomic port.  It never imports or calls a scientific adapter's ``predict``
method; adapter execution belongs to a worker assembly.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from pydantic import ValidationError

from chrom_platform.core.errors import APIError, InputValidationError
from chrom_platform.task_core.service import OperationContext
from chrom_platform.task_core.state import JobStatus

from .adapters import ToolCapability
from .ports import (
    AtomicPredictionCreatePort,
    AtomicPredictionCreateResult,
    PredictionAccessPort,
    PredictionCapabilityPort,
    PredictionCreateResult,
    PredictionIdempotencyPort,
    PredictionModelPort,
    PredictionStructurePort,
    StructureSnapshot,
)
from .schemas import (
    CcPredictionCreate,
    ChiralLcPredictionCreate,
    GcPredictionCreate,
    HplcPredictionCreate,
    ModelSnapshot,
    ModifierStructureSnapshot,
    PredictionAccepted,
    PredictionCreate,
    PredictionExecutionInputSnapshot,
    SfcFullColumnPredictionCreate,
    SfcSingleColumnPredictionCreate,
    TlcPredictionCreate,
    TlcConditionComparisonCreate,
    TlcReverseCreate,
)


_CREATE_TYPES = (
    TlcPredictionCreate,
    TlcConditionComparisonCreate,
    TlcReverseCreate,
    CcPredictionCreate,
    HplcPredictionCreate,
    ChiralLcPredictionCreate,
    SfcSingleColumnPredictionCreate,
    SfcFullColumnPredictionCreate,
    GcPredictionCreate,
)
_PAIR_TYPES = (CcPredictionCreate, HplcPredictionCreate, SfcSingleColumnPredictionCreate, SfcFullColumnPredictionCreate)


class PredictionServiceError(APIError):
    """Stable public error semantic for the prediction application boundary."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(status_code, code, message, retryable=retryable, details={})


def _payload_hash(request: PredictionCreate) -> str:
    payload = request.model_dump(mode="json")
    selection = payload.get("model_selection")
    if isinstance(selection, dict) and (
        selection.get("preprocessing_profile_id") is None
        and selection.get("preprocessing_profile_sha256") is None
    ):
        # Hash compatibility only: retain the serialized request shape for
        # persistence while treating the newly introduced absent/null pair
        # like the historical exact-selection payload.
        selection = dict(selection)
        selection.pop("preprocessing_profile_id", None)
        selection.pop("preprocessing_profile_sha256", None)
        payload["model_selection"] = selection
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source(ctx: OperationContext) -> str:
    if ctx.interaction in {"assistant", "agent"}:
        return "assistant"
    if ctx.interaction == "service":
        return "service"
    return "web"


@dataclass(frozen=True)
class PredictionPreparation:
    """Validated immutable inputs for either a single or batch prediction."""

    model_snapshot: ModelSnapshot
    input_snapshot: PredictionExecutionInputSnapshot

    @property
    def normalized_structures(self) -> list[str]:
        return list(self.input_snapshot.normalized_structures)

    @property
    def normalized_modifier_structures(self) -> dict[str, str]:
        return {
            item.structure_ref: item.normalized_smiles
            for item in self.input_snapshot.modifier_structures
        }


class PredictionCreateService:
    def __init__(
        self,
        *,
        access: PredictionAccessPort | None,
        structures: PredictionStructurePort | None,
        capabilities: PredictionCapabilityPort | None,
        models: PredictionModelPort | None,
        idempotency: PredictionIdempotencyPort | None,
        atomic: AtomicPredictionCreatePort | None,
    ) -> None:
        self.access = access
        self.structures = structures
        self.capabilities = capabilities
        self.models = models
        self.idempotency = idempotency
        self.atomic = atomic

    def create(
        self,
        ctx: OperationContext,
        request: PredictionCreate,
        *,
        idempotency_key: str,
    ) -> PredictionCreateResult:
        self._validate_request(request)
        if not idempotency_key or len(idempotency_key) > 128:
            raise PredictionServiceError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少有效 Idempotency-Key")
        if self.access is None:
            raise PredictionServiceError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测访问策略未配置", retryable=True)

        # Project/permission access is intentionally before idempotency replay.
        self.access.ensure_project_access(ctx, request.project_id, permission="prediction:create")
        payload_hash = _payload_hash(request)
        if self.idempotency is None:
            raise PredictionServiceError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测幂等端口未配置", retryable=True)
        replay = self.idempotency.lookup(
            ctx,
            request.project_id,
            key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            if replay.payload_hash != payload_hash:
                raise PredictionServiceError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
            return PredictionCreateResult(data=self._validate_accepted(replay.accepted), replayed=True)

        if self.atomic is None:
            raise PredictionServiceError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测原子写入端口未配置", retryable=True)
        prepared = self.prepare(ctx, request, _access_checked=True)
        created = self.atomic.create(
            ctx,
            request,
            model_snapshot=prepared.model_snapshot,
            input_snapshot=prepared.input_snapshot,
            source=_source(ctx),
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if not isinstance(created, AtomicPredictionCreateResult):
            raise PredictionServiceError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测原子写入未返回有效结果", retryable=True)
        accepted = self._validate_atomic_result(created)
        return PredictionCreateResult(data=accepted, replayed=created.replayed)

    def prepare(
        self,
        ctx: OperationContext,
        request: PredictionCreate,
        *,
        _access_checked: bool = False,
    ) -> PredictionPreparation:
        """Validate prediction inputs and freeze their model/structure snapshots.

        This method has no Job, Outbox, idempotency or prediction-record side effects.
        """
        self._validate_request(request)
        if self.access is None:
            raise PredictionServiceError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测访问策略未配置", retryable=True)
        if not _access_checked:
            self.access.ensure_project_access(ctx, request.project_id, permission="prediction:create")
        if self.capabilities is None or self.models is None:
            raise PredictionServiceError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测能力端口未配置", retryable=True)
        tool = self._get_tool(request)
        self._validate_structure_count(request, tool)
        model = self._resolve_model(ctx, request, tool)
        structures: list[StructureSnapshot] | None = None
        authorized_modifier_structures: dict[str, str] | None = None
        if request.chromatography_type == "sfc":
            # SFC modifiers are project resources too.  Authorize every ref
            # before adapter preflight so it cannot fall back to a global DB
            # lookup for a valid-looking CO/0.2 input.
            structures = self._load_structures(ctx, request)
            pair_structures = self._pair_structures(request, structures)
            self._validate_structure_relationships(ctx, request, pair_structures)
            by_id = {item.id: item for item in structures}
            authorized_modifier_structures = {
                item.structure_ref: by_id[item.structure_ref].normalized_smiles
                for item in request.input.modifier_components
            }
        try:
            capability_kwargs = {"model_snapshot": model}
            if "authorized_modifier_structures" in inspect.signature(
                self.capabilities.validate_request
            ).parameters:
                capability_kwargs["authorized_modifier_structures"] = authorized_modifier_structures
            validated = self.capabilities.validate_request(
                request.chromatography_type,
                request,
                **capability_kwargs,
            )
        except APIError:
            raise
        except InputValidationError:
            raise PredictionServiceError(
                422, "PREDICTION_INPUT_INVALID", "预测输入不符合模型能力约束"
            ) from None
        if type(validated) is not type(request):
            raise PredictionServiceError(
                422, "PREDICTION_INPUT_INVALID", "预测输入不符合模型能力约束"
            )
        if structures is None:
            structures = self._load_structures(ctx, request)
            self._validate_structure_relationships(ctx, request, structures)
        pair_structures = self._pair_structures(request, structures)
        if isinstance(request, HplcPredictionCreate):
            self._validate_hplc_raw_identity(request, pair_structures)
        modifier_snapshots = []
        if request.chromatography_type == "sfc":
            by_id = {item.id: item for item in structures}
            modifier_snapshots = [
                ModifierStructureSnapshot(
                    structure_ref=item.structure_ref,
                    normalized_smiles=by_id[item.structure_ref].normalized_smiles,
                )
                for item in request.input.modifier_components
            ]
        try:
            input_snapshot = PredictionExecutionInputSnapshot(
                structure_refs=list(request.structure_refs),
                normalized_structures=[item.normalized_smiles for item in pair_structures],
                modifier_structures=modifier_snapshots,
                input=getattr(request, "input", request),
            )
        except ValidationError:
            raise PredictionServiceError(422, "PREDICTION_INPUT_INVALID", "预测输入快照无效") from None

        return PredictionPreparation(model_snapshot=model, input_snapshot=input_snapshot)

    @staticmethod
    def _validate_request(request: object) -> None:
        if not isinstance(request, _CREATE_TYPES):
            raise PredictionServiceError(422, "PREDICTION_INPUT_INVALID", "必须使用已验证的具体预测请求 DTO")

    def _get_tool(self, request: PredictionCreate) -> ToolCapability:
        error_code = (
            "PREDICTION_INPUT_INVALID"
            if request.chromatography_type == "cc"
            else "PREDICTION_SCHEMA_UNSUPPORTED"
        )
        error_message = (
            "预测输入不符合模型能力约束"
            if request.chromatography_type == "cc"
            else "预测工具或 schema 版本不受支持"
        )
        try:
            tool = self.capabilities.get_tool(
                request.chromatography_type,
                request.tool,
                request.schema_version,
            )
        except APIError:
            raise
        except InputValidationError:
            raise PredictionServiceError(422, error_code, error_message) from None
        if not isinstance(tool, ToolCapability) or tool.id != request.tool or tool.schema_version != request.schema_version:
            raise PredictionServiceError(422, error_code, error_message)
        if tool.health.status != "READY":
            raise PredictionServiceError(503, "MODEL_UNAVAILABLE", "预测模型不可用", retryable=True)
        return tool

    @staticmethod
    def _validate_structure_count(request: PredictionCreate, tool: ToolCapability) -> None:
        count = len(request.structure_refs)
        if count < tool.structure_count.min or count > tool.structure_count.max:
            raise PredictionServiceError(422, "STRUCTURE_COUNT_INVALID", "结构数量不符合模型能力")
        if isinstance(request, _PAIR_TYPES) and len(set(request.structure_refs)) != 2:
            raise PredictionServiceError(422, "STRUCTURE_PAIR_INVALID", "对映体结构引用必须是两个不同结构")

    @staticmethod
    def _validate_atomic_result(created: AtomicPredictionCreateResult) -> PredictionAccepted:
        accepted = created.accepted
        return PredictionCreateService._validate_accepted(accepted)

    @staticmethod
    def _validate_accepted(accepted: object) -> PredictionAccepted:
        if not isinstance(accepted, PredictionAccepted):
            raise PredictionServiceError(
                503,
                "PREDICTION_DEPENDENCY_UNAVAILABLE",
                "预测原子写入未返回有效受理结果",
                retryable=True,
            )
        if (
            accepted.resource_ref.type != "prediction"
            or accepted.resource_ref.id != accepted.id
            or accepted.status not in {JobStatus.QUEUED, JobStatus.RUNNING}
        ):
            raise PredictionServiceError(
                503,
                "PREDICTION_DEPENDENCY_UNAVAILABLE",
                "预测原子写入返回的受理绑定无效",
                retryable=True,
            )
        return accepted

    def _resolve_model(
        self,
        ctx: OperationContext,
        request: PredictionCreate,
        tool: ToolCapability,
    ) -> ModelSnapshot:
        if request.chromatography_type == "sfc" and request.model_selection.mode != "exact":
            raise PredictionServiceError(422, "PREDICTION_INPUT_INVALID", "SFC v1.1 必须精确选择模型")
        try:
            model = self.models.resolve_model(ctx, request, tool=tool)
        except APIError:
            raise
        except InputValidationError:
            raise PredictionServiceError(422, "PREDICTION_INPUT_INVALID", "预测模型选择无效") from None
        if not isinstance(model, ModelSnapshot):
            raise PredictionServiceError(503, "MODEL_UNAVAILABLE", "预测模型未解析为有效快照", retryable=True)
        if model.input_schema_version != request.schema_version:
            raise PredictionServiceError(503, "MODEL_UNAVAILABLE", "预测模型 schema 版本不匹配", retryable=True)
        selection = request.model_selection
        if request.chromatography_type in {"cc", "hplc", "gc"} and (
            selection.mode != "exact"
            or selection.preprocessing_profile_id is None
            or selection.preprocessing_profile_sha256 is None
        ):
            raise PredictionServiceError(
                422, "PREDICTION_INPUT_INVALID", "该预测必须使用能力中声明的精确预处理版本"
            )
        if request.chromatography_type not in {"cc", "hplc", "gc"} and (
            getattr(selection, "preprocessing_profile_id", None) is not None
            or getattr(selection, "preprocessing_profile_sha256", None) is not None
        ):
            raise PredictionServiceError(422, "PREDICTION_INPUT_INVALID", "所选模型不支持该预处理版本")
        if selection.mode == "champion" and model.business_scope_id != selection.scope_id:
            raise PredictionServiceError(503, "MODEL_UNAVAILABLE", "预测模型业务 scope 不匹配", retryable=True)
        if selection.mode == "exact" and (
            model.id != selection.model_id
            or model.version != selection.model_version
            or model.preprocessing_profile_id != selection.preprocessing_profile_id
            or model.preprocessing_profile_sha256 != selection.preprocessing_profile_sha256
        ):
            raise PredictionServiceError(422, "PREDICTION_INPUT_INVALID", "模型或预处理版本已变化，请按当前能力重新选择")
        if not model.business_scope_id:
            raise PredictionServiceError(503, "MODEL_UNAVAILABLE", "预测模型未绑定业务 scope", retryable=True)
        return model

    def _load_structures(
        self,
        ctx: OperationContext,
        request: PredictionCreate,
    ) -> list[StructureSnapshot]:
        if self.structures is None:
            raise PredictionServiceError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "结构服务端口未配置", retryable=True)
        refs = list(request.structure_refs)
        if request.chromatography_type == "sfc":
            refs.extend(item.structure_ref for item in request.input.modifier_components)
        refs = list(dict.fromkeys(refs))
        try:
            values = self.structures.load_many(ctx, request.project_id, refs)
        except APIError:
            raise
        except (KeyError, LookupError, ValueError):
            raise PredictionServiceError(404, "STRUCTURE_NOT_FOUND", "结构不存在或不可见") from None
        if len(values) != len(refs):
            raise PredictionServiceError(404, "STRUCTURE_NOT_FOUND", "结构不存在或不可见")
        by_id = {item.id: item for item in values}
        if set(by_id) != set(refs) or any(
            item.project_id != request.project_id for item in values
        ):
            raise PredictionServiceError(404, "STRUCTURE_NOT_FOUND", "结构不存在或不可见")
        return [by_id[ref] for ref in refs]

    @staticmethod
    def _pair_structures(
        request: PredictionCreate,
        structures: list[StructureSnapshot],
    ) -> list[StructureSnapshot]:
        by_id = {item.id: item for item in structures}
        return [by_id[ref] for ref in request.structure_refs]

    @staticmethod
    def _validate_hplc_raw_identity(
        request: HplcPredictionCreate, structures: list[StructureSnapshot],
    ) -> None:
        if len(structures) != 2 or len(request.input.model_input_smiles) != 2:
            raise PredictionServiceError(422, "STRUCTURE_PAIR_INVALID", "HPLC 必须提交两个有序结构与原始 SMILES")
        try:
            from rdkit import Chem
        except ImportError:
            raise PredictionServiceError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "HPLC 结构校验能力不可用", retryable=True) from None
        for raw_smiles, structure in zip(request.input.model_input_smiles, structures):
            molecule = Chem.MolFromSmiles(raw_smiles)
            if molecule is None:
                raise PredictionServiceError(422, "HPLC_MODEL_INPUT_INVALID", "HPLC 原始 SMILES 无效或立体信息不完整")
            identity = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
            if identity != structure.normalized_smiles:
                raise PredictionServiceError(422, "HPLC_MODEL_INPUT_IDENTITY_MISMATCH", "HPLC 原始 SMILES 与同序结构引用不一致")
        # The structure service checks connectivity and inverted CIP for the
        # authorized refs. Exact per-slot isomeric identity above binds each
        # raw string to the corresponding ref without swapping either list.

    def _validate_structure_relationships(
        self,
        ctx: OperationContext,
        request: PredictionCreate,
        structures: list[StructureSnapshot],
    ) -> None:
        if self.structures is None:
            raise PredictionServiceError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "结构服务端口未配置", retryable=True)
        if isinstance(request, _PAIR_TYPES) and request.chromatography_type != "cc":
            try:
                self.structures.validate_enantiomer_pair(
                    ctx,
                    request.project_id,
                    structures,
                    chromatography_type=request.chromatography_type,
                )
            except APIError:
                raise
            except (KeyError, LookupError, ValueError):
                raise PredictionServiceError(422, "STRUCTURE_PAIR_INVALID", "对映体结构配对无效") from None
        elif isinstance(request, ChiralLcPredictionCreate):
            try:
                self.structures.validate_chiral_proposal(
                    ctx,
                    request.project_id,
                    structures[0],
                    proposal_id=request.input.enantiomer_proposal_id,
                )
            except APIError:
                raise
            except (KeyError, LookupError, ValueError):
                raise PredictionServiceError(422, "CHIRAL_PROPOSAL_INVALID", "手性结构 proposal 未确认或不匹配") from None


PredictionService = PredictionCreateService


__all__ = ["PredictionCreateService", "PredictionService", "PredictionServiceError"]
