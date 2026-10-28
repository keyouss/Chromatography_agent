"""Synthetic, asset-free tests for the narrowly allowlisted GC assembly gate."""

from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

_POLICY_PATH = Path(__file__).parents[1] / "src" / "chrom_platform" / "assembly" / "gc_candidate_policy.py"
EXPECTED_QA_AUDIT_ANCHOR = "ddbe8c981842c64fcc5781e4b1235e96733f7420a057954c7b5cf0fd817a4760"
_SPEC = importlib.util.spec_from_file_location("_gc_candidate_policy_test_module", _POLICY_PATH)
assert _SPEC is not None and _SPEC.loader is not None
policy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = policy
_SPEC.loader.exec_module(policy)


def _synthetic_receipt_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture_bytes = b"synthetic fixture bytes; not a production verification fixture"
    fixture_path = tmp_path / "synthetic-fixture.json"
    fixture_path.write_bytes(fixture_bytes)
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()

    configured = replace(
        policy._POLICY,
        fixture_sha256=fixture_sha256,
        profile_sha256="0" * 64,
    )
    descriptor = {
        "profile_id": configured.profile_id,
        "model_id": configured.model_id,
        "model_version": configured.model_version,
        "artifact_sha256": configured.artifact_sha256,
        "platform_input_schema_version": configured.platform_input_schema_version,
        "author_input_schema_version": configured.author_input_schema_version,
        "output_schema_version": configured.output_schema_version,
        "input_schema_id": configured.input_schema_id,
        "input_schema_sha256": "1" * 64,
        "output_schema_id": configured.output_schema_id,
        "output_schema_sha256": "2" * 64,
        "reference_fixture_id": configured.fixture_id,
        "reference_fixture_sha256": fixture_sha256,
        "preprocessing_protocol_id": "gc-preprocessing-rdkit-seed42-v1",
        "preprocessing": {
            "adapter_source_sha256": configured.adapter_source_sha256,
            "runtime_source_sha256": configured.runtime_source_sha256,
            "environment_reference": dict(policy._REFERENCE_ENVIRONMENT),
        },
    }
    configured = replace(configured, profile_sha256=policy._canonical_sha256(descriptor))
    monkeypatch.setattr(policy, "_POLICY", configured)

    environment = dict(policy._REFERENCE_ENVIRONMENT)
    snapshots = ({"predictions": [{"rt_q10_min": 1.0}]}, {"predictions": [{"rt_q10_min": 1.0}]})
    output_sha256s = tuple(policy._canonical_sha256(item) for item in snapshots)
    runtime = SimpleNamespace(
        _verified_handle=None,
        _runtime_nonce="runtime-instance-nonce",
        _review_generation=7,
        _verification_predict_attempts=2,
        _verification_predict_successes=2,
        _verification_forward_attempts=2,
        _verification_forward_successes=2,
    )
    values = {
        "runtime_nonce": runtime._runtime_nonce,
        "generation": runtime._review_generation,
        "profile_id": configured.profile_id,
        "profile_sha256": configured.profile_sha256,
        "model_id": configured.model_id,
        "model_version": configured.model_version,
        "artifact_sha256": configured.artifact_sha256,
        "author_input_schema_version": configured.author_input_schema_version,
        "platform_input_schema_version": configured.platform_input_schema_version,
        "input_schema_id": configured.input_schema_id,
        "input_schema_sha256": descriptor["input_schema_sha256"],
        "output_schema_id": configured.output_schema_id,
        "output_schema_sha256": descriptor["output_schema_sha256"],
        "fixture_id": configured.fixture_id,
        "fixture_sha256": fixture_sha256,
        "environment_sha256": configured.environment_sha256,
        "asset_inventory_sha256": configured.asset_inventory_sha256,
        "adapter_source_sha256": configured.adapter_source_sha256,
        "runtime_source_sha256": configured.runtime_source_sha256,
        "verification_predict_attempts": 2,
        "verification_predict_successes": 2,
        "verification_forward_attempts": 2,
        "verification_forward_successes": 2,
        "output_sha256s": output_sha256s,
        "receipt_sha256": "",
    }
    receipt = SimpleNamespace(**values)
    payload = {key: value for key, value in vars(receipt).items() if key != "receipt_sha256"}
    receipt.receipt_sha256 = policy._canonical_sha256(payload)
    runtime._profile_receipt = receipt
    adapter = SimpleNamespace(runtime=runtime, verified_handle=None)
    return {
        "adapter": adapter,
        "runtime": runtime,
        "receipt": receipt,
        "descriptor": descriptor,
        "environment": environment,
        "asset_sha256": configured.asset_inventory_sha256,
        "adapter_source_sha256": configured.adapter_source_sha256,
        "runtime_source_sha256": configured.runtime_source_sha256,
        "fixture_path": fixture_path,
        "snapshots": snapshots,
    }


def _matches(context: dict[str, object], *, qa_flag: object = False) -> bool:
    return policy._matches_gc_review_policy(
        adapter=context["adapter"],
        runtime=context["runtime"],
        receipt=context["receipt"],
        descriptor=context["descriptor"],
        current_environment=context["environment"],
        current_asset_inventory_sha256=context["asset_sha256"],
        current_adapter_source_sha256=context["adapter_source_sha256"],
        current_runtime_source_sha256=context["runtime_source_sha256"],
        fixture_path=context["fixture_path"],
        verification_output_snapshots=context["snapshots"],
        global_qa_approved=qa_flag,
    )


def test_synthetic_complete_receipt_facts_match_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _synthetic_receipt_context(tmp_path, monkeypatch)

    assert _matches(context)


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("profile_id", "gc-future-profile"),
        ("platform_input_schema_version", "1.0"),
        ("output_schema_id", "UnexpectedResult"),
        ("adapter_source_sha256", "f" * 64),
        ("runtime_source_sha256", "e" * 64),
        ("asset_inventory_sha256", "d" * 64),
        ("environment_sha256", "c" * 64),
        ("fixture_sha256", "b" * 64),
        ("generation", 8),
    ],
)
def test_synthetic_receipt_mismatch_stays_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, wrong: object,
) -> None:
    context = _synthetic_receipt_context(tmp_path, monkeypatch)
    setattr(context["receipt"], field, wrong)

    assert not _matches(context)


def test_global_qa_flag_true_does_not_use_reviewed_profile_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _synthetic_receipt_context(tmp_path, monkeypatch)

    assert not _matches(context, qa_flag=True)


def test_changed_fixture_bytes_stay_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _synthetic_receipt_context(tmp_path, monkeypatch)
    context["fixture_path"].write_bytes(b"changed bytes")

    assert not _matches(context)


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("environment", {"python": "3.11.x"}),
        ("asset_sha256", "d" * 64),
        ("adapter_source_sha256", "f" * 64),
        ("runtime_source_sha256", "e" * 64),
        ("snapshots", ({"one": 1}, {"one": 2})),
    ],
)
def test_live_binding_mismatch_stays_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, wrong: object,
) -> None:
    context = _synthetic_receipt_context(tmp_path, monkeypatch)
    context[field] = wrong

    assert not _matches(context)


def test_receipt_identity_must_be_runtime_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _synthetic_receipt_context(tmp_path, monkeypatch)
    context["runtime"]._profile_receipt = SimpleNamespace(**vars(context["receipt"]))

    assert not _matches(context)


def test_unconfigured_adapter_returns_without_runtime_or_assets() -> None:
    adapter = SimpleNamespace(runtime=None)

    assert policy.maybe_build_reviewed_gc_candidate(None) is None
    assert policy.maybe_build_reviewed_gc_candidate(adapter) is adapter


def test_reviewed_candidate_uses_runtime_approval_and_registers_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This value is approval metadata only; runtime-local receipt identity and
    # the typed one-use approval authorize candidate construction.
    assert policy._POLICY.qa_review_sha256_audit_anchor == EXPECTED_QA_AUDIT_ANCHOR
    context = _synthetic_receipt_context(tmp_path, monkeypatch)

    class FakeReceipt:
        pass

    class FakeApproval:
        def __init__(self, runtime: object, receipt: object, audit_sha256: str) -> None:
            self.runtime = runtime
            self.receipt = receipt
            self.qa_review_sha256 = audit_sha256

    class FakeRuntime:
        def __init__(self, receipt: FakeReceipt) -> None:
            self._profile_receipt = receipt
            self._verified_handle = None
            self._runtime_nonce = context["runtime"]._runtime_nonce
            self._review_generation = context["runtime"]._review_generation
            self._verification_predict_attempts = 2
            self._verification_predict_successes = 2
            self._verification_forward_attempts = 2
            self._verification_forward_successes = 2
            self._bound_model_id = None
            self._bound_model_version = None
            self._bound_artifact_sha256 = None
            self._rechecked = False
            self._approval = None
            self._consumed = False

        @property
        def verification_output_snapshots(self):
            return context["snapshots"]

        @property
        def _verified_fixture_path(self):
            return context["fixture_path"]

        def _platform_source_hashes(self):
            return context["adapter_source_sha256"], context["runtime_source_sha256"]

        def _current_environment(self):
            return dict(context["environment"])

        def _verified_asset_inventory_sha256(self):
            return context["asset_sha256"]

        def _recheck_consumed_receipt(self, receipt: FakeReceipt) -> None:
            assert receipt is self._profile_receipt
            self._rechecked = True

        def _issue_profile_review_approval(self, receipt: FakeReceipt, audit_sha256: str) -> FakeApproval:
            assert self._rechecked
            assert receipt is self._profile_receipt
            self._approval = FakeApproval(self, receipt, audit_sha256)
            return self._approval

        def _consume_profile_review_approval(self, approval: FakeApproval) -> FakeReceipt:
            assert approval is self._approval
            assert approval.runtime is self
            assert approval.receipt is self._profile_receipt
            assert not self._consumed
            self._profile_receipt = None
            self._consumed = True
            return approval.receipt

    class FakeAdapter:
        def __init__(self, runtime: FakeRuntime, *, verified_handle: object = None) -> None:
            self.runtime = runtime
            self.verified_handle = verified_handle
            self.structure_resolver = {"synthetic-ref": "C"}

    typed_receipt = FakeReceipt()
    vars(typed_receipt).update(vars(context["receipt"]))
    runtime = FakeRuntime(typed_receipt)
    runtime._profile_receipt = typed_receipt
    adapter = FakeAdapter(runtime)

    handle = SimpleNamespace(
        adapter_id="gc",
        business_scope_id=policy._POLICY.business_scope_id,
        model_id=policy._POLICY.model_id,
        model_version=policy._POLICY.model_version,
        artifact_sha256=policy._POLICY.artifact_sha256,
        input_schema_version=policy._POLICY.platform_input_schema_version,
        output_schema_version=policy._POLICY.output_schema_version,
        preprocessing_profile_id=policy._POLICY.profile_id,
        preprocessing_profile_sha256=policy._POLICY.profile_sha256,
    )

    class FakeCandidate(FakeAdapter):
        def __init__(self, runtime: FakeRuntime, verified_handle: object) -> None:
            super().__init__(runtime, verified_handle=verified_handle)

    candidate = FakeCandidate(runtime, handle)
    builder_calls: list[dict[str, object]] = []

    def fake_candidate_builder(*, runtime: FakeRuntime, structure_resolver: object, approval: FakeApproval):
        assert runtime is candidate.runtime
        assert approval is runtime._approval
        assert approval.runtime is runtime
        assert approval.receipt is typed_receipt
        assert approval.qa_review_sha256 == EXPECTED_QA_AUDIT_ANCHOR
        assert structure_resolver is adapter.structure_resolver
        # The real candidate builder consumes this exact approval before it
        # constructs evidence and asks the central issuer for a handle.
        runtime._consume_profile_review_approval(approval)
        runtime._verified_handle = handle
        builder_calls.append({"runtime": runtime, "approval": approval})
        return candidate

    fake_adapter_module = SimpleNamespace(
        GcPredictionAdapter=FakeAdapter,
        build_gc_profile_candidate_adapter=fake_candidate_builder,
    )
    fake_manifest_module = SimpleNamespace(GC_PREPROCESSING_PROTOCOL_QA_APPROVED=False)
    fake_runtime_module = SimpleNamespace(GcRuntime=FakeRuntime, GcProfileVerificationReceipt=FakeReceipt)
    fake_profile_module = SimpleNamespace(gc_serving_profile_descriptor=lambda: context["descriptor"])
    package_names = (
        "chrom_platform",
        "chrom_platform.adapters",
        "chrom_platform.adapters.gc",
    )
    for name in package_names:
        package = SimpleNamespace(__path__=[])
        monkeypatch.setitem(sys.modules, name, package)
    for name, module in (
        ("chrom_platform.adapters.gc.adapter", fake_adapter_module),
        ("chrom_platform.adapters.gc.manifest", fake_manifest_module),
        ("chrom_platform.adapters.gc.runtime", fake_runtime_module),
        ("chrom_platform.adapters.gc.serving_profile", fake_profile_module),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    returned = policy.maybe_build_reviewed_gc_candidate(adapter)

    assert returned is candidate
    assert len(builder_calls) == 1
    assert runtime._approval is builder_calls[0]["approval"]
    assert runtime._profile_receipt is None
    assert runtime._consumed
    assert runtime._verified_handle is handle
    for field in (
        "adapter_id",
        "business_scope_id",
        "model_id",
        "model_version",
        "artifact_sha256",
        "input_schema_version",
        "output_schema_version",
        "preprocessing_profile_id",
        "preprocessing_profile_sha256",
    ):
        assert getattr(candidate.verified_handle, field) == getattr(handle, field)

    class FakeRegistry:
        def __init__(self) -> None:
            self.adapter = None

        def register(self, value: FakeCandidate) -> None:
            self.adapter = value

        def health(self) -> str:
            return "READY" if self.adapter is not None and self.adapter.verified_handle is handle else "UNAVAILABLE"

    registry = FakeRegistry()
    registry.register(returned)
    assert registry.health() == "READY"
