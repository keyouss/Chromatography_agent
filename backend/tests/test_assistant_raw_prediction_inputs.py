from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord, IdempotencyScope
from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.priority import (
    PRIORITY_TOOL_NAMES,
    PriorityAssistantService,
    PriorityAssistantToolService,
    PriorityOperationContext,
    _raw_prediction_arguments_schema,
    priority_toolset,
)
from chrom_platform.modules.model_center.schemas import now_utc
from chrom_platform.structure_core.router import _normalize_in_transaction
from chrom_platform.structure_core.schemas import NormalizeStructureRequest, OriginalInput, StructureView


def _actor() -> ActorContext:
    return ActorContext(
        actor_id="actor-a",
        project_ids=["project-a"],
        auth_method="service",
        permissions=["*"],
        policy_version="p1",
        default_project_id="project-a",
    )


def _hplc_models(count: int = 1) -> list[dict]:
    return [
        {
            "id": f"hplc-model-{index}",
            "version": f"{index}.0",
            "task_type": "hplc",
            "health": {"status": "READY"},
            "preprocessing_profile_id": f"profile-{index}",
            "preprocessing_profile_sha256": f"{index:064x}",
        }
        for index in range(1, count + 1)
    ]


class _Capabilities:
    def __init__(self, models: list[dict]):
        self.models = models

    def capabilities(self):
        return {
            "capabilities": [{
                "id": "hplc",
                "health": {"status": "READY"},
                "tools": [{"id": "single", "schema_version": "1.1", "health": {"status": "READY"}}],
                "models": self.models,
            }]
        }


class _Structures:
    def __init__(self):
        self.requests = []
        self.keys = []

    def normalize(self, _ctx, request, *, idempotency_key):
        index = len(self.requests) + 1
        self.requests.append(request)
        self.keys.append(idempotency_key)
        return SimpleNamespace(data=SimpleNamespace(
            id=f"str-{index}",
            canonical_smiles=f"canonical-{index}",
            snapshot_hash=f"{index:064x}",
            has_stereochemistry=True,
            stereochemistry_status="specified",
            stereochemistry_warnings=[],
        ))


def test_raw_schema_covers_all_types_and_keeps_hplc_single_source() -> None:
    schema = _raw_prediction_arguments_schema()
    branches = schema["oneOf"]
    raw_types = {branch["properties"]["chromatography_type"]["const"] for branch in branches}
    assert raw_types == {"tlc", "cc", "hplc", "chiral_lc", "gc", "sfc"}

    hplc = next(branch for branch in branches if branch["properties"]["chromatography_type"]["const"] == "hplc")
    assert hplc["properties"]["smiles"]["minItems"] == 2
    hplc_input = schema["$defs"]["AssistantRawHplcInput"]
    assert "model_input_smiles" not in hplc_input["properties"]

    assert {"single_column", "full_column"}.issubset({
        branch["properties"].get("tool", {}).get("const") for branch in branches
    })
    assert {"condition_compare", "reverse_solvent"}.issubset({
        branch["properties"].get("tool", {}).get("const") for branch in branches
    })
    specs = {spec.name: spec for spec in priority_toolset().specs}
    assert set(PRIORITY_TOOL_NAMES) == set(specs)
    assert specs["create_prediction"].schema_version == "1.1"


def test_hplc_raw_request_normalizes_in_order_and_binds_exact_profile() -> None:
    structures = _Structures()
    service = PriorityAssistantService(capabilities=_Capabilities(_hplc_models()), structures=structures)
    tools = PriorityAssistantToolService(service)
    ctx = PriorityOperationContext(actor=_actor(), server_project_id="project-a")
    raw_smiles = ["C[C@H](N)C(=O)O", "C[C@@H](N)C(=O)O"]

    request = tools._raw_prediction_request({
        "chromatography_type": "hplc",
        "smiles": raw_smiles,
        "input": {"isopropanol_fraction": 0.25, "flow_rate_ml_min": 1.2, "column_ids": ["ADH", "IA"]},
    }, ctx, idempotency_key="call-key", normalize=True)

    assert request.structure_refs == ["str-1", "str-2"]
    assert request.input.model_input_smiles == raw_smiles
    assert [item.value for item in structures.requests] == raw_smiles
    assert [item.preserve_stereochemistry for item in structures.requests] == [True, True]
    assert structures.keys == ["call-key:structure:0", "call-key:structure:1"]
    assert request.model_selection.model_id == "hplc-model-1"
    assert request.model_selection.preprocessing_profile_id == "profile-1"

    with pytest.raises(AssistantError) as error:
        tools._raw_prediction_request({
            "chromatography_type": "hplc",
            "smiles": raw_smiles,
            "input": {
                "model_input_smiles": ["replacement-a", "replacement-b"],
                "isopropanol_fraction": 0.25,
                "flow_rate_ml_min": 1.2,
                "column_ids": ["ADH", "IA"],
            },
        }, ctx, idempotency_key="conflicting-call", normalize=True)
    assert error.value.payload.code == "VALIDATION_FAILED"
    assert len(structures.requests) == 2


def test_multiple_ready_models_require_an_exact_selection_before_normalizing() -> None:
    structures = _Structures()
    service = PriorityAssistantService(capabilities=_Capabilities(_hplc_models(2)), structures=structures)
    tools = PriorityAssistantToolService(service)
    ctx = PriorityOperationContext(actor=_actor(), server_project_id="project-a")

    with pytest.raises(AssistantError) as error:
        tools._raw_prediction_request({
            "chromatography_type": "hplc",
            "smiles": ["C[C@H](N)C(=O)O", "C[C@@H](N)C(=O)O"],
            "input": {"isopropanol_fraction": 0.25, "flow_rate_ml_min": 1.2, "column_ids": ["ADH"]},
        }, ctx, idempotency_key="call-key", normalize=True)

    assert error.value.payload.code == "PREDICTION_MODEL_SELECTION_REQUIRED"
    assert len(structures.requests) == 0


def test_multiple_ready_models_accept_only_an_exact_selected_profile_pair() -> None:
    structures = _Structures()
    service = PriorityAssistantService(capabilities=_Capabilities(_hplc_models(2)), structures=structures)
    tools = PriorityAssistantToolService(service)
    ctx = PriorityOperationContext(actor=_actor(), server_project_id="project-a")

    request = tools._raw_prediction_request({
        "chromatography_type": "hplc",
        "smiles": ["C[C@H](N)C(=O)O", "C[C@@H](N)C(=O)O"],
        "input": {"isopropanol_fraction": 0.25, "flow_rate_ml_min": 1.2, "column_ids": ["ADH"]},
        "model_selection": {
            "mode": "exact",
            "model_id": "hplc-model-2",
            "model_version": "2.0",
            "preprocessing_profile_id": "profile-2",
            "preprocessing_profile_sha256": f"{2:064x}",
        },
    }, ctx, idempotency_key="call-key", normalize=True)

    assert request.model_selection.model_id == "hplc-model-2"
    assert request.model_selection.preprocessing_profile_id == "profile-2"
    assert len(structures.requests) == 2


def test_restored_prediction_arguments_without_raw_smiles_fail_closed() -> None:
    structures = _Structures()
    service = PriorityAssistantService(capabilities=_Capabilities(_hplc_models()), structures=structures)
    tools = PriorityAssistantToolService(service)
    ctx = PriorityOperationContext(actor=_actor(), server_project_id="project-a")

    with pytest.raises(AssistantError) as error:
        tools.validate(
            ctx,
            tool_name="create_prediction",
            arguments={
                "project_id": "project-a",
                "chromatography_type": "hplc",
                "tool": "single",
                "schema_version": "1.1",
                "model_selection": {"mode": "exact", "model_id": "hplc-model-1", "model_version": "1.0"},
                "structure_refs": ["structure-a", "structure-b"],
                "input": {
                    "model_input_smiles": ["C[C@H](N)C(=O)O", "C[C@@H](N)C(=O)O"],
                    "isopropanol_fraction": 0.25,
                    "flow_rate_ml_min": 1.2,
                    "column_ids": ["ADH", "IA"],
                },
            },
        )

    assert error.value.payload.code == "RECOVERY_STATE_INVALID"
    assert "先查询已有预测任务和结果" in error.value.payload.message
    assert "确认未受理" in error.value.payload.message
    assert structures.requests == []


def test_structure_audit_ids_separate_new_operations_but_true_replay_adds_none() -> None:
    class Store:
        def __init__(self):
            self.records = {}

        def get(self, scope):
            return self.records.get(scope)

        def begin(self, scope, payload_hash):
            existing = self.records.get(scope)
            if existing is not None:
                return BeginResult(existing, False)
            record = IdempotencyRecord(payload_hash=payload_hash, status="IN_PROGRESS")
            self.records[scope] = record
            return BeginResult(record, True)

        def complete(self, scope, payload_hash, status_code, response):
            self.records[scope] = IdempotencyRecord(payload_hash, "COMPLETED", status_code, response)

    class Transaction:
        def __init__(self, store, audit):
            self.idempotency = store
            self.structures = object()
            self.audit = audit

    class Uow:
        def __init__(self):
            self.store = Store()
            self.audits = []

        @contextmanager
        def transaction(self):
            yield Transaction(self.store, SimpleNamespace(record=self.audits.append))

    class Service:
        def normalize(self, payload, structure_id, _repository):
            return StructureView(
                id=structure_id,
                project_id=payload.project_id,
                schema_version="1.0",
                original_input=OriginalInput(input_type="smiles", value=payload.value),
                canonical_smiles="CCO",
                is_valid=True,
                has_stereochemistry=False,
                stereochemistry_status="not_applicable",
                stereochemistry_warnings=[],
                snapshot_hash="a" * 64,
                created_at=now_utc(),
            )

    uow = Uow()
    service = Service()
    payload = NormalizeStructureRequest(project_id="project-a", input_type="smiles", value="CCO", preserve_stereochemistry=True)
    first_scope = IdempotencyScope("actor-a", "project-a", "POST", "/api/v1/structures/normalize", "tool-1")
    second_scope = IdempotencyScope("actor-a", "project-a", "POST", "/api/v1/structures/normalize", "tool-2")
    common = {"service": service, "uow": uow, "payload": payload, "payload_hash": "b" * 64,
              "structure_id": "str-shared", "request_id": "req-same"}

    _normalize_in_transaction(**common, scope=first_scope)
    _normalize_in_transaction(**common, scope=second_scope)
    _normalize_in_transaction(**common, scope=first_scope)

    assert len(uow.audits) == 2
    assert uow.audits[0].id != uow.audits[1].id
