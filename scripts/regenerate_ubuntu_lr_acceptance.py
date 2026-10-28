"""Run the new package acceptance suite and regenerate expected/comparison files."""
from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "deliverables" / "chromatography_tools_cc_hplc_ubuntu_lr_v1"
SUITE = PACKAGE / "acceptance_examples"
ACTUAL = SUITE / "actual_outputs"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def refresh_hashes() -> None:
    for model in ("cc_column_chromatography", "hplc_enantioseparation"):
        entries = []
        for path in sorted((PACKAGE / model).rglob("*")):
            if path.is_file() and path.name != "MODEL_SHA256.txt":
                entries.append(f"{sha256(path)}  {path.relative_to(PACKAGE).as_posix()}")
        (PACKAGE / model / "MODEL_SHA256.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")
    entries = []
    for path in sorted(PACKAGE.rglob("*")):
        if path.is_file() and path.name != "MANIFEST_SHA256.txt":
            entries.append(f"{sha256(path)}  {path.relative_to(PACKAGE).as_posix()}")
    (PACKAGE / "MANIFEST_SHA256.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")


def comparison(case: dict, actual: dict, reference: dict) -> dict:
    result = {"case_id": case["case_id"], "model": case["model"], "note": "误差用于描述该回归样例，不作为独立泛化性能结论或单例通过门槛。", "comparisons": []}
    if case["model"] == "cc":
        windows = {row["name"]: row for row in actual["predicted_elution_windows"]}
        measured = reference.get("measured_volume_ml", {})
        for name in ("V1", "V2"):
            predicted = windows[name]["volume_ml"]
            target = float(measured[name])
            result["comparisons"].append({"target": f"{name}_volume_ml", "predicted_q50": predicted["q50"], "measured": target, "absolute_error": abs(predicted["q50"] - target), "relative_error": abs(predicted["q50"] - target) / max(abs(target), 1e-12), "inside_q10_q90": predicted["q10"] <= target <= predicted["q90"], "measured_time_seconds": reference.get("measured_time_seconds", {}).get(name), "predicted_time_seconds_q50": windows[name]["time_seconds"]["q50"]})
    else:
        measured = reference.get("measured_rt_min", [])
        for row, target in zip(actual.get("columns", [])[0].get("compounds", []), measured):
            median = row["rt_q50_min"]
            result["comparisons"].append({"column": actual.get("columns", [])[0].get("column"), "smiles": row["smiles"], "predicted_q50_min": median, "measured_rt_min": target, "absolute_error_min": abs(median - float(target)), "relative_error": abs(median - float(target)) / max(abs(float(target)), 1e-12), "inside_q10_q90": row["rt_q10_min"] <= float(target) <= row["rt_q90_min"]})
    return result


def main() -> None:
    if not PACKAGE.is_dir():
        raise FileNotFoundError(PACKAGE)
    subprocess.run([sys.executable, str(SUITE / "run_acceptance.py"), "--package-root", str(PACKAGE), "--output-dir", str(ACTUAL)], check=True)
    catalog = json.loads((SUITE / "case_catalog.json").read_text(encoding="utf-8"))
    for case in catalog["cases"]:
        case_dir = SUITE / Path(case["input"]).parent
        actual_path = ACTUAL / case["model"] / f"{case['case_id']}.json"
        if case["kind"] == "success":
            actual = json.loads(actual_path.read_text(encoding="utf-8"))
            (case_dir / "expected_output.json").write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            reference = json.loads((case_dir / "reference.json").read_text(encoding="utf-8"))
            (case_dir / "comparison.json").write_text(json.dumps(comparison(case, actual, reference), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    subprocess.run([sys.executable, str(SUITE / "verify_outputs.py"), "--actual-dir", str(ACTUAL)], check=True)
    shutil.rmtree(ACTUAL)
    refresh_hashes()
    print(json.dumps({"success": True, "actual_dir": str(ACTUAL)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
