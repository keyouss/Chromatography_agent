import copy
import json
import unittest
from pathlib import Path

from manifest import TOOLS, validate, validate_file


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent
        self.full = json.loads((self.root / "universal-v1.json").read_text(encoding="utf-8"))

    def test_all_profiles_keep_structure(self):
        for name in ("universal-v1", "sfc-v1", "cc-v1", "minimal-structure-v1"):
            with self.subTest(profile=name):
                result = validate(json.loads((self.root / f"{name}.json").read_text(encoding="utf-8")))
                self.assertIn("structure_core", result["required_services"])

    def test_profiles_publish_only_current_prediction_tools(self):
        expected = {
            "universal-v1": {
                "tlc": ["single", "condition_compare", "reverse_solvent"],
                "cc": ["single"],
                "hplc": ["single"],
                "sfc": ["single_column", "full_column"],
                "gc": ["single"],
            },
            "sfc-v1": {"sfc": ["single_column", "full_column"]},
            "cc-v1": {"cc": ["single"]},
            "minimal-structure-v1": {},
        }
        self.assertTrue(all("batch" in tools for tools in TOOLS.values()))
        for name, prediction_tools in expected.items():
            with self.subTest(profile=name):
                manifest = json.loads((self.root / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["prediction_tools"], prediction_tools)
                self.assertTrue(all("batch" not in tools for tools in prediction_tools.values()))

    def test_reject_invalid_compositions(self):
        mutations = [
            lambda m: m["modules"].append("coding"),
            lambda m: m.update(build_id="../escape"),
            lambda m: m["modules"].remove("knowledge"),
            lambda m: m["modules"].remove("assistant"),
            lambda m: m["chromatography_types"].remove("tlc"),
            lambda m: m["prediction_tools"]["tlc"].remove("reverse_solvent"),
            lambda m: m["modules"].append("prediction"),
            lambda m: m.update(sandbox=True),
        ]
        for mutate in mutations:
            manifest = copy.deepcopy(self.full)
            mutate(manifest)
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                validate(manifest)

    def test_training_does_not_add_management_pages(self):
        self.full["modules"].remove("datasets")
        self.full["modules"].remove("model_center")
        result = validate(self.full)
        self.assertIn("data_version_service", result["required_services"])
        self.assertIn("model_registration_service", result["required_services"])
        self.assertNotIn("model_center", result["modules"])

    def test_structure_core_and_converter_are_implicit(self):
        manifest = json.loads((self.root / "minimal-structure-v1.json").read_text(encoding="utf-8"))
        manifest["modules"] = []
        manifest["utilities"] = []
        result = validate(manifest)
        self.assertIn("structure_core", result["required_services"])
        self.assertTrue(result["structure_converter_required"])

    def test_reverse_utility_without_prediction_is_narrow(self):
        manifest = {
            "manifest_version": "1.0", "build_id": "tlc-reverse-v1",
            "modules": ["utilities"], "chromatography_types": ["tlc"],
            "prediction_tools": {"tlc": ["reverse_solvent"]},
            "utilities": ["tlc_reverse_solvent"], "export_formats": [],
            "auth_provider": "deployment_default",
        }
        validate(manifest)
        manifest["chromatography_types"].append("sfc")
        manifest["prediction_tools"]["sfc"] = ["single_column"]
        with self.assertRaises(ValueError):
            validate(manifest)

    def test_cli_file_stem_matches_build_id(self):
        matching = self.root / "_manifest_stem_check.json"
        matching.write_text(json.dumps(self.full), encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                validate_file(matching)
        finally:
            matching.unlink()


if __name__ == "__main__":
    unittest.main()
