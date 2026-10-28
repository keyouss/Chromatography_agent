from __future__ import annotations

import hashlib
import json
import ctypes
from pathlib import Path
import time

import pytest

import chrom_platform.artifact_core.dataset_csv_elevated_runner as elevated
import chrom_platform.artifact_core.scanner_defender as scanner_defender
from chrom_platform.artifact_core.scanner_defender import MAX_OUTPUT_BYTES, ProcessResult
from chrom_platform.artifact_core.storage import LocalArtifactStore


@pytest.fixture(autouse=True)
def _mock_caller_token(monkeypatch):
    monkeypatch.setattr(elevated, "_current_user_sid", lambda: "S-1-5-21-fixture")
    monkeypatch.setattr(elevated, "_current_token_elevated", lambda: False)
    monkeypatch.setattr(elevated, "_read_runtime_manifest_pin", lambda: "a" * 64)


def _binding(monkeypatch, tmp_path: Path) -> elevated.DatasetScanBinding:
    body = b"fixed synthetic bytes"
    digest = hashlib.sha256(body).hexdigest()
    runtime_root = tmp_path / "runtime"
    artifact_root = runtime_root / "artifacts"
    path = artifact_root / "objects" / "artifact-fixed" / "content"
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    stat = path.stat()
    monkeypatch.setattr(elevated, "ARTIFACT_ROOT", artifact_root)
    monkeypatch.setattr(elevated, "INPUT_SHA256", digest)
    monkeypatch.setattr(elevated, "INPUT_SIZE_BYTES", len(body))
    state = runtime_root / "state"
    state.mkdir()
    monkeypatch.setattr(elevated, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(elevated, "STATE_ROOT", state)
    monkeypatch.setattr(elevated, "LEDGER_PATH", state / "quota.used")
    monkeypatch.setattr(elevated, "REQUEST_PATH", state / "request.json")
    return elevated.DatasetScanBinding(
        path=path,
        identity=(stat.st_dev, stat.st_ino),
        project_id=elevated.PROJECT_ID,
        profile_id=elevated.PROFILE_ID,
        artifact_id="artifact-fixed",
        attempt_id="attempt-fixed",
        sha256=digest,
        size_bytes=len(body),
    )


def _argv(binding: elevated.DatasetScanBinding) -> list[str]:
    return [str(elevated.DEFENDER_EXE), "-Scan", "-ScanType", "3", "-File", str(binding.path), "-DisableRemediation"]


def _transport_result(request, request_sha256, output=b"bounded raw result"):
    return elevated.ElevatedProcessResult(
        process=ProcessResult(0, output),
        launched_pid=721,
        client_pid=721,
        elevated=True,
        image_path=str(elevated.PYTHON_EXE),
        creation_id=123456,
        user_sid=request["caller_sid"],
        request_sha256=request_sha256,
        nonce=request["nonce"],
    )


def test_runner_binds_claim_and_returns_only_authenticated_raw_output(monkeypatch, tmp_path):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        return _transport_result(kwargs["request"], kwargs["request_sha256"])

    runner = elevated.DatasetCsvElevatedRunner(source_manifest_sha256="a" * 64, transport=transport)
    with runner.bind(binding):
        result = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)

    assert result.returncode == 0
    assert result.output == b"bounded raw result"
    assert calls[0]["request"]["project_id"] == elevated.PROJECT_ID
    assert calls[0]["request"]["profile_id"] == elevated.PROFILE_ID
    assert calls[0]["request"]["artifact_id"] == binding.artifact_id
    assert calls[0]["request"]["attempt_id"] == binding.attempt_id
    assert calls[0]["request"]["input_sha256"] == binding.sha256
    assert calls[0]["request"]["identity"] == list(binding.identity)
    assert json.loads(elevated.REQUEST_PATH.read_text(encoding="utf-8"))["nonce"] == calls[0]["request"]["nonce"]
    ledger = json.loads(elevated.LEDGER_PATH.read_text(encoding="utf-8"))
    assert ledger["status"] == "COMPLETE"
    assert ledger["scan_completed"] is True


@pytest.mark.parametrize("change", ["project_id", "profile_id", "path", "identity", "sha256"])
def test_runner_rejects_mismatched_fixed_binding_before_uac(monkeypatch, tmp_path, change):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)
    calls = []
    runner = elevated.DatasetCsvElevatedRunner(
        source_manifest_sha256="a" * 64,
        transport=lambda **kwargs: calls.append(kwargs) or _transport_result(kwargs["request"], kwargs["request_sha256"]),
    )
    updates = {
        "project_id": "other-project",
        "profile_id": "other-profile",
        "path": tmp_path / "other.csv",
        "identity": (0, 0),
        "sha256": "0" * 64,
    }
    bad = elevated.DatasetScanBinding(**{**binding.__dict__, change: updates[change]})
    with runner.bind(bad):
        result = runner(_argv(bad), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)
    expected = {
        "project_id": "DATASET_SCAN_FIXED_BINDING_MISMATCH",
        "profile_id": "DATASET_SCAN_FIXED_BINDING_MISMATCH",
        "path": "DATASET_SCAN_TARGET_PATH_MISMATCH",
        "identity": "DATASET_SCAN_TARGET_IDENTITY_CHANGED",
        "sha256": "DATASET_SCAN_FIXED_BINDING_MISMATCH",
    }
    assert result.error_code == expected[change]
    assert calls == []
    assert not elevated.LEDGER_PATH.exists()


def _store_scan_binding(monkeypatch, tmp_path: Path, root_kind: str):
    body = b"same synthetic csv bytes"
    digest = hashlib.sha256(body).hexdigest()
    runtime_root = tmp_path / "runtime"
    artifact_root = runtime_root / "artifacts"
    artifact_root.mkdir(parents=True)
    (runtime_root / "state").mkdir()
    monkeypatch.setattr(elevated, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(elevated, "ARTIFACT_ROOT", artifact_root)
    monkeypatch.setattr(elevated, "STATE_ROOT", runtime_root / "state")
    monkeypatch.setattr(elevated, "INPUT_SHA256", digest)
    monkeypatch.setattr(elevated, "INPUT_SIZE_BYTES", len(body))

    configured_root = artifact_root
    if root_kind == "logical":
        logical_root = tmp_path / "msix-logical-local" / "artifacts"
        physical_root = artifact_root.resolve()
        original_resolve = Path.resolve

        def mapped_resolve(path, strict=False):
            try:
                relative = path.relative_to(logical_root)
            except ValueError:
                return original_resolve(path, strict=strict)
            return original_resolve(physical_root / relative, strict=strict)

        monkeypatch.setattr(Path, "resolve", mapped_resolve)
        configured_root = logical_root

    store = LocalArtifactStore(configured_root)
    assert store.configured
    assert store.put(
        "artifact-fixed", body, expected_size=len(body), expected_sha256=digest,
    )[0] == "objects/artifact-fixed/content"
    target = store.prepare_scan_target(
        "objects/artifact-fixed/content", expected_sha256=digest, expected_size=len(body),
    )
    binding = elevated.DatasetScanBinding(
        path=target.path,
        identity=target.identity,
        project_id=elevated.PROJECT_ID,
        profile_id=elevated.PROFILE_ID,
        artifact_id="artifact-fixed",
        attempt_id="attempt-fixed",
        sha256=target.sha256,
        size_bytes=target.size_bytes,
    )
    return binding, body, runtime_root, artifact_root


@pytest.mark.parametrize("root_kind", ["logical", "physical"])
def test_store_logical_and_physical_roots_bind_to_same_canonical_target(monkeypatch, tmp_path, root_kind):
    binding, _body, _runtime_root, _artifact_root = _store_scan_binding(monkeypatch, tmp_path, root_kind)

    path, identity = elevated._check_target(elevated._binding_payload(binding))

    assert path == binding.path
    assert identity == binding.identity


@pytest.mark.parametrize("outside_kind", ["same_content_sibling", "outside_runtime"])
def test_same_content_at_another_path_is_still_rejected(monkeypatch, tmp_path, outside_kind):
    binding, body, runtime_root, _artifact_root = _store_scan_binding(monkeypatch, tmp_path, "physical")
    if outside_kind == "same_content_sibling":
        alternate = runtime_root / "state" / "same-content.csv"
    else:
        alternate = tmp_path / "outside-runtime.csv"
    alternate.write_bytes(body)
    different_path = elevated.DatasetScanBinding(**{**binding.__dict__, "path": alternate})

    with pytest.raises(ValueError, match="TARGET_PATH_MISMATCH"):
        elevated._check_target(elevated._binding_payload(different_path))


def test_reparse_component_is_still_rejected_without_a_symlink_alias(monkeypatch, tmp_path):
    binding, _body, _runtime_root, _artifact_root = _store_scan_binding(monkeypatch, tmp_path, "physical")
    reparse_component = binding.path.parent
    original_lstat = Path.lstat

    def report_reparse(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == reparse_component:
            return type("ReparseStat", (), {
                "st_mode": info.st_mode,
                "st_file_attributes": 0x400,
            })()
        return info

    monkeypatch.setattr(Path, "lstat", report_reparse)
    with pytest.raises(ValueError, match="RUNTIME_REPARSE_REJECTED"):
        elevated._check_target(elevated._binding_payload(binding))


def _preflight_only_runner(monkeypatch, transport_calls, reserve_calls):
    runner = elevated.DatasetCsvElevatedRunner(
        source_manifest_sha256="a" * 64,
        transport=lambda **kwargs: transport_calls.append(kwargs),
    )
    monkeypatch.setattr(runner, "_reserve_once", lambda request: reserve_calls.append(request) or True)
    return runner


def test_preflight_source_pin_exception_is_fixed_and_pre_reserve(monkeypatch, tmp_path, caplog):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: (_ for _ in ()).throw(OSError("SECRET C:\\private")))
    monkeypatch.setattr(elevated, "_check_target", lambda _binding: pytest.fail("target check must not run"))
    transport_calls, reserve_calls = [], []
    runner = _preflight_only_runner(monkeypatch, transport_calls, reserve_calls)
    caplog.set_level("WARNING", logger=elevated.__name__)

    with runner.bind(binding):
        result = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)

    assert result.error_code == "SOURCE_MANIFEST_CHECK_FAILED"
    assert "stage=source_manifest reason=SOURCE_MANIFEST_CHECK_FAILED" in caplog.text
    assert "SECRET" not in caplog.text and "private" not in caplog.text and str(binding.path) not in caplog.text
    assert reserve_calls == [] and transport_calls == []
    assert not elevated.LEDGER_PATH.exists() and not elevated.REQUEST_PATH.exists()


def test_preflight_pin_value_mismatch_keeps_existing_code_and_stops(monkeypatch, tmp_path):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "b" * 64)
    monkeypatch.setattr(elevated, "_check_target", lambda _binding: pytest.fail("target check must not run"))
    transport_calls, reserve_calls = [], []
    runner = _preflight_only_runner(monkeypatch, transport_calls, reserve_calls)

    with runner.bind(binding):
        result = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)

    assert result.error_code == "SOURCE_MANIFEST_PIN_MISMATCH"
    assert reserve_calls == [] and transport_calls == []
    assert not elevated.LEDGER_PATH.exists() and not elevated.REQUEST_PATH.exists()


def test_preflight_target_reason_is_fixed_and_stops(monkeypatch, tmp_path, caplog):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)
    monkeypatch.setattr(elevated, "_check_target", lambda _binding: (_ for _ in ()).throw(ValueError("TARGET_IDENTITY_CHANGED")))
    transport_calls, reserve_calls = [], []
    runner = _preflight_only_runner(monkeypatch, transport_calls, reserve_calls)
    caplog.set_level("WARNING", logger=elevated.__name__)

    with runner.bind(binding):
        result = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)

    assert result.error_code == "DATASET_SCAN_TARGET_IDENTITY_CHANGED"
    assert "stage=target reason=TARGET_IDENTITY_CHANGED" in caplog.text
    assert reserve_calls == [] and transport_calls == []
    assert not elevated.LEDGER_PATH.exists() and not elevated.REQUEST_PATH.exists()


def test_preflight_unknown_target_exception_is_sanitized_and_stops(monkeypatch, tmp_path, caplog):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)
    monkeypatch.setattr(elevated, "_check_target", lambda _binding: (_ for _ in ()).throw(RuntimeError("SECRET C:\\private")))
    transport_calls, reserve_calls = [], []
    runner = _preflight_only_runner(monkeypatch, transport_calls, reserve_calls)
    caplog.set_level("WARNING", logger=elevated.__name__)

    with runner.bind(binding):
        result = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)

    assert result.error_code == "DATASET_SCAN_TARGET_INVALID"
    assert "stage=target reason=TARGET_INVALID" in caplog.text
    assert "SECRET" not in caplog.text and "private" not in caplog.text and str(binding.path) not in caplog.text
    assert reserve_calls == [] and transport_calls == []
    assert not elevated.LEDGER_PATH.exists() and not elevated.REQUEST_PATH.exists()


def test_preflight_token_query_exception_is_distinct_and_stops(monkeypatch, tmp_path, caplog):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)
    monkeypatch.setattr(elevated, "_check_target", lambda _binding: None)
    monkeypatch.setattr(elevated, "_current_token_elevated", lambda: (_ for _ in ()).throw(OSError("SECRET")))
    transport_calls, reserve_calls = [], []
    runner = _preflight_only_runner(monkeypatch, transport_calls, reserve_calls)
    caplog.set_level("WARNING", logger=elevated.__name__)

    with runner.bind(binding):
        result = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)

    assert result.error_code == "CALLER_TOKEN_ELEVATION_QUERY_FAILED"
    assert "stage=caller_token_elevation reason=CALLER_TOKEN_ELEVATION_QUERY_FAILED" in caplog.text
    assert "SECRET" not in caplog.text
    assert reserve_calls == [] and transport_calls == []
    assert not elevated.LEDGER_PATH.exists() and not elevated.REQUEST_PATH.exists()


def test_preflight_elevated_boolean_preserves_existing_rejection(monkeypatch, tmp_path, caplog):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)
    monkeypatch.setattr(elevated, "_check_target", lambda _binding: None)
    monkeypatch.setattr(elevated, "_current_token_elevated", lambda: True)
    transport_calls, reserve_calls = [], []
    runner = _preflight_only_runner(monkeypatch, transport_calls, reserve_calls)
    caplog.set_level("WARNING", logger=elevated.__name__)

    with runner.bind(binding):
        result = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)

    assert result.error_code == "CALLER_MUST_BE_UNELEVATED"
    assert "stage=caller_token_elevation reason=CALLER_IS_ELEVATED" in caplog.text
    assert reserve_calls == [] and transport_calls == []


def test_preflight_logging_failure_does_not_change_rejection(monkeypatch, tmp_path):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)
    monkeypatch.setattr(elevated, "_check_target", lambda _binding: (_ for _ in ()).throw(ValueError("TARGET_IDENTITY_CHANGED")))

    class BrokenLogger:
        def warning(self, *_args, **_kwargs):
            raise RuntimeError("logging unavailable")

    monkeypatch.setattr(
        elevated,
        "logging",
        type("BrokenLogging", (), {"getLogger": staticmethod(lambda _name: BrokenLogger())})(),
    )
    transport_calls, reserve_calls = [], []
    runner = _preflight_only_runner(monkeypatch, transport_calls, reserve_calls)

    with runner.bind(binding):
        result = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)

    assert result.error_code == "DATASET_SCAN_TARGET_IDENTITY_CHANGED"
    assert reserve_calls == [] and transport_calls == []


def test_uac_rejection_consumes_quota_and_never_retries_automatically(monkeypatch, tmp_path):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)
    calls = []

    def rejected(**_kwargs):
        calls.append(1)
        raise PermissionError("cancelled")

    runner = elevated.DatasetCsvElevatedRunner(source_manifest_sha256="a" * 64, transport=rejected)
    with runner.bind(binding):
        first = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)
        second = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)
    assert first.error_code == "UAC_CANCELLED"
    assert second.error_code == "DATASET_SCAN_QUOTA_USED"
    assert calls == [1]
    ledger = json.loads(elevated.LEDGER_PATH.read_text(encoding="utf-8"))
    assert ledger["schema"] == "dataset-csv-scan-ledger-v1"
    assert ledger["status"] == "STARTED"


def test_transport_timeout_keeps_public_timeout_code_and_started_ledger(monkeypatch, tmp_path):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)

    def timeout(**_kwargs):
        raise TimeoutError("UAC_WAIT_TIMEOUT")

    runner = elevated.DatasetCsvElevatedRunner(source_manifest_sha256="a" * 64, transport=timeout)
    with runner.bind(binding):
        result = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)

    assert result.timed_out is True
    assert result.error_code == elevated.ERROR_TIMEOUT
    assert json.loads(elevated.LEDGER_PATH.read_text(encoding="utf-8"))["status"] == "STARTED"


def test_native_timeout_reasons_are_fixed_to_last_observed_phase():
    assert elevated._native_timeout_reason("UAC_WAIT", "UAC_WAIT_TIMEOUT") == "UAC_WAIT_TIMEOUT"
    assert elevated._native_timeout_reason("PIPE_CONNECT", "PIPE_CONNECT_TIMEOUT") == "PIPE_CONNECT_TIMEOUT"
    assert elevated._native_timeout_reason("PIPE_READ_HEADER", "PIPE_READ_TIMEOUT") == "PIPE_READ_TIMEOUT"
    assert elevated._native_timeout_reason("PIPE_READ_BODY", "PIPE_READ_TIMEOUT") == "PIPE_READ_TIMEOUT"
    assert elevated._native_timeout_reason("HELPER_EXIT", "HELPER_EXIT_UNKNOWN") == "HELPER_EXIT_TIMEOUT"
    assert elevated._native_timeout_reason("PIPE_CONNECT", "HELPER_EXIT_UNKNOWN") == "NATIVE_TRANSPORT_FAILED"


def test_native_diagnostic_is_timestampped_correlated_and_redacted(caplog):
    caplog.set_level("WARNING", logger=elevated.__name__)
    elevated._log_native_observation(
        "PIPE_READ_HEADER", "PIPE_READ_TIMEOUT", "STARTED",
        launched_pid=721, creation_id=123456, image_matches=True,
        peer_authenticated=True, request_sha256="a" * 64,
    )
    assert "stage=PIPE_READ_HEADER reason=PIPE_READ_TIMEOUT launch_state=STARTED" in caplog.text
    assert "request_sha256_prefix=aaaaaaaaaaaa" in caplog.text
    assert "at_utc=" in caplog.text
    assert "nonce" not in caplog.text and "pipe_name" not in caplog.text
    assert "SID" not in caplog.text and "raw output" not in caplog.text


def test_launch_identity_is_observed_before_pipe_and_never_claims_authentication(monkeypatch):
    class Api:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)

    class Kernel:
        GetProcessId = Api(lambda _handle: 721)
        QueryFullProcessImageNameW = Api(
            lambda _handle, _flags, buffer, _size: setattr(buffer, "value", str(elevated.PYTHON_EXE)) or 1,
        )

    monkeypatch.setattr(elevated, "_process_creation_id", lambda _kernel, _handle: 123456)
    observations = []
    monkeypatch.setattr(elevated, "_log_native_observation", lambda *args, **kwargs: observations.append((args, kwargs)))

    assert elevated._observe_launch_handle(Kernel(), "fixture-handle") == (721, 123456, True)
    assert observations == [(("HELPER_IDENTITY", "HELPER_IDENTITY_CAPTURED", "STARTED"), {
        "launched_pid": 721, "creation_id": 123456, "image_matches": True, "request_sha256": "",
    })]
    assert observations[0][1].get("peer_authenticated", False) is False


def test_late_handle_is_logged_before_close_even_if_logging_fails(monkeypatch):
    calls = []

    class Api:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)

    class Kernel:
        GetProcessId = Api(lambda _handle: 721)
        QueryFullProcessImageNameW = Api(
            lambda _handle, _flags, buffer, _size: setattr(buffer, "value", str(elevated.PYTHON_EXE)) or 1,
        )

    monkeypatch.setattr(elevated, "_process_creation_id", lambda _kernel, _handle: 123456)

    def broken_log(*args, **kwargs):
        calls.append(("log", args, kwargs))
        raise RuntimeError("secret raw exception")

    monkeypatch.setattr(elevated, "_log_native_observation", broken_log)
    identity = elevated._observe_launch_handle(
        Kernel(), "fixture-handle", late=True,
        close=lambda handle: calls.append(("close", handle)),
    )

    assert identity == (721, 123456, True)
    assert calls[0][0] == "log" and calls[0][1][:3] == ("SHELLEXECUTE", "SHELLEXECUTE_LATE_HANDLE", "STARTED")
    assert calls[1] == ("close", "fixture-handle")


def test_run_win32_pipe_fake_transport_logs_identity_before_timeout_and_closes_handles(monkeypatch):
    class Api:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)

    class Kernel:
        def __init__(self):
            self.closed = []
            self.CloseHandle = Api(lambda handle: self.closed.append(handle) or 1)
            self.GetCurrentProcess = Api(lambda: 1)
            self.LocalFree = Api(lambda _value: 0)
            self.CreateNamedPipeW = Api(lambda *_args: 100)
            self.ConnectNamedPipe = Api(lambda *_args: 0)
            self.WaitForSingleObject = Api(lambda *_args: 0x102)
            self.GetNamedPipeClientProcessId = Api(lambda *_args: 0)
            self.GetExitCodeProcess = Api(lambda *_args: 0)
            self.GetProcessId = Api(lambda _handle: 200)
            self.QueryFullProcessImageNameW = Api(
                lambda _handle, _flags, buffer, _size: setattr(buffer, "value", str(elevated.PYTHON_EXE)) or 1,
            )

    class Advapi:
        def __init__(self):
            self.OpenProcessToken = Api(self.open_process_token)
            self.GetTokenInformation = Api(self.get_token_information)
            self.ConvertSidToStringSidW = Api(self.convert_sid)
            self.ConvertStringSecurityDescriptorToSecurityDescriptorW = Api(self.convert_sddl)

        @staticmethod
        def open_process_token(_process, _access, output):
            ctypes.cast(output, ctypes.POINTER(elevated.wintypes.HANDLE)).contents.value = 55
            return 1

        @staticmethod
        def get_token_information(_token, _kind, _buffer, _size, returned):
            ctypes.cast(returned, ctypes.POINTER(elevated.wintypes.DWORD)).contents.value = ctypes.sizeof(ctypes.c_void_p)
            return 1

        @staticmethod
        def convert_sid(_sid, output):
            ctypes.cast(output, ctypes.POINTER(elevated.wintypes.LPWSTR)).contents = ctypes.c_wchar_p("S-1-5-21-fixture")
            return 1

        @staticmethod
        def convert_sddl(_sddl, _revision, output, _size):
            ctypes.cast(output, ctypes.POINTER(elevated.wintypes.LPVOID)).contents.value = 4
            return 1

    kernel = Kernel()
    advapi = Advapi()
    monkeypatch.setattr(elevated.ctypes, "WinDLL", lambda name, **_kwargs: {
        "kernel32": kernel, "advapi32": advapi,
        "shell32": object(), "ole32": object(),
    }[name])

    def shell_execute(_ole32, _shell32, info, _info_type):
        info.hProcess = 200
        return True, 0

    monkeypatch.setattr(elevated, "_shell_execute_sta", shell_execute)
    monkeypatch.setattr(elevated, "_process_creation_id", lambda _kernel, _handle: 987654)
    monkeypatch.setattr(
        elevated, "_connect_named_pipe_until_connected",
        lambda *_args: (_ for _ in ()).throw(TimeoutError("PIPE_CONNECT_TIMEOUT")),
    )
    observations = []
    monkeypatch.setattr(
        elevated, "_log_native_observation",
        lambda *args, **kwargs: observations.append((args, kwargs)),
    )
    request_hash = "a" * 64
    request = {
        "deadline_monotonic": time.monotonic() + 10,
        "pipe_name": r"\\.\pipe\ChromPlatform.DatasetCsvScan.fixture",
        "caller_sid": "S-1-5-21-fixture", "parent_pid": 99,
    }

    with pytest.raises(TimeoutError, match="PIPE_CONNECT_TIMEOUT"):
        elevated._run_win32_pipe(request, request_hash, timeout=10)

    assert observations[0][0][:3] == ("HELPER_IDENTITY", "HELPER_IDENTITY_CAPTURED", "STARTED")
    assert observations[0][1]["launched_pid"] == 200
    assert observations[0][1]["creation_id"] == 987654
    assert observations[0][1].get("peer_authenticated", False) is False
    assert observations[-1][0][:3] == ("PIPE_CONNECT", "PIPE_CONNECT_TIMEOUT", "STARTED")
    assert observations[-1][1]["request_sha256"] == request_hash
    assert kernel.closed[-2:] == [100, 200]


@pytest.mark.parametrize(
    "changes",
    [
        {"timed_out": True}, {"reader_incomplete": True},
        {"output_truncated": True}, {"error_code": "SCAN_UNKNOWN"}, {"returncode": None},
    ],
)
def test_uncertain_or_malformed_completion_never_marks_ledger_idle(monkeypatch, tmp_path, changes):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)
    def transport(**kwargs):
        result = _transport_result(kwargs["request"], kwargs["request_sha256"])
        process = ProcessResult(**{**result.process.__dict__, **changes})
        return elevated.ElevatedProcessResult(**{**result.__dict__, "process": process})
    runner = elevated.DatasetCsvElevatedRunner(source_manifest_sha256="a" * 64, transport=transport)
    with runner.bind(binding):
        runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)
    ledger = json.loads(elevated.LEDGER_PATH.read_text(encoding="utf-8"))
    assert ledger["status"] == "STARTED"


@pytest.mark.parametrize(
    "field,value",
    [("client_pid", 722), ("elevated", False), ("user_sid", "S-1-5-21-other"), ("creation_id", 0)],
)
def test_runner_rejects_untrusted_pipe_peer(monkeypatch, tmp_path, field, value):
    binding = _binding(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)

    def transport(**kwargs):
        result = _transport_result(kwargs["request"], kwargs["request_sha256"])
        return elevated.ElevatedProcessResult(**{**result.__dict__, field: value})

    runner = elevated.DatasetCsvElevatedRunner(source_manifest_sha256="a" * 64, transport=transport)
    with runner.bind(binding):
        result = runner(_argv(binding), timeout=120, max_output_bytes=MAX_OUTPUT_BYTES)
    assert result.reader_incomplete is True
    assert result.error_code == "ELEVATED_SCAN_PEER_UNTRUSTED"


def test_elevated_bootstrap_is_fixed_physical_package_and_has_no_module_args():
    args = elevated._fixed_elevation_arguments()
    assert args[:3] == ["-B", "-I", "-c"]
    assert elevated.PACKAGE_ROOT == Path(elevated.__file__).resolve().parents[2]
    assert "testing" not in str(elevated.PACKAGE_ROOT).lower()
    assert str(elevated.PACKAGE_ROOT).replace("\\", "\\\\") in args[3]
    assert elevated.HELPER_MODULE in args[3]
    assert "runpy.run_module" in args[3]
    assert "sys.argv" not in args[3]


def test_audit02_paths_are_fixed_and_share_the_physical_state_root():
    assert elevated.PACKAGE_ROOT == Path(elevated.__file__).resolve().parents[2]
    assert elevated.REQUEST_PATH == elevated.STATE_ROOT / "dataset-csv-scan-v1-audit02-20261027.request.json"
    assert elevated.LEDGER_PATH == elevated.STATE_ROOT / "dataset-csv-scan-v1-audit02-20261027.used"
    assert elevated.REQUEST_PATH.parent == elevated.LEDGER_PATH.parent == elevated.STATE_ROOT


def test_static_nonce_fixture_is_bound_into_the_ledger_request_hash():
    request = {
        "schema": "dataset-csv-elevated-scan-v1",
        "nonce": "a" * 32,
        "source_manifest_sha256": "b" * 64,
        "build_id": elevated.BUILD_ID,
        "project_id": elevated.PROJECT_ID,
        "profile_id": elevated.PROFILE_ID,
        "artifact_id": "artifact-static-fixture",
        "attempt_id": "attempt-static-fixture",
        "input_sha256": elevated.INPUT_SHA256,
        "size_bytes": elevated.INPUT_SIZE_BYTES,
    }
    request_bytes = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ledger = {
        "schema": "dataset-csv-scan-ledger-v1",
        "status": "STARTED",
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        **{key: request[key] for key in (
            "build_id", "project_id", "profile_id", "artifact_id", "attempt_id", "input_sha256", "size_bytes",
        )},
        "source_manifest_sha256": request["source_manifest_sha256"],
        "started_at_utc": "2026-10-27T00:00:00+00:00",
    }
    changed_nonce = {**request, "nonce": "c" * 32}
    changed_bytes = json.dumps(changed_nonce, sort_keys=True, separators=(",", ":")).encode("utf-8")

    assert set(ledger) == elevated._LEDGER_STARTED_FIELDS
    assert ledger["request_sha256"] == hashlib.sha256(request_bytes).hexdigest()
    assert ledger["request_sha256"] != hashlib.sha256(changed_bytes).hexdigest()
    assert all(ledger[field] == request[field] for field in (
        "build_id", "project_id", "profile_id", "artifact_id", "attempt_id", "input_sha256", "size_bytes",
    ))


def test_shell_execute_uses_noasync_and_balances_sta_for_s_ok_and_s_false():
    class Api:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)
    class Info(ctypes.Structure):
        _fields_ = [("value", ctypes.c_int)]
    for hresult in (0, 1):
        calls = []
        ole32 = type("Ole", (), {
            "CoInitializeEx": Api(lambda *_args: hresult),
            "CoUninitialize": Api(lambda: calls.append("uninitialize")),
        })()
        shell32 = type("Shell", (), {
            "ShellExecuteExW": Api(lambda _info: calls.append("execute") or True),
        })()
        ok, _error = elevated._shell_execute_sta(ole32, shell32, Info(), Info)
        assert ok is True
        assert calls == ["execute", "uninitialize"]
    assert elevated._fixed_shell_execute_mask() == 0x00000040 | 0x00000100


def test_shell_execute_sta_failure_is_fail_closed_without_uninitialize():
    class Api:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)
    class Info(ctypes.Structure):
        _fields_ = [("value", ctypes.c_int)]
    calls = []
    ole32 = type("Ole", (), {
        "CoInitializeEx": Api(lambda *_args: -2147417850),  # RPC_E_CHANGED_MODE
        "CoUninitialize": Api(lambda: calls.append("uninitialize")),
    })()
    shell32 = type("Shell", (), {
        "ShellExecuteExW": Api(lambda _info: calls.append("execute") or True),
    })()
    with pytest.raises(OSError, match="UAC_STA_INITIALIZATION_FAILED"):
        elevated._shell_execute_sta(ole32, shell32, Info(), Info)
    assert calls == []


def test_helper_checks_expired_deadline_before_starting_defender(monkeypatch):
    called = []
    now = scanner_defender.DefenderStatus(
        product_version=elevated.DEFENDER_PRODUCT_VERSION,
        engine_version=elevated.DEFENDER_ENGINE_VERSION,
        signature_version="fixture-signature",
        signature_updated_at_utc=None,
        antivirus_enabled=True,
        authenticode_status="Valid",
        signer_subject=elevated.DEFENDER_SIGNER,
        exe_sha256=elevated.DEFENDER_EXE_SHA256,
    )
    monkeypatch.setattr(elevated, "_source_manifest_pin", lambda: "a" * 64)
    monkeypatch.setattr(elevated, "_current_user_sid", lambda: "S-1-5-21-fixture")
    monkeypatch.setattr(elevated, "_current_token_elevated", lambda: True)
    monkeypatch.setattr(elevated, "_check_target", lambda _request: (Path("C:/fixed.csv"), (1, 2)))
    monkeypatch.setattr(elevated, "_lock_target_windows", lambda *_args: None)
    monkeypatch.setattr(scanner_defender, "_read_status", lambda: now)
    monkeypatch.setattr(scanner_defender, "_valid_status", lambda _status: True)
    monkeypatch.setattr(scanner_defender, "_sha256_file", lambda _path: elevated.DEFENDER_EXE_SHA256)
    monkeypatch.setattr(elevated, "_digest", lambda _path: (elevated.INPUT_SIZE_BYTES, elevated.INPUT_SHA256))
    monkeypatch.setattr(elevated, "_file_identity", lambda _path: (1, 2))
    monkeypatch.setattr(elevated, "_run_bounded_process", lambda *_a, **_k: called.append(1))
    request = {
        "schema": "dataset-csv-elevated-scan-v1",
        "caller_sid": "S-1-5-21-fixture",
        "parent_pid": 91,
        "source_manifest_sha256": "a" * 64,
        "deadline_utc": time.time() - 1,
        "deadline_monotonic": time.monotonic() - 1,
        "profile_id": elevated.PROFILE_ID,
        "attempt_id": "attempt-fixture",
        "artifact_id": "artifact-fixture",
    }
    with pytest.raises(TimeoutError, match="SCAN_DEADLINE_EXPIRED"):
        elevated._serve_request(
            request,
            "b" * 64,
            timeout=120,
            connect=lambda _request: ("fake-pipe", 91),
            send=lambda *_args: 0,
            close=lambda _handle: None,
        )
    assert called == []


def test_helper_rejects_unexpected_pipe_server_before_validation_or_scan(monkeypatch):
    calls = []
    monkeypatch.setattr(elevated, "_helper_result", lambda *_args: calls.append("scan") or {})
    with pytest.raises(ValueError, match="PIPE_SERVER_PID_MISMATCH"):
        elevated._serve_request(
            {"parent_pid": 91}, "a" * 64, timeout=10,
            connect=lambda _request: ("fake-pipe", 92),
            send=lambda *_args: 0,
            close=lambda handle: calls.append(("close", handle)),
        )
    assert calls == [("close", "fake-pipe")]


def test_helper_pipe_connect_failure_never_reaches_scan(monkeypatch):
    calls = []
    monkeypatch.setattr(elevated, "_helper_result", lambda *_args: calls.append("scan") or {})
    with pytest.raises(OSError, match="PIPE_CONNECT_FAILED"):
        elevated._serve_request(
            {"parent_pid": 91}, "a" * 64, timeout=10,
            connect=lambda _request: (_ for _ in ()).throw(OSError("PIPE_CONNECT_FAILED")),
            send=lambda *_args: 0,
            close=lambda _handle: None,
        )
    assert calls == []


def test_pipe_nowait_connect_polls_error_536_and_accepts_race_535():
    class Kernel:
        attempts = 0
        def ConnectNamedPipe(self, _pipe, overlapped):
            assert overlapped is None
            self.attempts += 1
            return False
        def WaitForSingleObject(self, _process, _timeout):
            return 0x102
    kernel = Kernel()
    errors = iter([536, 535])
    elevated._connect_named_pipe_until_connected(
        kernel, "pipe", "process", time.monotonic() + 2,
        get_error=lambda: next(errors), sleep=lambda _delay: None,
    )
    assert kernel.attempts == 2


def test_pipe_nowait_read_retries_error_232_without_pending_buffer():
    class Api:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)
    class Kernel:
        def __init__(self):
            self.peeks = 0
            self.reads = 0
            self.PeekNamedPipe = Api(self.peek)
            self.ReadFile = Api(self.read)
        def peek(self, _pipe, _buffer, _size, _read, available, _total):
            self.peeks += 1
            ctypes.cast(available, ctypes.POINTER(elevated.wintypes.DWORD)).contents.value = 3
            return True
        def read(self, _pipe, buffer, count, read, overlapped):
            assert overlapped is None
            self.reads += 1
            if self.reads == 1:
                return False
            assert count == 3
            ctypes.memmove(buffer, b"abc", 3)
            ctypes.cast(read, ctypes.POINTER(elevated.wintypes.DWORD)).contents.value = 3
            return True
    kernel = Kernel()
    errors = iter([232])
    assert elevated._read_pipe_exact(
        kernel, "pipe", 3, time.monotonic() + 2,
        get_error=lambda: next(errors), sleep=lambda _delay: None,
    ) == b"abc"
    assert kernel.reads == 2


def test_pipe_wire_rejects_missing_or_mistyped_state_fields():
    expected = {
        "schema": "dataset-csv-elevated-result-v1", "nonce": "n", "request_sha256": "a" * 64,
        "project_id": elevated.PROJECT_ID, "profile_id": elevated.PROFILE_ID,
        "artifact_id": "artifact-fixed", "attempt_id": "attempt-fixed",
        "input_sha256": elevated.INPUT_SHA256, "size_bytes": elevated.INPUT_SIZE_BYTES,
        "identity": [1, 2],
    }
    valid = {
        **expected, "returncode": 2, "output_b64": "eA==", "timed_out": False,
        "output_truncated": False, "reader_incomplete": False, "error_code": None,
    }
    assert elevated._decode_helper_result_body(json.dumps(valid).encode(), expected).returncode == 2
    for invalid in (
        {key: value for key, value in valid.items() if key != "timed_out"},
        {**valid, "timed_out": 0},
        {**valid, "returncode": True},
        {**valid, "error_code": 5},
    ):
        with pytest.raises(OSError):
            elevated._decode_helper_result_body(json.dumps(invalid).encode(), expected)


def test_runtime_source_pin_comes_only_from_fixed_runtime_json(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "runtime.json").write_text(
        json.dumps({"CHROM_BACKEND_SOURCE_MANIFEST_SHA256": "a" * 64}), encoding="utf-8",
    )
    monkeypatch.setattr(elevated, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(elevated, "_reject_reparse_path", lambda _path: None)
    monkeypatch.setenv("CHROM_BACKEND_SOURCE_MANIFEST_SHA256", "b" * 64)
    assert elevated._read_runtime_manifest_pin() == "a" * 64


@pytest.mark.parametrize(
    "ledger,expected",
    [
        (None, True),
        ({"schema": "dataset-csv-scan-ledger-v1", "status": "STARTED"}, False),
        ({"schema": "dataset-csv-scan-ledger-v1", "status": "UNKNOWN"}, False),
        ({
            "schema": "dataset-csv-scan-ledger-v1", "status": "COMPLETE",
            "scan_completed": True,
            "request_sha256": "a" * 64, "helper_pid": 21, "helper_creation_id": 99,
            "scan_returncode": 0, "started_at_utc": "2026-10-26T00:00:00+00:00",
            "completed_at_utc": "2026-10-26T00:00:01+00:00",
            "build_id": elevated.BUILD_ID, "project_id": elevated.PROJECT_ID,
            "profile_id": elevated.PROFILE_ID, "artifact_id": "artifact-fixed",
            "attempt_id": "attempt-fixed", "input_sha256": elevated.INPUT_SHA256,
            "size_bytes": elevated.INPUT_SIZE_BYTES, "source_manifest_sha256": "b" * 64,
        }, True),
    ],
)
def test_ledger_idle_requires_complete_authenticated_process_record(monkeypatch, tmp_path, ledger, expected):
    marker = tmp_path / "scan-ledger.json"
    monkeypatch.setattr(elevated, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(elevated, "_read_runtime_manifest_pin", lambda: "b" * 64)
    if ledger is not None:
        marker.write_text(json.dumps(ledger), encoding="utf-8")
    assert elevated.ledger_processes_idle(marker) is expected


def test_ledger_idle_rejects_wrong_scope_and_incomplete_native_completion(monkeypatch, tmp_path):
    monkeypatch.setattr(elevated, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(elevated, "_read_runtime_manifest_pin", lambda: "b" * 64)
    valid = {
        "schema": "dataset-csv-scan-ledger-v1", "status": "COMPLETE", "scan_completed": True,
        "request_sha256": "a" * 64, "helper_pid": 21, "helper_creation_id": 99,
        "scan_returncode": 2, "started_at_utc": "2026-10-26T00:00:00+00:00",
        "completed_at_utc": "2026-10-26T00:00:01+00:00",
        "build_id": elevated.BUILD_ID, "project_id": elevated.PROJECT_ID,
        "profile_id": elevated.PROFILE_ID, "artifact_id": "artifact-fixed",
        "attempt_id": "attempt-fixed", "input_sha256": elevated.INPUT_SHA256,
        "size_bytes": elevated.INPUT_SIZE_BYTES, "source_manifest_sha256": "b" * 64,
    }
    marker = tmp_path / "scan-ledger.json"
    for key, value in (("project_id", "other-project"), ("size_bytes", elevated.INPUT_SIZE_BYTES - 1),
                       ("scan_completed", False), ("helper_pid", True)):
        invalid = {**valid, key: value}
        marker.write_text(json.dumps(invalid), encoding="utf-8")
        assert elevated.ledger_processes_idle(marker) is False


def test_ledger_idle_rejects_reparse_marker_even_when_target_is_missing(monkeypatch, tmp_path):
    marker = tmp_path / "scan-ledger.json"
    monkeypatch.setattr(elevated, "RUNTIME_ROOT", tmp_path)
    original_lstat = Path.lstat
    monkeypatch.setattr(
        Path, "lstat",
        lambda path: type("Stat", (), {"st_mode": 0, "st_file_attributes": 0x400})() if path == marker else original_lstat(path),
    )
    assert elevated.ledger_processes_idle(marker) is False

