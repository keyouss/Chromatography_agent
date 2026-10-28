"""A changed review fixture must fail before environment or model inspection."""
import hashlib
from types import SimpleNamespace

import pytest

from chrom_platform.adapters.gc.runtime import GcAssetUnavailable, GcRuntime


@pytest.mark.parametrize("state", ["changed", "missing", "unbound"])
def test_gc_consumption_rechecks_actual_fixture_before_inspection(tmp_path, state):
    fixture = tmp_path / "fixture.json"
    original = b'{"synthetic_unit_fixture":true}'
    fixture.write_bytes(original)
    receipt = SimpleNamespace(fixture_sha256=hashlib.sha256(original).hexdigest())
    runtime = object.__new__(GcRuntime)
    runtime._verified_fixture_path = fixture if state != "unbound" else None
    runtime._inspect = lambda: pytest.fail("stale fixture must fail before inspection")
    if state == "changed":
        fixture.write_bytes(b'{"synthetic_unit_fixture":false}')
    elif state == "missing":
        fixture.unlink()
    with pytest.raises(GcAssetUnavailable, match="GC_PROFILE_REVIEW_FIXTURE_STALE"):
        runtime._recheck_consumed_receipt(receipt)
