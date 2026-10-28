import json
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from assemble_offline_bundle import BundleBlocked, assemble  # noqa: E402
from verify_offline_bundle import verify  # noqa: E402


class OfflineBundleTests(unittest.TestCase):
    def _staged(self, root: Path, profile: str = "minimal-structure-v1") -> tuple[Path, Path, Path]:
        backend = root / "backend"
        frontend = root / "frontend"
        backend.mkdir()
        frontend.mkdir()
        (backend / "build-manifest.json").write_text(json.dumps({"build_id": profile, "chromatography_types": []}), encoding="utf-8")
        migration = backend / "alembic" / "versions" / "0001_m1_persistence.py"
        migration.parent.mkdir(parents=True)
        migration.write_text("# staged migration\n", encoding="utf-8")
        requirements = backend / "requirements-profile.txt"
        requirements.write_text("fastapi==1\n", encoding="utf-8")
        rows = []
        for path in (backend / "build-manifest.json", migration, requirements):
            rows.append({"path": path.relative_to(backend).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        (backend / "SOURCE_MANIFEST.json").write_text(json.dumps({"build_id": profile, "modules": [], "chromatography_types": [], "files": rows, "migrations": ["alembic/versions/0001_m1_persistence.py"], "workers": [], "dependencies": ["fastapi==1"]}), encoding="utf-8")
        (frontend / "ASSEMBLED_PROFILE.json").write_text(json.dumps({"build_id": profile}), encoding="utf-8")
        (frontend / "dist").mkdir(parents=True)
        (frontend / "dist" / "index.html").write_text("<!doctype html>", encoding="utf-8")
        lock = root / "image-lock.json"
        lock.write_text(json.dumps({service: f"example/{service}@sha256:{'a' * 64}" for service in ("api", "worker", "frontend", "mysql", "redis", "nginx")}), encoding="utf-8")
        return backend, frontend, lock

    def test_missing_model_assets_blocks_prediction_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend, frontend, lock = self._staged(root, "cc-v1")
            backend.joinpath("build-manifest.json").write_text(json.dumps({"build_id": "cc-v1", "chromatography_types": ["cc"]}), encoding="utf-8")
            with self.assertRaises(BundleBlocked) as error:
                assemble(profile="cc-v1", backend_package=backend, frontend_package=frontend, output_dir=root / "out", image_lock=lock, env_template=ROOT / "deploy" / "v1" / ".env.example")
            self.assertEqual(error.exception.blockers[0]["code"], "MODEL_ASSETS_MISSING")

    def test_minimal_bundle_hashes_and_verifies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend, frontend, lock = self._staged(root)
            output = root / "out"
            env = root / ".env.example"
            env.write_text((ROOT / "deploy" / "v1" / ".env.example").read_text(encoding="utf-8").replace("WORKER_COMMAND=", "WORKER_COMMAND=audited-worker-entrypoint"), encoding="utf-8")
            report = assemble(profile="minimal-structure-v1", backend_package=backend, frontend_package=frontend, output_dir=output, image_lock=lock, env_template=env)
            self.assertEqual(verify(output), [])
            archive = root / "chrom-platform-minimal-structure-v1.zip"
            self.assertEqual(report["zip_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_offline_bundle.py"), str(archive)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
