import importlib.util
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(r"D:\Jupyter\Chromatography_agent")
RUN = ROOT / "testing/runs/2026-10-28-data-import-audit03"
ISO = RUN / "audit03-successor-original-restore-01"


def load_startup_check():
    spec = importlib.util.spec_from_file_location("successor_startup_check", ISO / "startup-check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SuccessorOriginalStartPreparationTests(unittest.TestCase):
    def test_template_and_manifest_are_inactive_and_pinned(self):
        template = json.loads((ISO / "authorization.template.json").read_text(encoding="utf-8"))
        manifest = json.loads((ISO / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(template["status"], "TEMPLATE_NOT_ACTIVE")
        self.assertIsNone(template["binding_sha256"])
        self.assertEqual(manifest["status"], "PREPARED_INACTIVE_SINGLE_ASSISTANT_ORIGINAL_START")
        self.assertEqual(manifest["restore_boundary"]["data_config_baseline_sha256"], "e475ad2f9299bc659f295271ac7607f9bcc7295520edf004a67354cb2bac4c81")
        self.assertEqual(manifest["restore_boundary"]["data_source_pin_sha256"], "31675275a79398770cbcb8238b3ee09d4580cb953dc1f6c2e0ebf82edddf7886")
        for entry in manifest["files"]:
            digest = hashlib.sha256((ISO / entry["path"]).read_bytes()).hexdigest()
            self.assertEqual(digest, entry["sha256"])
        manifest_hash = hashlib.sha256((ISO / "manifest.json").read_bytes()).hexdigest()
        helper = RUN / "audit03-successor-config-restore.py"
        helper_hash = hashlib.sha256(helper.read_bytes()).hexdigest()
        self.assertEqual(manifest["helper_source"], {"path": helper.name, "sha256": helper_hash})
        wrapper = (RUN / "audit03-successor-original-start-once.ps1").read_text(encoding="utf-8")
        self.assertIn(manifest_hash, wrapper)
        self.assertIn(helper_hash, wrapper)

    def test_idle_outcome_keeps_unknown_distinct_from_idle(self):
        check = load_startup_check()
        selected = {"dataset_only": True}
        with patch.object(check, "preserved_unknown_exception", return_value=False):
            raw_idle, code, exception = check.idle_outcome(
                {"dataset_scan_completion_unknown": 1, "job": 0}, selected, {"job_attempt_active": 0}
            )
        self.assertFalse(raw_idle)
        self.assertEqual(code, 3)
        self.assertFalse(exception)
        with patch.object(check, "preserved_unknown_exception", return_value=True):
            raw_idle, code, exception = check.idle_outcome(
                {"dataset_scan_completion_unknown": 1, "job": 0}, selected, {"job_attempt_active": 0}
            )
        self.assertFalse(raw_idle)
        self.assertEqual(code, 0)
        self.assertTrue(exception)

    def test_unknown_exception_fails_closed_on_any_pending_or_lease(self):
        check = load_startup_check()
        counts = {"dataset_scan_completion_unknown": 1, "job": 1}
        self.assertFalse(check.preserved_unknown_exception(counts, {"job_attempt_active": 0}, "data-20261026"))
        counts["job"] = 0
        self.assertFalse(check.preserved_unknown_exception(counts, {"job_attempt_active": 1}, "data-20261026"))

    def test_ordinary_idle_requires_all_observed_leases_zero(self):
        check = load_startup_check()
        selected = {"dataset_only": False}
        raw_idle, code, exception = check.idle_outcome({"job": 0}, selected, {"job_attempt_active": 0})
        self.assertTrue(raw_idle)
        self.assertEqual(code, 0)
        self.assertFalse(exception)
        raw_idle, code, exception = check.idle_outcome({"job": 0}, selected, {"job_attempt_active": 1})
        self.assertFalse(raw_idle)
        self.assertEqual(code, 3)
        self.assertFalse(exception)

    def test_static_entry_is_start_only_and_single_use(self):
        wrapper = (RUN / "audit03-successor-original-start-once.ps1").read_text(encoding="utf-8")
        start = (ISO / "start-platform.ps1").read_text(encoding="utf-8")
        managed = (ISO / "run-local.ps1").read_text(encoding="utf-8")
        startup = (ISO / "startup-check.py").read_text(encoding="utf-8")
        self.assertEqual(wrapper.count("Start-Process -FilePath $pwsh"), 1)
        self.assertIn("'-NoProfile', '-File', $entry", wrapper)
        self.assertNotIn("'-NonInteractive'", wrapper)
        self.assertNotIn("Stop-Process", wrapper)
        self.assertIn("Assert-SuccessorWrapperParent", start)
        self.assertIn('Assert-SuccessorAssistantParent', managed)
        self.assertIn("$Action -in @('stop', 'restart')", managed)
        self.assertIn("$successorProfile -ceq 'data-20261026' -and $Action -cne 'status'", managed)
        self.assertIn("check_preserved_unknown_exception", startup)
        self.assertIn("job_attempt_active", startup)
        self.assertNotIn("POST_REBOOT_AUTH", startup)
        self.assertNotIn("POST_REBOOT_BOOT_PROOF", startup)
        preflight = wrapper.index("    Assert-HelperResult", wrapper.index("try {\n    $principal"))
        receipt = wrapper.index("Write-ExclusiveJson $receiptPath $r", preflight)
        authorization = wrapper.index("Write-ExclusiveJson $authPath", receipt)
        start_call = wrapper.index("Start-Process -FilePath $pwsh", authorization)
        self.assertLess(preflight, receipt)
        self.assertLess(receipt, authorization)
        self.assertLess(authorization, start_call)
        self.assertLess(start_call, wrapper.index("$null = $startChild.Handle", start_call))
        self.assertLess(wrapper.index("$null = $startChild.Handle", start_call), wrapper.index("$startChild.WaitForExit()", start_call))
        self.assertNotIn("$_.Exception.Message", wrapper)
        self.assertIn("-BindingSha256", start)
        self.assertIn("(?<hash>[a-f0-9]{64})", start)


if __name__ == "__main__":
    unittest.main()