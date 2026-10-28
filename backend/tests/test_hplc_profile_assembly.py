"""Package boundaries for HPLC write 1.1 versus historical read 1.0."""
from pathlib import Path
import subprocess
import sys

from chrom_platform.assembly.profile import assemble_profile

ROOT = Path(__file__).resolve().parents[2]


def test_isolated_universal_hplc_read_and_write_boundaries(tmp_path: Path) -> None:
    package = assemble_profile(ROOT / "builds/universal-v1.json", tmp_path)
    script = '''
import sys
sys.path.insert(0, str(__import__('pathlib').Path.cwd()))
from pydantic import TypeAdapter, ValidationError
from chrom_platform.modules.prediction.schemas import PredictionCreate, PredictionStoredRead, PredictionInputSnapshot
from chrom_platform.modules.prediction import persistence, service, production_worker, worker
from chrom_platform.app import create_app
document = create_app('build-manifest.json', contract_generation=True).openapi()
snapshot = document['components']['schemas']['PredictionInputSnapshot']['properties']['input']
assert any(item.get('$ref','').endswith('/HplcInputV10') for item in snapshot['anyOf'])
payload = dict(project_id='p', chromatography_type='hplc', tool='single', schema_version='1.0',
    model_selection=dict(mode='exact', model_id='m', model_version='v'),
    structure_refs=['a','b'], input=dict(isopropanol_fraction=0.1,flow_rate_ml_min=1,column_ids=['c']))
assert TypeAdapter(PredictionStoredRead).validate_python(payload).schema_version == '1.0'
try:
    TypeAdapter(PredictionCreate).validate_python(payload)
except ValidationError:
    pass
else:
    raise AssertionError('legacy request entered create union')
PredictionInputSnapshot.model_validate(dict(structure_refs=['a','b'], normalized_structures=['C','N'],input=payload['input']))
payload['schema_version']='1.1'
payload['input']['model_input_smiles']=['C','N']
assert TypeAdapter(PredictionCreate).validate_python(payload).input.model_input_smiles == ['C','N']
'''
    result = subprocess.run([sys.executable, "-B", "-I", "-c", script], cwd=package, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_non_hplc_packages_remove_execution_and_legacy_dependencies(tmp_path: Path) -> None:
    for name in ("cc-v1", "sfc-v1"):
        package = assemble_profile(ROOT / f"builds/{name}.json", tmp_path)
        prediction = package / "chrom_platform/modules/prediction"
        for filename in ("schemas.py", "service.py", "persistence.py", "worker.py", "production_worker.py"):
            source = (prediction / filename).read_text(encoding="utf-8")
            assert "HplcPrediction" not in source, (name, filename)
            assert "validate_hplc_execution_binding" not in source, (name, filename)
        script = '''
import sys
sys.path.insert(0, str(__import__('pathlib').Path.cwd()))
from chrom_platform.modules.prediction import schemas, service, persistence, worker, production_worker
assert schemas.PredictionStoredRead is schemas.PredictionCreate
'''
        result = subprocess.run([sys.executable, "-B", "-I", "-c", script], cwd=package, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


def test_minimal_package_has_no_prediction_or_hplc(tmp_path: Path) -> None:
    package = assemble_profile(ROOT / "builds/minimal-structure-v1.json", tmp_path)
    assert not (package / "chrom_platform/adapters/hplc").exists()
    assert not (package / "chrom_platform/modules/prediction").exists()
    script = '''
import sys
sys.path.insert(0, str(__import__('pathlib').Path.cwd()))
from chrom_platform.app import create_app
document = create_app('build-manifest.json', contract_generation=True).openapi()
assert not any(name.startswith('Hplc') for name in document['components']['schemas'])
assert not any('/prediction' in path for path in document['paths'])
'''
    result = subprocess.run([sys.executable, "-B", "-I", "-c", script], cwd=package, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
