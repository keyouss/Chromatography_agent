"""The API import closure must not assemble or deserialize scientific models."""
import os
from pathlib import Path
import subprocess
import sys


def test_api_and_assistant_composition_do_not_import_scientific_assembly_or_torch():
    source = Path(__file__).resolve().parents[1] / "src"
    environment = {**os.environ, "PYTHONPATH": str(source)}
    program = """
import sys
import chrom_platform.assembly.prediction_api
import chrom_platform.assembly.m4
import chrom_platform.app
assert 'chrom_platform.assembly.prediction' not in sys.modules
assert 'chrom_platform.assembly.prediction_worker_registry' not in sys.modules
assert 'chrom_platform.worker_cli' not in sys.modules
assert 'torch' not in sys.modules
assert 'torch_geometric' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", program], env=environment,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
