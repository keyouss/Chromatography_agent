from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.modules.assistant import adapter as adapter_module
from chrom_platform.modules.assistant.production_worker import AssistantLlmConfigurationTestWorker
from chrom_platform.modules.assistant.settings_schemas import LlmTestChecks
from chrom_platform.modules.assistant.settings_service import activate_configuration


APPROVED_ENDPOINT = "https://api.siliconflow.cn/v1"


@pytest.mark.parametrize("status", [401, 403])
def test_provider_auth_failure_skips_later_probes_without_calling_provider_again(monkeypatch, status):
    calls: list[str] = []

    class FakeAdapter:
        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, *, tools, **_kwargs):
            calls.append("connectivity" if not tools else "tool_calling")
            raise adapter_module.ProviderTransportError(
                "HTTP_4XX", http_status=status, message="SECRET_SENTINEL provider body",
            )

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        SimpleNamespace(
            id="cfg-auth", revision="rev-auth", url=APPROVED_ENDPOINT,
            model_id="model", key_reference="dpapi:v1:" + "a" * 32,
            settings={"external_send_policy": "approved_endpoint_only", "timeout_seconds": 2, "max_retries": 0},
        ),
        secret_resolver=lambda _reference: "not-used",
        approved_endpoints_resolver=lambda: APPROVED_ENDPOINT,
        job_id="job-auth", test_id="test-auth",
    ))

    assert calls == ["connectivity"]
    assert result == {
        "status": "FAILED",
        "checks": {
            "connectivity": "FAILED", "streaming": "SKIPPED",
            "tool_calling": "SKIPPED", "structured_output": "SKIPPED",
        },
        "error": {
            "code": "PROVIDER_AUTHENTICATION_FAILED",
            "message": "LLM Provider 认证或授权失败",
        },
    }
    assert LlmTestChecks.model_validate(result["checks"]).connectivity == "FAILED"
    assert "SECRET_SENTINEL" not in str(result)


def test_non_auth_provider_4xx_keeps_existing_followup_probe_behavior(monkeypatch):
    calls: list[str] = []

    class FakeAdapter:
        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, *, tools, on_event, validate_tool, execute_tool, **_kwargs):
            stage = "tool_calling" if tools else ("connectivity" if not calls else "streaming")
            calls.append(stage)
            if stage == "connectivity":
                raise adapter_module.ProviderTransportError("HTTP_4XX", http_status=429)
            if tools:
                args = validate_tool("assistant_test_echo", {"value": "chrom-platform-test"}, "echo-1")
                result = execute_tool("assistant_test_echo", args, "echo-1")
                return {"text": "echo checked", "tool_calls": 1, "tool_results": [result]}
            await on_event({"type": "text_delta", "delta": "streamed"})
            return {"text": "streamed"}

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        SimpleNamespace(
            id="cfg-rate-limit", revision="rev-rate-limit", url=APPROVED_ENDPOINT,
            model_id="model", key_reference="dpapi:v1:" + "b" * 32,
            settings={"external_send_policy": "approved_endpoint_only", "timeout_seconds": 2, "max_retries": 0},
        ),
        secret_resolver=lambda _reference: "not-used",
        approved_endpoints_resolver=lambda: APPROVED_ENDPOINT,
    ))

    assert calls == ["connectivity", "tool_calling", "streaming"]
    assert result["status"] == "FAILED"
    assert result["checks"] == {
        "connectivity": "FAILED", "streaming": "PASSED",
        "tool_calling": "PASSED", "structured_output": "NOT_SUPPORTED",
    }
    assert result["error"]["code"] == "PROVIDER_TEST_FAILED"


@pytest.mark.parametrize("prior_config_status", ["CANDIDATE", "TESTED"])
def test_auth_skip_finish_persists_failure_and_keeps_old_check_values_readable(prior_config_status):
    from chrom_platform.modules.assistant.production_worker import _LlmTestClaim

    class AsyncContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeSession:
        def __init__(self, rows):
            self.rows = list(rows)
            self.added = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return AsyncContext()

        async def scalar(self, _query):
            return self.rows.pop(0) if self.rows else None

        def add(self, value):
            self.added.append(value)

    config_hash = "c" * 64
    job = SimpleNamespace(
        id="job-auth", project_id="project-auth", owner_id="owner-auth",
        status="RUNNING", revision=2, etag="2", result_ref=None, deadline_at=None,
    )
    attempt = SimpleNamespace(
        status="RUNNING", execution_token="execution-auth", fencing_token=4,
        result_ref=None,
    )
    test = SimpleNamespace(
        id="test-auth", configuration_revision="revision-auth",
        configuration_hash=config_hash, status="RUNNING",
    )
    config = SimpleNamespace(
        id="config-auth", project_id="project-auth", revision="revision-auth",
        settings={"config_hash": config_hash}, status=prior_config_status, last_test_id=None,
    )
    session = FakeSession([job, attempt, test, config, None])
    claim = _LlmTestClaim(
        message=SimpleNamespace(job_id=job.id, attempt=1), test_id=test.id,
        config=SimpleNamespace(id=config.id), token="execution-auth", fence=4,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=90),
    )
    outcome = {
        "status": "FAILED",
        "checks": {
            "connectivity": "FAILED", "streaming": "SKIPPED",
            "tool_calling": "SKIPPED", "structured_output": "SKIPPED",
        },
        "error": {"code": "PROVIDER_AUTHENTICATION_FAILED", "message": "LLM Provider 认证或授权失败"},
    }

    finished = asyncio.run(AssistantLlmConfigurationTestWorker(
        session_factory=lambda: session,
    )._finish(claim, outcome))

    assert finished is True
    assert (job.status, attempt.status, test.status) == ("FAILED", "FAILED", "FAILED")
    assert test.checks == outcome["checks"]
    assert test.error == outcome["error"]
    assert job.error == attempt.error == outcome["error"]
    assert job.result_ref is None and attempt.result_ref is None
    assert config.status == "CANDIDATE"
    assert config.last_test_id == test.id
    assert LlmTestChecks.model_validate({
        "connectivity": "PASSED", "streaming": "PASSED",
        "tool_calling": "PASSED", "structured_output": "NOT_SUPPORTED",
    }).structured_output == "NOT_SUPPORTED"
    assert LlmTestChecks.model_validate(outcome["checks"]).structured_output == "SKIPPED"

    class ActivationSession:
        def __init__(self):
            self.results = [None, config]

        def scalar(self, _query):
            return self.results.pop(0)

    actor = ActorContext(
        actor_id="owner-auth", project_ids=["project-auth"], auth_method="session",
        permissions=["settings:write"], policy_version="test-v1", default_project_id="project-auth",
    )
    with pytest.raises(APIError) as rejected:
        activate_configuration(
            ActivationSession(), actor, config.id, test_id=test.id,
            expected_active_revision=0, if_match='"active-0"', request_id="request-auth",
        )
    assert (rejected.value.status_code, rejected.value.payload.code) == (409, "STATE_CONFLICT")


def test_execute_rejects_skipped_check_without_auth_failure_shape():
    from chrom_platform.modules.assistant.production_worker import AssistantLlmTestMessage, _LlmTestClaim

    claim = _LlmTestClaim(
        message=AssistantLlmTestMessage("job-forged-skip", 1, "dispatch-forged-skip"),
        test_id="test-forged-skip", config=SimpleNamespace(id="config-forged-skip"),
        token="token-forged-skip", fence=1,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=90),
    )
    normalized = {}
    worker = AssistantLlmConfigurationTestWorker(
        session_factory=lambda: None,
        test_runner=lambda **_kwargs: {
            "status": "SUCCEEDED",
            "checks": {
                "connectivity": "PASSED", "streaming": "PASSED",
                "tool_calling": "PASSED", "structured_output": "SKIPPED",
            },
            "error": None,
        },
    )

    async def claim_job(_message):
        return claim, "RUNNING"

    async def finish_job(_claim, outcome):
        normalized.update(outcome)
        return True

    worker._claim = claim_job
    worker._finish = finish_job
    result = asyncio.run(worker.execute({
        "job_id": claim.message.job_id, "attempt": 1,
        "dispatch_id": claim.message.dispatch_id,
    }))

    assert result.status.value == "FAILED"
    assert normalized == {
        "status": "FAILED",
        "checks": {
            "connectivity": "FAILED", "streaming": "FAILED",
            "tool_calling": "FAILED", "structured_output": "NOT_SUPPORTED",
        },
        "error": {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置能力测试未通过"},
    }
    assert "SKIPPED" not in str(normalized)

    class AsyncContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeSession:
        def __init__(self, rows):
            self.rows = list(rows)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return AsyncContext()

        async def scalar(self, _query):
            return self.rows.pop(0) if self.rows else None

        def add(self, _value):
            pass

    config_hash = "d" * 64
    job = SimpleNamespace(
        id=claim.message.job_id, project_id="project-forged-skip", owner_id="owner-forged-skip",
        status="RUNNING", revision=1, etag="1", result_ref=None, deadline_at=None,
    )
    attempt = SimpleNamespace(
        status="RUNNING", execution_token=claim.token, fencing_token=claim.fence,
        result_ref=None,
    )
    test = SimpleNamespace(
        id=claim.test_id, configuration_revision="revision-forged-skip",
        configuration_hash=config_hash, status="RUNNING",
    )
    config = SimpleNamespace(
        id=claim.config.id, project_id=job.project_id, revision="revision-forged-skip",
        settings={"config_hash": config_hash}, status="CANDIDATE", last_test_id=None,
    )
    persisted_session = FakeSession([job, attempt, test, config, None])
    direct_claim = _LlmTestClaim(
        message=claim.message, test_id=test.id, config=claim.config,
        token=claim.token, fence=claim.fence, deadline_at=claim.deadline_at,
    )
    finished = asyncio.run(AssistantLlmConfigurationTestWorker(
        session_factory=lambda: persisted_session,
    )._finish(direct_claim, {
        "status": "SUCCEEDED",
        "checks": {
            "connectivity": "PASSED", "streaming": "PASSED",
            "tool_calling": "PASSED", "structured_output": "SKIPPED",
        },
        "error": None,
    }))

    assert finished is True
    assert (job.status, attempt.status, test.status) == ("FAILED", "FAILED", "FAILED")
    assert test.checks == {
        "connectivity": "NOT_SUPPORTED", "streaming": "NOT_SUPPORTED",
        "tool_calling": "NOT_SUPPORTED", "structured_output": "NOT_SUPPORTED",
    }
    assert "SKIPPED" not in str(test.checks)
    assert config.status == "CANDIDATE"
