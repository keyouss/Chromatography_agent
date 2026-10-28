from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from rdkit import Chem


REPO = Path(__file__).resolve().parents[1]
DELIVERABLES = REPO / "deliverables"
PACKAGE = DELIVERABLES / "chromatography_tools_noncommercial_with_examples_v2"
OUTPUT = DELIVERABLES / "chromatography_tools_acceptance_examples_v2"

MODEL_DIRS = {
    "hplc": "hplc_enantioseparation",
    "gc": "gc_retention_time",
    "tlc": "tlc_rf",
    "cc": "cc_column_chromatography",
}

HPLC_COLUMNS = (
    "ADH", "ODH", "IC", "IA", "OJH", "ASH", "IC3", "IE", "ID", "OD3",
    "IB", "AD", "AD3", "IF", "OD", "AS", "OJ3", "IG", "AZ", "IAH",
    "OJ", "ICH", "OZ3", "IF3", "IAU",
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def canonical_without_stereo(smiles: str) -> str | None:
    mol = Chem.MolFromSmiles(str(smiles))
    if mol is None:
        return None
    return Chem.MolToSmiles(mol, isomericSmiles=False)


def case_dir(model: str, case_id: str) -> Path:
    return OUTPUT / "cases" / model / case_id


def add_case(catalog: list[dict[str, Any]], model: str, case_id: str,
             payload: dict[str, Any], reference: dict[str, Any], kind: str = "success") -> None:
    directory = case_dir(model, case_id)
    write_json(directory / "input.json", payload)
    write_json(directory / "reference.json", reference)
    catalog.append({
        "model": model,
        "case_id": case_id,
        "kind": kind,
        "input": f"cases/{model}/{case_id}/input.json",
        "expected_output": f"cases/{model}/{case_id}/expected_output.json",
        "reference": f"cases/{model}/{case_id}/reference.json",
        "comparison": f"cases/{model}/{case_id}/comparison.json" if kind == "success" else None,
    })


def select_hplc(catalog: list[dict[str, Any]]) -> None:
    source_rel = "hplc_enantioseparation/dataset/All_column_charity.csv"
    frame = pd.read_csv(PACKAGE / source_rel)
    frame["source_row"] = np.arange(len(frame)) + 2
    frame = frame.dropna(subset=["SMILES", "RT", "Speed", "i-PrOH_proportion", "Column"]).copy()
    frame["base"] = frame["SMILES"].map(canonical_without_stereo)
    frame = frame.dropna(subset=["base"])

    for column in HPLC_COLUMNS:
        subset = frame[frame["Column"].astype(str).eq(column)]
        candidates: list[dict[str, Any]] = []
        keys = ["base", "Speed", "i-PrOH_proportion"]
        for (base, speed, ipa), group in subset.groupby(keys, sort=True):
            unique = group.sort_values("source_row").drop_duplicates("SMILES")
            if len(unique) < 2:
                continue
            first, second = unique.iloc[0], unique.iloc[1]
            candidates.append({
                "base": base,
                "speed": float(speed),
                "ipa": float(ipa),
                "rows": [first, second],
                "mean_rt": (float(first["RT"]) + float(second["RT"])) / 2.0,
            })
        if not candidates:
            raise RuntimeError(f"No HPLC stereoisomer pair found for {column}")
        median_rt = float(np.median([item["mean_rt"] for item in candidates]))
        selected = min(
            candidates,
            key=lambda item: (
                abs(item["mean_rt"] - median_rt),
                int(item["rows"][0]["source_row"]),
            ),
        )
        rows = selected["rows"]
        case_id = f"hplc_{column.lower()}"
        payload = {
            "smiles": [str(row["SMILES"]) for row in rows],
            "ipa_fraction": selected["ipa"],
            "flow_rate_ml_min": selected["speed"],
            "columns": [column],
            "device": "cpu",
        }
        reference = {
            "purpose": "数据集可追溯的功能/回归样例，不是独立泛化测试。",
            "selection_rule": "该柱全部可配对立体异构体中，选择平均实测 RT 最接近中位数者。",
            "source_file": source_rel,
            "source_rows": [int(row["source_row"]) for row in rows],
            "column": column,
            "literature": [str(row["Literature"]) for row in rows],
            "measured_rt_min": [float(row["RT"]) for row in rows],
            "comparison_scope": "比较两个 RT、分位区间和洗脱顺序；无实测 Sp，分离概率只展示。",
        }
        add_case(catalog, "hplc", case_id, payload, reference)


def choose_distinct_rows(frame: pd.DataFrame, value_column: str, targets: list[float],
                         unique_columns: list[str]) -> list[pd.Series]:
    values = frame[value_column].astype(float)
    target_values = [float(values.quantile(q)) for q in targets]
    selected: list[pd.Series] = []
    used: dict[str, set[str]] = {name: set() for name in unique_columns}
    for target in target_values:
        ordered = frame.assign(_distance=(values - target).abs()).sort_values(
            ["_distance", "source_row"], kind="stable"
        )
        choice = None
        for _, row in ordered.iterrows():
            if all(str(row[name]) not in used[name] for name in unique_columns):
                choice = row
                break
        if choice is None:
            for _, row in ordered.iterrows():
                if int(row["source_row"]) not in {int(x["source_row"]) for x in selected}:
                    choice = row
                    break
        if choice is None:
            raise RuntimeError(f"Unable to select row near {target} for {value_column}")
        selected.append(choice)
        for name in unique_columns:
            used[name].add(str(choice[name]))
    return selected


def select_gc(catalog: list[dict[str, Any]]) -> None:
    source_rel = "gc_retention_time/Data/Clean_database.csv"
    frame = pd.read_csv(PACKAGE / source_rel)
    frame["source_row"] = np.arange(len(frame)) + 2
    numeric = ["Peak_time", "Initial_T", "Final_T", "Heating_rate", "Ret_time"]
    for name in numeric:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna(subset=["Smiles", "Method_index", *numeric]).copy()
    frame = frame[
        (frame["Final_T"] >= frame["Initial_T"])
        & (frame["Heating_rate"] > 0)
        & (frame["Ret_time"] >= 0)
        & np.isclose(frame["Ret_time"], np.round(frame["Ret_time"]))
    ]
    frame = frame[frame["Smiles"].map(lambda value: Chem.MolFromSmiles(str(value)) is not None)].copy()
    frame["rt_band"] = pd.qcut(frame["Peak_time"].rank(method="first"), 4, labels=False)

    selected_rows: list[tuple[int, pd.Series]] = []
    used_smiles: set[str] = set()
    used_methods: set[str] = set()
    for band in range(4):
        group = frame[frame["rt_band"].eq(band)].copy()
        for q in (0.2, 0.5, 0.8):
            target = float(group["Peak_time"].quantile(q))
            ordered = group.assign(_distance=(group["Peak_time"] - target).abs()).sort_values(
                ["_distance", "source_row"], kind="stable"
            )
            choice = None
            for _, row in ordered.iterrows():
                if str(row["Smiles"]) not in used_smiles and str(row["Method_index"]) not in used_methods:
                    choice = row
                    break
            if choice is None:
                for _, row in ordered.iterrows():
                    if int(row["source_row"]) not in {int(x[1]["source_row"]) for x in selected_rows}:
                        choice = row
                        break
            if choice is None:
                raise RuntimeError(f"Unable to select GC row for band {band}")
            selected_rows.append((band, choice))
            used_smiles.add(str(choice["Smiles"]))
            used_methods.add(str(choice["Method_index"]))

    for index, (band, row) in enumerate(selected_rows, 1):
        case_id = f"gc_q{band + 1}_{(index - 1) % 3 + 1:02d}"
        payload = {
            "smiles": [str(row["Smiles"])],
            "initial_temperature_c": float(row["Initial_T"]),
            "final_temperature_c": float(row["Final_T"]),
            "heating_rate_c_per_min": float(row["Heating_rate"]),
            "initial_hold_min": int(round(float(row["Ret_time"]))),
            "device": "cpu",
        }
        reference = {
            "purpose": "数据集可追溯的功能/回归样例，不是独立泛化测试。",
            "selection_rule": f"实测 RT 四分位分层 Q{band + 1} 中按 20%/50%/80% 位置选择，并优先保持分子和方法编号不同。",
            "source_file": source_rel,
            "source_rows": [int(row["source_row"])],
            "molecule_index": int(row["Mole_index"]),
            "method_index": int(row["Method_index"]),
            "name": None if pd.isna(row.get("Name")) else str(row.get("Name")),
            "cas": None if pd.isna(row.get("CAS")) else str(row.get("CAS")),
            "measured_rt_min": float(row["Peak_time"]),
        }
        add_case(catalog, "gc", case_id, payload, reference)


def select_tlc(catalog: list[dict[str, Any]]) -> None:
    source_rel = "tlc_rf/TLC_dataset.xlsx"
    frame = pd.read_excel(PACKAGE / source_rel)
    frame["source_row"] = np.arange(len(frame)) + 2
    systems = [
        ("hexane_ethyl_acetate", ("H", "EA"), ("hexane", "ethyl_acetate")),
        ("dichloromethane_methanol", ("DCM", "MeOH"), ("dichloromethane", "methanol")),
        ("hexane_diethyl_ether", ("H", "Et2O"), ("hexane", "diethyl_ether")),
    ]
    all_columns = ["H", "EA", "DCM", "MeOH", "Et2O"]
    for label, columns, names in systems:
        work = frame.copy()
        for name in [*all_columns, "Rf"]:
            work[name] = pd.to_numeric(work[name], errors="coerce").fillna(0.0)
        active = (work[columns[0]] > 0) & (work[columns[1]] > 0)
        inactive_columns = [name for name in all_columns if name not in columns]
        if inactive_columns:
            active &= (work[inactive_columns].abs().sum(axis=1) == 0)
        work = work[active & work["COMPOUND_SMILES"].notna()].copy()
        work["polar_fraction"] = work[columns[1]] / (work[columns[0]] + work[columns[1]])
        picked = choose_distinct_rows(work, "polar_fraction", [0.2, 0.5, 0.8], ["COMPOUND_ID"])
        for level, row in zip(("low", "mid", "high"), picked):
            case_id = f"tlc_{label}_{level}"
            payload = {
                "smiles": str(row["COMPOUND_SMILES"]),
                "mobile_phase": {
                    names[0]: float(row[columns[0]]),
                    names[1]: float(row[columns[1]]),
                },
                "device": "cpu",
            }
            reference = {
                "purpose": "数据集可追溯的功能/回归样例，不是独立泛化测试。",
                "selection_rule": f"{label} 体系中选择极性溶剂比例接近 20%/50%/80% 的不同化合物。",
                "source_file": source_rel,
                "source_rows": [int(row["source_row"])],
                "tlc_id": int(row["TLC_ID"]),
                "compound_id": int(row["COMPOUND_ID"]),
                "compound_name": None if pd.isna(row.get("COMPOUND_ENG_NAME")) else str(row.get("COMPOUND_ENG_NAME")),
                "cas": None if pd.isna(row.get("COMPOUND_CAS.NO")) else str(row.get("COMPOUND_CAS.NO")),
                "measured_rf": float(row["Rf"]),
            }
            add_case(catalog, "tlc", case_id, payload, reference)


def parse_ratio(value: Any) -> tuple[float, float] | None:
    try:
        left, right = str(value).strip().split("/", 1)
        pe, ea = float(left), float(right)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) and item >= 0 for item in (pe, ea)) or pe + ea <= 0:
        return None
    return pe, ea


def select_cc(catalog: list[dict[str, Any]]) -> None:
    for spec in ("4g", "8g", "25g", "40g"):
        source_rel = f"cc_column_chromatography/dataset/dataset_{spec}.csv"
        frame = pd.read_csv(PACKAGE / source_rel)
        frame["source_row"] = np.arange(len(frame)) + 2
        frame["ratio"] = frame["PE/EA"].map(parse_ratio)
        for name in ["Density g/ml", "V/ul", "Volume of loading solvent/ul", "Flow mL/min", "t1", "t2", "verified t1", "verified t2"]:
            frame[name] = pd.to_numeric(frame[name], errors="coerce")
        frame["measured_t1"] = frame["verified t1"].fillna(frame["t1"])
        frame["measured_t2"] = frame["verified t2"].fillna(frame["t2"])
        required = ["smiles", "ratio", "Density g/ml", "V/ul", "Volume of loading solvent/ul", "Flow mL/min", "measured_t1", "measured_t2"]
        frame = frame.dropna(subset=required).copy()
        frame = frame[
            frame["loading solvent"].astype(str).str.upper().isin(["PE", "EA", "DCM"])
            & (frame["Density g/ml"] > 0)
            & (frame["V/ul"] > 0)
            & (frame["Volume of loading solvent/ul"] > 0)
            & (frame["Flow mL/min"] > 0)
            & (frame["measured_t1"] >= 0)
            & (frame["measured_t2"] >= frame["measured_t1"])
        ].copy()
        frame["measured_v1_ml"] = frame["measured_t1"] / 60.0 * frame["Flow mL/min"]
        frame["measured_v2_ml"] = frame["measured_t2"] / 60.0 * frame["Flow mL/min"]
        picked = choose_distinct_rows(frame, "measured_v2_ml", [0.2, 0.5, 0.8], ["smiles"])
        for level, row in zip(("low", "mid", "high"), picked):
            pe, ea = row["ratio"]
            case_id = f"cc_{spec}_{level}"
            payload = {
                "smiles": str(row["smiles"]),
                "column_spec": spec,
                "mobile_phase": {"petroleum_ether": pe, "ethyl_acetate": ea},
                "loading_solvent": str(row["loading solvent"]).upper(),
                "density_g_ml": float(row["Density g/ml"]),
                "sample_volume_ul": float(row["V/ul"]),
                "loading_solvent_volume_ul": float(row["Volume of loading solvent/ul"]),
                "flow_rate_ml_min": float(row["Flow mL/min"]),
            }
            reference = {
                "purpose": "数据集可追溯的功能/回归样例，不是独立泛化测试。",
                "selection_rule": f"{spec} 数据中选择实测 V2 接近 20%/50%/80% 分位的不同化合物。",
                "source_file": source_rel,
                "source_rows": [int(row["source_row"])],
                "name": None if pd.isna(row.get("name")) else str(row.get("name")),
                "cas": None if pd.isna(row.get("CAS")) else str(row.get("CAS")),
                "measured_time_seconds": {"V1": float(row["measured_t1"]), "V2": float(row["measured_t2"])},
                "measured_volume_ml": {"V1": float(row["measured_v1_ml"]), "V2": float(row["measured_v2_ml"])},
                "volume_formula": "time_seconds / 60 * flow_rate_ml_min",
            }
            add_case(catalog, "cc", case_id, payload, reference)


def add_negative_cases(catalog: list[dict[str, Any]]) -> None:
    cases = [
        ("hplc", "hplc_invalid_smiles", {"smiles": ["not-a-smiles", "CC"], "ipa_fraction": 0.1, "flow_rate_ml_min": 1.0, "columns": ["ADH"], "device": "cpu"}, "SMILES"),
        ("hplc", "hplc_unknown_column", {"smiles": ["C[C@H](O)C(=O)O", "C[C@@H](O)C(=O)O"], "ipa_fraction": 0.1, "flow_rate_ml_min": 1.0, "columns": ["UNKNOWN"], "device": "cpu"}, "未知 HPLC 柱"),
        ("gc", "gc_invalid_smiles", {"smiles": ["not-a-smiles"], "initial_temperature_c": 40, "final_temperature_c": 200, "heating_rate_c_per_min": 20, "initial_hold_min": 2, "device": "cpu"}, "SMILES"),
        ("gc", "gc_incomplete_program", {"smiles": ["CCCCCCC"], "initial_temperature_c": 40, "device": "cpu"}, "完整提供"),
        ("tlc", "tlc_invalid_smiles", {"smiles": "not-a-smiles", "mobile_phase": {"hexane": 0.8, "ethyl_acetate": 0.2}, "device": "cpu"}, "无效的 SMILES"),
        ("tlc", "tlc_unsupported_mobile_phase", {"smiles": "CCO", "mobile_phase": {"hexane": 0.5, "ethyl_acetate": 0.25, "methanol": 0.25}, "device": "cpu"}, "只支持论文验证"),
        ("cc", "cc_invalid_smiles", {"smiles": "not-a-smiles", "column_spec": "4g", "mobile_phase": {"petroleum_ether": 40, "ethyl_acetate": 1}, "loading_solvent": "PE", "density_g_ml": 1.0, "sample_volume_ul": 100, "loading_solvent_volume_ul": 200, "flow_rate_ml_min": 10}, "Invalid SMILES"),
        ("cc", "cc_unsupported_mobile_phase", {"smiles": "CCO", "column_spec": "4g", "mobile_phase": {"petroleum_ether": 40, "ethyl_acetate": 1, "methanol": 1}, "loading_solvent": "PE", "density_g_ml": 1.0, "sample_volume_ul": 100, "loading_solvent_volume_ul": 200, "flow_rate_ml_min": 10}, "Unsupported mobile_phase"),
    ]
    for model, case_id, payload, error_fragment in cases:
        reference = {
            "purpose": "输入校验样例。",
            "expected_exit_code": 2,
            "expected_error_contains": error_fragment,
        }
        add_case(catalog, model, case_id, payload, reference, kind="error")


RUNNER = r'''from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

MODEL_DIRS = {"hplc": "hplc_enantioseparation", "gc": "gc_retention_time", "tlc": "tlc_rf", "cc": "cc_column_chromatography"}

def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def normalize_paths(value: Any, package_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: normalize_paths(item, package_root) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_paths(item, package_root) for item in value]
    if isinstance(value, str):
        try:
            path = Path(value)
            if path.is_absolute():
                return path.resolve().relative_to(package_root.resolve()).as_posix()
        except (OSError, ValueError):
            pass
    return value

def load_module(model: str, model_dir: Path) -> Any:
    sys.path.insert(0, str(model_dir))
    spec = importlib.util.spec_from_file_location(f"acceptance_{model}_predict", model_dir / "predict.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {model_dir / 'predict.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def main() -> int:
    parser = argparse.ArgumentParser(description="运行色谱模型 V2 验收样例")
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("actual_outputs"))
    args = parser.parse_args()
    package_root = args.package_root.resolve()
    output_dir = args.output_dir.resolve()
    suite_root = Path(__file__).resolve().parent
    os.environ.setdefault("MPLCONFIGDIR", str(output_dir / "_runtime_cache" / "matplotlib"))
    catalog = json.loads((suite_root / "case_catalog.json").read_text(encoding="utf-8"))

    for model in ("hplc", "gc", "tlc", "cc"):
        model_dir = package_root / MODEL_DIRS[model]
        cases = [item for item in catalog["cases"] if item["model"] == model]
        success_cases = [item for item in cases if item["kind"] == "success"]
        module = load_module(model, model_dir)
        if model in {"hplc", "tlc"}:
            module.load_model("cpu")
        elif model == "gc":
            module.load_model(device="cpu")
        elif model == "cc":
            for column_spec in ("4g", "8g", "25g", "40g"):
                module.load_model(column_spec)

        for item in success_cases:
            payload = json.loads((suite_root / item["input"]).read_text(encoding="utf-8"))
            result = module.predict(payload)
            result = normalize_paths(result, package_root)
            write_json(output_dir / model / f"{item['case_id']}.json", result)

        for item in [entry for entry in cases if entry["kind"] == "error"]:
            input_path = suite_root / item["input"]
            child_env = os.environ.copy()
            child_env["PYTHONIOENCODING"] = "utf-8"
            completed = subprocess.run(
                [sys.executable, str(model_dir / "predict.py"), "--input-file", str(input_path)],
                cwd=model_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
            )
            result = {
                "success": False,
                "exit_code": completed.returncode,
                "stderr": completed.stderr.strip(),
            }
            write_json(output_dir / model / f"{item['case_id']}.json", result)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


VERIFIER = r'''from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

TOLERANCE = 1e-4

def close(a: float, b: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=TOLERANCE)

def compare(expected: Any, actual: Any, path: str, failures: list[str]) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            failures.append(f"{path}: expected object")
            return
        for key, value in expected.items():
            if key in {"model_path", "model_paths"}:
                continue
            if key not in actual:
                failures.append(f"{path}.{key}: missing")
            else:
                compare(value, actual[key], f"{path}.{key}", failures)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            failures.append(f"{path}: list length mismatch")
            return
        for index, (left, right) in enumerate(zip(expected, actual)):
            compare(left, right, f"{path}[{index}]", failures)
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(actual, (int, float)) or not math.isfinite(float(actual)) or not close(expected, actual):
            failures.append(f"{path}: expected {expected}, got {actual}")
    elif expected != actual:
        failures.append(f"{path}: expected {expected!r}, got {actual!r}")

def structural_checks(model: str, output: dict[str, Any], payload: dict[str, Any], failures: list[str]) -> None:
    if output.get("success") is not True:
        failures.append("success is not true")
        return
    if model == "tlc":
        if not 0 <= float(output["predicted_rf"]) <= 1:
            failures.append("predicted_rf outside [0,1]")
    elif model == "gc":
        for row in output["predictions"]:
            q = [row["rt_q10_min"], row["rt_q50_min"], row["rt_q90_min"]]
            if not q[0] <= q[1] <= q[2]: failures.append("GC quantiles unordered")
    elif model == "hplc":
        if output["recommended_column"] not in payload["columns"]:
            failures.append("recommended_column not in candidates")
        for column in output["columns"]:
            if not 0 <= float(column["separation_probability"]) <= 1:
                failures.append("separation_probability outside [0,1]")
            for row in column["compounds"]:
                q = [row["rt_q10_min"], row["rt_q50_min"], row["rt_q90_min"]]
                if not q[0] <= q[1] <= q[2]: failures.append("HPLC quantiles unordered")
    elif model == "cc":
        windows = {row["name"]: row for row in output["predicted_elution_windows"]}
        flow = float(payload["flow_rate_ml_min"])
        for name in ("V1", "V2"):
            volume = windows[name]["volume_ml"]
            seconds = windows[name]["time_seconds"]
            if not volume["q10"] <= volume["q50"] <= volume["q90"]:
                failures.append(f"{name} quantiles unordered")
            for q in ("q10", "q50", "q90"):
                if not close(seconds[q], volume[q] * 60.0 / flow):
                    failures.append(f"{name}.{q} time conversion mismatch")
        if windows["V1"]["volume_ml"]["q50"] > windows["V2"]["volume_ml"]["q50"]:
            failures.append("V1 median greater than V2 median")

def main() -> int:
    parser = argparse.ArgumentParser(description="校验色谱模型 V2 验收输出")
    parser.add_argument("--actual-dir", type=Path, default=Path("actual_outputs"))
    args = parser.parse_args()
    suite_root = Path(__file__).resolve().parent
    actual_dir = args.actual_dir.resolve()
    catalog = json.loads((suite_root / "case_catalog.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    checked = 0
    for item in catalog["cases"]:
        expected = json.loads((suite_root / item["expected_output"]).read_text(encoding="utf-8"))
        actual_path = actual_dir / item["model"] / f"{item['case_id']}.json"
        if not actual_path.is_file():
            failures.append(f"{item['case_id']}: missing actual output")
            continue
        actual = json.loads(actual_path.read_text(encoding="utf-8"))
        local: list[str] = []
        if item["kind"] == "success":
            payload = json.loads((suite_root / item["input"]).read_text(encoding="utf-8"))
            compare(expected, actual, item["case_id"], local)
            structural_checks(item["model"], actual, payload, local)
        else:
            reference = json.loads((suite_root / item["reference"]).read_text(encoding="utf-8"))
            if actual.get("success") is not False:
                local.append("error case unexpectedly succeeded")
            if actual.get("exit_code") != reference["expected_exit_code"]:
                local.append(f"expected exit code {reference['expected_exit_code']}, got {actual.get('exit_code')}")
            if reference["expected_error_contains"] not in actual.get("stderr", ""):
                local.append(f"stderr missing {reference['expected_error_contains']!r}")
        failures.extend(f"{item['case_id']}: {message}" for message in local)
        checked += 1
    if failures:
        print(json.dumps({"success": False, "checked": checked, "failures": failures}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"success": True, "checked": checked, "tolerance": TOLERANCE}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


README = """# 色谱模型 V2 验收样例

本附件提供 HPLC、GC、TLC、CC 四类模型的数据集可追溯输入、固定模型输出、实测参考值和自动复跑脚本。

这些样例用于验证交付包能否正确加载和稳定推理。来源记录可能参与过模型训练，因此不能作为独立测试集，也不能据此声明模型的泛化性能。

## 样例数量

- HPLC：25 个成功样例，覆盖全部 25 种支持柱型。
- GC：12 个成功样例，覆盖四个实测 RT 分层。
- TLC：9 个成功样例，覆盖三种支持的二元流动相体系。
- CC：12 个成功样例，覆盖 4g、8g、25g、40g 四种柱规格。
- 输入校验：每个模型 2 个错误样例，共 8 个。

合计 58 个成功预测样例和 8 个输入校验样例。

## 运行

在本附件目录执行：

```powershell
D:\\Anaconda\\envs\\DE\\python.exe .\\run_acceptance.py --package-root "<V2模型包目录>" --output-dir .\\actual_outputs
D:\\Anaconda\\envs\\DE\\python.exe .\\verify_outputs.py --actual-dir .\\actual_outputs
```

统一包内运行时，`--package-root` 指向统一包根目录。独立测试包不包含权重，必须指向 V2 模型包；V1 的 CC 时间字段存在已知换算问题，不应作为本套基准的目标版本。

## 文件说明

每个用例目录包含：

- `input.json`：模型请求。
- `expected_output.json`：V2 权重在 DE/CPU 环境生成的基准输出。
- `reference.json`：数据集文件、原始行号、实测值与选择方法。
- `comparison.json`：成功样例的预测—实测对比。

`summary.csv` 按预测目标展开汇总。绝对模型路径已归一化为相对于模型包根目录的路径；复跑时路径字段不参与数值一致性判断。

验收采用 `1e-4` 的绝对容差，并额外检查字段结构、分位顺序、数值范围和 CC 时间换算。预测误差仅作说明，不作为单例通过门槛。

## CC V2 修复

CC 的训练目标是洗脱体积：`volume_ml = time_seconds / 60 × flow_rate_ml_min`。因此反向换算必须为：

`time_seconds = volume_ml × 60 / flow_rate_ml_min`

V1 入口误用了系数 1200，使 `time_seconds` 放大 20 倍。V2 只修复该派生时间字段；权重和 V1/V2 预测体积不变。

## 授权

本附件沿用模型交付包的非商业研究用途声明。公开发布、商业部署或再分发前，应确认论文源码、数据和权重授权。
"""


def version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def create_environment() -> dict[str, Any]:
    import torch
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "executable": str(Path(sys.executable).resolve()),
        "device": "cpu",
        "packages": {
            "numpy": version("numpy"),
            "pandas": version("pandas"),
            "torch": version("torch"),
            "torch-geometric": version("torch-geometric"),
            "rdkit": version("rdkit"),
            "mordred": version("mordred"),
            "scikit-learn": version("scikit-learn"),
            "lightgbm": version("lightgbm"),
            "xgboost": version("xgboost"),
        },
    }


def run_suite() -> Path:
    actual = OUTPUT / "_baseline_actual_outputs"
    subprocess.run(
        [sys.executable, str(OUTPUT / "run_acceptance.py"), "--package-root", str(PACKAGE), "--output-dir", str(actual)],
        cwd=OUTPUT,
        check=True,
    )
    return actual


def percent_error(predicted: float, measured: float) -> float | None:
    if measured == 0:
        return None
    return abs(predicted - measured) / abs(measured)


def make_comparisons(catalog: list[dict[str, Any]], actual: Path) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for item in catalog:
        output_path = actual / item["model"] / f"{item['case_id']}.json"
        output = json.loads(output_path.read_text(encoding="utf-8"))
        destination = OUTPUT / item["expected_output"]
        if item["kind"] != "success":
            reference = json.loads((OUTPUT / item["reference"]).read_text(encoding="utf-8"))
            write_json(destination, {
                "success": False,
                "exit_code": reference["expected_exit_code"],
                "error_contains": reference["expected_error_contains"],
            })
            continue
        write_json(destination, output)
        reference = json.loads((OUTPUT / item["reference"]).read_text(encoding="utf-8"))
        comparisons: list[dict[str, Any]] = []
        if item["model"] == "hplc":
            compounds = output["columns"][0]["compounds"]
            measured_values = reference["measured_rt_min"]
            predicted_order = sorted(range(2), key=lambda i: compounds[i]["rt_q50_min"])
            measured_order = sorted(range(2), key=lambda i: measured_values[i])
            for index, row in enumerate(compounds):
                predicted, measured = float(row["rt_q50_min"]), float(measured_values[index])
                comparisons.append({
                    "target": f"compound_{index + 1}_rt_min", "predicted_q50": predicted,
                    "measured": measured, "absolute_error": abs(predicted - measured),
                    "relative_error": percent_error(predicted, measured),
                    "inside_q10_q90": row["rt_q10_min"] <= measured <= row["rt_q90_min"],
                })
            order_match = predicted_order == measured_order
            for row in comparisons: row["elution_order_match"] = order_match
        elif item["model"] == "gc":
            row = output["predictions"][0]
            predicted, measured = float(row["rt_q50_min"]), float(reference["measured_rt_min"])
            comparisons.append({
                "target": "retention_time_min", "predicted_q50": predicted,
                "measured": measured, "absolute_error": abs(predicted - measured),
                "relative_error": percent_error(predicted, measured),
                "inside_q10_q90": row["rt_q10_min"] <= measured <= row["rt_q90_min"],
            })
        elif item["model"] == "tlc":
            predicted, measured = float(output["predicted_rf"]), float(reference["measured_rf"])
            comparisons.append({
                "target": "rf", "predicted_q50": predicted, "measured": measured,
                "absolute_error": abs(predicted - measured),
                "relative_error": percent_error(predicted, measured), "inside_q10_q90": None,
            })
        elif item["model"] == "cc":
            windows = {row["name"]: row for row in output["predicted_elution_windows"]}
            for target in ("V1", "V2"):
                row = windows[target]["volume_ml"]
                predicted, measured = float(row["q50"]), float(reference["measured_volume_ml"][target])
                comparisons.append({
                    "target": f"{target}_volume_ml", "predicted_q50": predicted,
                    "measured": measured, "absolute_error": abs(predicted - measured),
                    "relative_error": percent_error(predicted, measured),
                    "inside_q10_q90": row["q10"] <= measured <= row["q90"],
                    "measured_time_seconds": reference["measured_time_seconds"][target],
                    "predicted_time_seconds_q50": windows[target]["time_seconds"]["q50"],
                })
        comparison_doc = {
            "case_id": item["case_id"],
            "model": item["model"],
            "note": "误差用于描述该回归样例，不作为独立泛化性能结论或单例通过门槛。",
            "comparisons": comparisons,
        }
        write_json(OUTPUT / item["comparison"], comparison_doc)
        for row in comparisons:
            summary.append({
                "model": item["model"], "case_id": item["case_id"], **row,
                "source_file": reference["source_file"],
                "source_rows": ";".join(str(value) for value in reference["source_rows"]),
            })
    return summary


def write_summary(rows: list[dict[str, Any]]) -> None:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with (OUTPUT / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_manifest(root: Path) -> None:
    manifest = root / "MANIFEST_SHA256.txt"
    if manifest.exists():
        manifest.unlink()
    lines = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix().lower()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        relative = str(path.relative_to(root)).replace("/", "\\")
        lines.append(f"{digest}  {relative}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not PACKAGE.is_dir():
        raise FileNotFoundError(PACKAGE)
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)

    catalog: list[dict[str, Any]] = []
    select_hplc(catalog)
    select_gc(catalog)
    select_tlc(catalog)
    select_cc(catalog)
    add_negative_cases(catalog)
    counts = {
        model: {
            "success": sum(item["model"] == model and item["kind"] == "success" for item in catalog),
            "error": sum(item["model"] == model and item["kind"] == "error" for item in catalog),
        }
        for model in MODEL_DIRS
    }
    write_json(OUTPUT / "case_catalog.json", {"version": "2", "counts": counts, "cases": catalog})
    (OUTPUT / "run_acceptance.py").write_text(RUNNER, encoding="utf-8")
    (OUTPUT / "verify_outputs.py").write_text(VERIFIER, encoding="utf-8")
    (OUTPUT / "README.md").write_text(README, encoding="utf-8")
    write_json(OUTPUT / "environment.json", create_environment())

    actual = run_suite()
    summary = make_comparisons(catalog, actual)
    shutil.rmtree(actual)
    write_summary(summary)
    write_manifest(OUTPUT)
    print(json.dumps({"output": str(OUTPUT), "counts": counts, "summary_rows": len(summary)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
