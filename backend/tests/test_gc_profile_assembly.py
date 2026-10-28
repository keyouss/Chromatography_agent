"""GC historical reads and profile-bound writes in asset-free packages."""
import json
from pathlib import Path
import subprocess
import sys

from chrom_platform.assembly.profile import assemble_profile

ROOT = Path(__file__).resolve().parents[2]


def test_gc_candidate_policy_is_copied_only_for_gc_packages(tmp_path: Path) -> None:
    profiles = {
        "gc": ROOT / "builds/universal-v1.json",
        "minimal": ROOT / "builds/minimal-structure-v1.json",
        "cc": ROOT / "builds/cc-v1.json",
        "sfc": ROOT / "builds/sfc-v1.json",
    }
    for name, source_manifest in profiles.items():
        raw = json.loads(source_manifest.read_text(encoding="utf-8"))
        raw["build_id"] = f"gc-policy-package-closure-{name}"
        if name == "gc":
            raw.update({
                "modules": ["prediction"],
                "chromatography_types": ["gc"],
                "prediction_tools": {"gc": ["single"]},
                "utilities": [],
            })
            raw.pop("assistant_features", None)
        manifest_path = tmp_path / f"{name}-build-manifest.json"
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")

        package = assemble_profile(manifest_path, tmp_path / f"{name}-package")
        helper = package / "chrom_platform/assembly/gc_candidate_policy.py"
        if name == "gc":
            source_helper = ROOT / "backend/src/chrom_platform/assembly/gc_candidate_policy.py"
            assert helper.is_file()
            assert helper.read_bytes() == source_helper.read_bytes()
        else:
            assert not helper.exists(), name


def test_isolated_gc_historical_read_and_profile_bound_write(tmp_path: Path) -> None:
    package = assemble_profile(ROOT / "builds/universal-v1.json", tmp_path)
    script = '''
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from pydantic import TypeAdapter, ValidationError
from chrom_platform.modules.prediction.schemas import PredictionCreate, PredictionStoredRead
from chrom_platform.modules.prediction import service, persistence, worker, production_worker
schema = TypeAdapter(PredictionCreate).json_schema()
selection_schema = schema['$defs']['GcExactProfileModelSelection']
for field in ('preprocessing_profile_id', 'preprocessing_profile_sha256'):
    assert field in selection_schema['required']
    assert selection_schema['properties'][field]['type'] == 'string'
payload = dict(project_id='p', chromatography_type='gc', tool='single', schema_version='1.0',
    model_selection=dict(mode='exact', model_id='m', model_version='v'), structure_refs=['a'],
    input=dict(temperature_program=dict(mode='series', temperature_series_c=[40.0]*30)))
assert TypeAdapter(PredictionStoredRead).validate_python(payload).schema_version == '1.0'
for candidate in (payload, dict(payload, schema_version='1.1')):
    try:
        TypeAdapter(PredictionCreate).validate_python(candidate)
    except ValidationError:
        pass
    else:
        raise AssertionError('legacy or profileless request accepted for execution')
payload['schema_version']='1.1'
payload['model_selection'].update(preprocessing_profile_id='profile', preprocessing_profile_sha256='a'*64)
assert TypeAdapter(PredictionCreate).validate_python(payload).schema_version == '1.1'
'''
    result = subprocess.run([sys.executable, "-B", "-I", "-c", script], cwd=package, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_non_gc_packages_remove_gc_execution_and_legacy_read(tmp_path: Path) -> None:
    for name in ("cc-v1", "sfc-v1"):
        package = assemble_profile(ROOT / f"builds/{name}.json", tmp_path)
        assert not (package / "chrom_platform/adapters/gc").exists()
        prediction = package / "chrom_platform/modules/prediction"
        for filename in ("schemas.py", "service.py", "persistence.py", "worker.py", "production_worker.py"):
            source = (prediction / filename).read_text(encoding="utf-8")
            assert "GcPrediction" not in source, (name, filename)
            assert "validate_gc_execution_binding" not in source, (name, filename)
        script = '''
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from chrom_platform.modules.prediction import schemas, service, persistence, worker, production_worker
assert schemas.PredictionStoredRead is schemas.PredictionCreate
'''
        result = subprocess.run([sys.executable, "-B", "-I", "-c", script], cwd=package, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
