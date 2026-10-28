"""Focused regressions for same-Run prediction waiting and model context."""

import asyncio
import time

import pytest

from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.runtime import (
    AgentRuntime,
    RuntimeConfigSnapshot,
    _model_tool_result,
    _summary,
)
from chrom_platform.modules.assistant.tools import BoundTool, ToolExecutionLedger, ToolSpec


def _runtime(tools):
    config = RuntimeConfigSnapshot(
        "https://api.openai.com/v1", "test-model", "key-ref", "revision-1",
        provider_configuration_hash="c" * 64,
    )
    return AgentRuntime(object(), config, toolset=tools, history=[])


def test_wait_polls_authorized_service_until_same_prediction_has_result():
    reads = []

    def get_prediction(_ctx, arguments):
        reads.append(arguments["prediction_id"])
        status = "RUNNING" if len(reads) == 1 else "SUCCEEDED"
        return {
            "id": "prediction-1", "job_id": "job-1", "status": status,
            "result": {"metrics": [{"key": "retention_time", "value": 1.25, "unit": "min"}]} if status == "SUCCEEDED" else None,
        }

    runtime = _runtime({"get_prediction": get_prediction})
    result = asyncio.run(runtime._wait_for_prediction(
        "create_prediction",
        {"data": {"id": "prediction-1", "job_id": "job-1", "status": "QUEUED"}},
        arguments={}, ctx=None, run_id="run-1", attempt=2,
        tool_call_id="tool-1", deadline=time.monotonic() + 3,
    ))

    assert result["status"] == "SUCCEEDED"
    assert result["result"]["metrics"][0]["value"] == 1.25
    assert reads == ["prediction-1", "prediction-1"]


def test_wait_timeout_returns_last_real_references_without_repolling():
    reads = []
    runtime = _runtime({"get_prediction": lambda _ctx, arguments: reads.append(arguments)})
    accepted = {
        "id": "prediction-1", "job_id": "job-1", "status": "QUEUED",
        "resource_ref": {"type": "prediction", "id": "prediction-1"},
    }

    result = asyncio.run(runtime._wait_for_prediction(
        "create_prediction", accepted, arguments={}, ctx=None, run_id="run-1",
        attempt=1, tool_call_id="tool-1", deadline=time.monotonic() - 1,
    ))

    assert result == {**accepted, "wait_timeout": True}
    assert reads == []


@pytest.mark.parametrize("record", [
    {"id": "other", "job_id": "job-1", "status": "SUCCEEDED", "result": {"value": 1}},
    {"id": "prediction-1", "job_id": "other", "status": "SUCCEEDED", "result": {"value": 1}},
    {"id": "prediction-1", "job_id": "job-1", "status": "SUCCEEDED", "result": None},
])
def test_wait_rejects_wrong_job_binding_or_success_without_result(record):
    runtime = _runtime({"get_prediction": lambda _ctx, _args: record})
    with pytest.raises(AssistantError) as exc:
        asyncio.run(runtime._wait_for_prediction(
            "create_prediction",
            {"id": "prediction-1", "job_id": "job-1", "status": "QUEUED"},
            arguments={}, ctx=None, run_id="run-1", attempt=1,
            tool_call_id="tool-1", deadline=time.monotonic() + 2,
        ))
    assert exc.value.payload.code == "PREDICTION_RESULT_INVALID"


def test_mutable_status_reads_bypass_cached_ledger_result():
    class PredictionService:
        reads = 0

        def validate(self, *_args, **_kwargs):
            return None

        def execute(self, *_args, **_kwargs):
            self.reads += 1
            status = "RUNNING" if self.reads == 1 else "SUCCEEDED"
            return {"id": "prediction-1", "status": status, "result": {"value": 1} if status == "SUCCEEDED" else None}

    service = PredictionService()
    tool = BoundTool(ToolSpec("get_prediction", "Read prediction"), service, ledger=ToolExecutionLedger())
    runtime = _runtime({"get_prediction": tool})
    first = runtime._execute_tool("get_prediction", {"prediction_id": "prediction-1"}, ctx=None, run_id="run-1")
    second = runtime._execute_tool("get_prediction", {"prediction_id": "prediction-1"}, ctx=None, run_id="run-1")

    assert first.result["status"] == "RUNNING"
    assert second.result["status"] == "SUCCEEDED"
    assert service.reads == 2


def test_prediction_model_view_keeps_values_units_warnings_and_provenance():
    view = _model_tool_result("get_prediction", {
        "id": "prediction-1", "job_id": "job-1", "status": "SUCCEEDED",
        "chromatography_type": "hplc", "source": "assistant", "data_origin": "production",
        "model": {"id": "model-1", "version": "3.2", "artifact_sha256": "large"},
        "warnings": [{"code": "LOW_SUPPORT", "message": "范围外", "field": "column_id"}],
        "input_snapshot": {"large": "omitted"},
        "result": {
            "task_type": "hplc", "metrics": [{"key": "selectivity", "value": 0.5155313611, "unit": "ratio", "origin": "model"}],
            "rows": [{"retention_time_min": 1.25}], "applicability": {"warnings": []},
            "visualizations": [{"data": {"large": "omitted"}}],
        },
    })

    assert view["source"] == "assistant" and view["data_origin"] == "production"
    assert view["model"] == {"id": "model-1", "version": "3.2", "artifact_sha256": "large"}
    assert view["warnings"][0]["code"] == "LOW_SUPPORT"
    assert view["result"]["metrics"][0] == {"key": "selectivity", "value": 0.5155313611, "unit": "ratio", "origin": "model"}
    assert "input_snapshot" not in view and "visualizations" not in view["result"]
    wire = _summary(view)
    assert '"value":0.5155313611' in wire and '"unit":"ratio"' in wire
