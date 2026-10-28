"""Build the non-commercial CC/HPLC Ubuntu-LR delivery archive.

This script intentionally writes a new versioned directory and never touches
the existing V2 package.  Training caches and historical checkpoints are not
copied into the release.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "deliverables" / "chromatography_tools_cc_hplc_ubuntu_lr_v1"
ZIP_PATH = PACKAGE.with_suffix(".zip")
CC = ROOT / "Services" / "column_chromatography_prediction-main"
HPLC = ROOT / "Services" / "Retention-Time-Prediction-for-Chromatographic-Enantioseparation-main"
OLD_ACCEPTANCE = ROOT / "deliverables" / "chromatography_tools_noncommercial_with_examples_v2" / "acceptance_examples"


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_acceptance() -> None:
    target = PACKAGE / "acceptance_examples"
    target.mkdir(parents=True)
    catalog = json.loads((OLD_ACCEPTANCE / "case_catalog.json").read_text(encoding="utf-8"))
    cases = [item for item in catalog["cases"] if item["model"] in {"cc", "hplc"}]
    new_catalog = {
        "version": "ubuntu-lr-v1",
        "counts": {
            "cc": {"success": sum(x["model"] == "cc" and x["kind"] == "success" for x in cases), "error": sum(x["model"] == "cc" and x["kind"] == "error" for x in cases)},
            "hplc": {"success": sum(x["model"] == "hplc" and x["kind"] == "success" for x in cases), "error": sum(x["model"] == "hplc" and x["kind"] == "error" for x in cases)},
        },
        "cases": cases,
    }
    (target / "case_catalog.json").write_text(json.dumps(new_catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for model in ("cc", "hplc"):
        source = OLD_ACCEPTANCE / "cases" / model
        destination = target / "cases" / model
        shutil.copytree(source, destination)
    run_text = (OLD_ACCEPTANCE / "run_acceptance.py").read_text(encoding="utf-8")
    run_text = run_text.replace("运行色谱模型 V2 验收样例", "运行 CC/HPLC Ubuntu LR 验收样例")
    run_text = run_text.replace("MODEL_DIRS = {\"hplc\": \"hplc_enantioseparation\", \"gc\": \"gc_retention_time\", \"tlc\": \"tlc_rf\", \"cc\": \"cc_column_chromatography\"}", "MODEL_DIRS = {\"hplc\": \"hplc_enantioseparation\", \"cc\": \"cc_column_chromatography\"}")
    run_text = run_text.replace('for model in ("hplc", "gc", "tlc", "cc"):', 'for model in ("hplc", "cc"):')
    (target / "run_acceptance.py").write_text(run_text, encoding="utf-8")
    verify_text = (OLD_ACCEPTANCE / "verify_outputs.py").read_text(encoding="utf-8").replace("校验色谱模型 V2 验收输出", "校验 CC/HPLC Ubuntu LR 验收输出")
    (target / "verify_outputs.py").write_text(verify_text, encoding="utf-8")
    (target / "environment.json").write_text(json.dumps({
        "training": {"conda": "LR", "python": "3.12.13", "device": "RTX 4060", "split_seed_cc": 42, "split_seed_hplc": 388, "model_seed_primary": 525},
        "verification": {"device": "cpu", "tolerance": 1e-4, "clean_environment_rebuild_verified": False},
        "note": "全新环境重建未在本机验证；本包按 Ubuntu LR 运行时导出。",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "README.md").write_text("""# CC/HPLC Ubuntu LR v1 验收样例\n\n本目录包含 12 个 CC 成功样例、25 个 HPLC 成功样例，以及每个模型 2 个非法输入样例。expected_output.json 由本包新 Ubuntu/LR 权重重新生成；reference.json 仅用于数据可追溯的功能/回归对照，不是独立泛化评测。\n\n运行：\n\n```bash\nconda run -n LR python run_acceptance.py --package-root ..\nconda run -n LR python verify_outputs.py\n```\n\n验收在 CPU 上执行，数值容差为 1e-4。\n""", encoding="utf-8")


def write_docs() -> None:
    (PACKAGE / "requirements-ubuntu-lr.txt").write_text("""# Runtime pins observed in /home/keyous/miniforge3/envs/LR\n# Do not install globally; a fresh environment rebuild was not verified here.\npython==3.12.13\nnumpy==1.26.4\npandas==2.2.3\nrdkit==2023.09.1\nmordred==1.2.0\ntorch==2.3.0\ntorch-geometric==2.8.0.post1\ntqdm==4.70.0\n""", encoding="utf-8")
    (PACKAGE / "environment-ubuntu-cpu.yml").write_text("""name: chromatography-ubuntu-cpu\n# Informational only. The clean environment was not created or verified on the training host.\nchannels:\n  - conda-forge\ndependencies:\n  - python=3.12.13\n  - numpy=1.26.4\n  - pandas=2.2.3\n  - rdkit=2023.09.1\n  - pip\n  - pip:\n      - mordred==1.2.0\n      - torch==2.3.0\n      - torch-geometric==2.8.0.post1\n""", encoding="utf-8")
    (PACKAGE / "README.md").write_text("""# CC/HPLC Ubuntu LR v1 非商业离线交付包\n\n本包是从 Windows 训练权重迁移后，在 Ubuntu 24.04 + Conda LR 环境重新训练的 CC 与 HPLC 模型。GC/TLC、旧 V2 交付包、统一 8010 服务和旧权重均未修改。默认入口在 CPU 上推理，不需要服务端。\n\n## 内容\n\n- `cc_column_chromatography/`：4g 基础模型及 8g、25g、40g 迁移模型，保持原请求/响应结构；时间严格按 `time_seconds = volume_ml * 60 / flow_rate_ml_min`。\n- `hplc_enantioseparation/`：25 柱 QGeoGNN、RT 分位数、Sp 与推荐柱接口；交付入口使用 seed=324/42 的严格双模型输出集成。\n- `acceptance_examples/`：37 个成功样例（CC 12、HPLC 25）和 4 个错误样例；expected outputs 由本包权重生成。\n\n## 训练与论文对照\n\nCC 使用论文 80/10/10 随机拆分、5 层 GIN/128 维，并保留所有合法正目标。本地 4g CSV 为 4243 行，不是论文报告的 4684 条，因此指标应理解为本地数据复现。HPLC 使用 25867 条原始记录，删除坏记录并按论文移除 `RT*flow > 60`，目标为 `RTv=RT*flow`，主实验拆分种子 388、90/5/5。论文把多柱 3.40 分别称为 MAE 和 RMSE，本包同时报告两者并保留该口径矛盾。\n\n### 权重选择记录\n\n各规格均保留验证集损失更低的候选：4g=`paper_4g_final_lr5e-4_b1024_s525`，8g=`transfer_8g_lr1e4_s525`，25g=`transfer_25g_final_lr1e4_s525`，40g=`transfer_40g_lr1e4_s525`；其中 25g/40g 使用迁移搜索中更好的权重，没有被后续较差试验覆盖。HPLC 使用 `final_hplc_lr5e4_b1024_s324` 与 `final_hplc_lr5e4_b1024_s42_compare` 的输出集成，并以验证总损失选择。\n\nHPLC RTv 验证 R²=0.6930，测试 R²=0.7201，距论文多柱 0.702 约 0.018；CC 的 4g/8g/25g 接近或超过论文对照，但 40g 的本地 R²（V1/V2=0.7191/0.6589）未达到约 0.10 的复现阈值。因此本包统一标注为“本地数据复现”，不宣称完全复现论文；canonical-SMILES（CC）与 index（HPLC）分组实验仅作泛化风险诊断。\n\n指标、数据 SHA256、拆分与运行元数据见各模型目录的 `metrics.json`/`metadata.json`。随机拆分权重用于交付，分组实验仅作风险诊断。\n\n## Ubuntu CPU 使用\n\n```bash\nconda run -n LR python cc_column_chromatography/predict.py --demo\nconda run -n LR python hplc_enantioseparation/predict.py --demo\ncd acceptance_examples\nconda run -n LR python run_acceptance.py --package-root .. --output-dir actual_outputs\nconda run -n LR python verify_outputs.py --actual-dir actual_outputs\n```\n\n`requirements-ubuntu-lr.txt` 和 `environment-ubuntu-cpu.yml` 仅为重建说明；按用户要求未创建新的干净环境，因此全新环境重建未在本机验证。模型权重和论文数据仅限非商业研究/功能验证，请按原作者许可使用。\n""", encoding="utf-8")
    (PACKAGE / "CHANGELOG.md").write_text("""# Changelog\n\n## ubuntu-lr-v1\n\n- 在 Ubuntu LR 环境重新训练 CC 4g/8g/25g/40g 和 HPLC 25 柱 QGeoGNN。\n- HPLC 交付权重使用严格 state_dict 加载；保留旧源代码权重的兼容分支但不带入本包。\n- 修正 CC 离线入口的时间换算为体积 × 60 / 流速。\n- 新增 CPU 验收、数据审计、模型 SHA256、论文指标对照和环境说明。\n""", encoding="utf-8")
    changelog_path = PACKAGE / "CHANGELOG.md"
    if changelog_path.is_file():
        changelog_path.write_text(changelog_path.read_text(encoding="utf-8").replace("HPLC 交付权重使用严格 state_dict 加载；", "HPLC 交付权重使用严格 state_dict 加载，并以 seed=324/42 双模型输出集成作为最终入口；"), encoding="utf-8")
    cc_lines = ["# Ubuntu LR 指标报告", "", "## CC", "", "| 规格 | 验证 V1 R² | 验证 V2 R² | 测试 V1 R² | 测试 V2 R² | 论文 V1/V2 R² |", "|---|---:|---:|---:|---:|---|"]
    for spec, reference in (("4g", "0.859 / 0.913"), ("8g", "0.759 / 0.752"), ("25g", "0.747 / 0.840"), ("40g", "0.826 / 0.824")):
        folder = PACKAGE / "cc_column_chromatography" / "cc_qgeognn_artifacts" / f"qgeognn_silica_{spec}{'_ft' if spec != '4g' else ''}"
        metrics = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))["metrics"]
        cc_lines.append(f"| {spec} | {metrics['validation']['V1']['r2']:.4f} | {metrics['validation']['V2']['r2']:.4f} | {metrics['test']['V1']['r2']:.4f} | {metrics['test']['V2']['r2']:.4f} | {reference} |")
        cc_lines.append(f"{spec} 详细指标：验证 V1 MAE/RMSE={metrics['validation']['V1']['mae']:.4f}/{metrics['validation']['V1']['rmse']:.4f}、V2 MAE/RMSE={metrics['validation']['V2']['mae']:.4f}/{metrics['validation']['V2']['rmse']:.4f}、10–90% 覆盖率={metrics['validation']['interval_10_90_coverage']['V1']:.4f}/{metrics['validation']['interval_10_90_coverage']['V2']:.4f}；测试 V1 MAE/RMSE={metrics['test']['V1']['mae']:.4f}/{metrics['test']['V1']['rmse']:.4f}、V2 MAE/RMSE={metrics['test']['V2']['mae']:.4f}/{metrics['test']['V2']['rmse']:.4f}、10–90% 覆盖率={metrics['test']['interval_10_90_coverage']['V1']:.4f}/{metrics['test']['interval_10_90_coverage']['V2']:.4f}。")
    hmeta = json.loads((PACKAGE / "hplc_enantioseparation" / "application" / "metadata.json").read_text(encoding="utf-8"))
    hm = hmeta["metrics"]
    cc_diag = CC / "cc_qgeognn_training" / "runs_ubuntu_lr_v1" / "diagnostic_4g_grouped_smiles" / "metrics.json"
    h_diag = HPLC / "hplc_qgeognn_training" / "runs_ubuntu_lr_v1" / "diagnostic_hplc_grouped_index" / "metrics.json"
    if cc_diag.is_file():
        cm = json.loads(cc_diag.read_text(encoding="utf-8"))
        cc_lines.append(f"CC canonical-SMILES 分组诊断验证 R²：V1={cm['validation']['V1']['r2']:.4f}，V2={cm['validation']['V2']['r2']:.4f}（仅风险诊断，不替换随机拆分权重）。")
    cc_lines.extend(["", "## HPLC 多柱", "", f"验证集 RTv R²={hm['validation']['RTv']['r2']:.4f}，MAE={hm['validation']['RTv']['mae']:.4f}，RMSE={hm['validation']['RTv']['rmse']:.4f}，MRE={hm['validation']['RTv']['mre']:.4f}；测试集 RTv R²={hm['test']['RTv']['r2']:.4f}，MAE={hm['test']['RTv']['mae']:.4f}，RMSE={hm['test']['RTv']['rmse']:.4f}。论文多柱 R²=0.702，论文/补充材料对 3.40 的 MAE/RMSE 命名不一致，本报告同时保留两种指标。", "", "区间覆盖率与分位顺序违规率同时写入 application/metadata.json；测试集只作最终报告，不参与权重选择。"])
    if h_diag.is_file():
        dm = json.loads(h_diag.read_text(encoding="utf-8"))
        cc_lines.append(f"HPLC index 分组诊断验证 RTv R²={dm['validation']['RTv']['r2']:.4f}（仅风险诊断，不替换随机拆分权重）。")
    (PACKAGE / "metrics_report.md").write_text("\n".join(cc_lines) + "\n", encoding="utf-8")


def copy_models() -> None:
    cc_target = PACKAGE / "cc_column_chromatography"
    cc_target.mkdir(parents=True)
    for relative in ["predict.py", "application/QGeoGNN.py", "cc_qgeognn_runtime/__init__.py", "cc_qgeognn_runtime/bundle.py", "cc_qgeognn_runtime/legacy.py", "cc_qgeognn_runtime/schema.json", "Wu 等 - 2025 - Intelligent column chromatography prediction model based on automation and machine learning.pdf", "examples/request.json"]:
        copy_file(CC / relative, cc_target / relative.replace("cc_qgeognn_runtime/", "cc_qgeognn_runtime/"))
    for source in sorted((CC / "dataset").glob("dataset_*.csv")):
        copy_file(source, cc_target / "dataset" / source.name)
    cc_artifacts = CC / "cc_qgeognn_artifacts_ubuntu_lr_v1"
    for bundle in sorted(cc_artifacts.glob("qgeognn_silica_*")):
        if bundle.is_dir():
            target_bundle = cc_target / "cc_qgeognn_artifacts" / bundle.name
            shutil.copytree(bundle, target_bundle)
            metadata_path = bundle / "metadata.json"
            if metadata_path.is_file():
                source_run = json.loads(metadata_path.read_text(encoding="utf-8")).get("source_run")
                if source_run:
                    run_dir = CC / "cc_qgeognn_training" / "runs_ubuntu_lr_v1" / source_run
                    for manifest_name in ("data_audit.json", "splits.json"):
                        if (run_dir / manifest_name).is_file() and not (target_bundle / manifest_name).is_file():
                            copy_file(run_dir / manifest_name, target_bundle / manifest_name)
    hplc_target = PACKAGE / "hplc_enantioseparation"
    hplc_target.mkdir(parents=True)
    for relative in ["predict.py", "runtime.py", "Xu 等 - 2023 - Retention time prediction for chromatographic enantioseparation by quantile geometry-enhanced graph.pdf", "examples/request.json"]:
        copy_file(HPLC / relative, hplc_target / relative)
    artifact = HPLC / "hplc_qgeognn_artifacts_ubuntu_lr_v1"
    for filename in ("GeoGNN_model.pth", "GeoGNN_model_ensemble.pth", "GeoGNN_model_s324.pth", "GeoGNN_model_s42.pth", "GeoGNN_model.meta.json", "column_descriptor.npy", "metadata.json", "checksums.json"):
        copy_file(artifact / filename, hplc_target / "application" / filename)
    hmeta = json.loads((artifact / "metadata.json").read_text(encoding="utf-8"))
    hrun = HPLC / "hplc_qgeognn_training" / "runs_ubuntu_lr_v1" / str(hmeta.get("source_run", ""))
    for manifest_name in ("data_audit.json", "splits.json"):
        source_manifest = artifact / manifest_name if (artifact / manifest_name).is_file() else hrun / manifest_name
        if source_manifest.is_file():
            copy_file(source_manifest, hplc_target / "application" / manifest_name)
    for filename in ("All_column_charity.csv", "bad_all_column.npy"):
        copy_file(HPLC / "dataset" / filename, hplc_target / "dataset" / filename)


def write_hashes() -> None:
    for model in ("cc_column_chromatography", "hplc_enantioseparation"):
        entries = []
        for path in sorted((PACKAGE / model).rglob("*")):
            if path.is_file() and path.name not in {"MODEL_SHA256.txt"}:
                entries.append(f"{sha256(path)}  {path.relative_to(PACKAGE).as_posix()}")
        (PACKAGE / model / "MODEL_SHA256.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")
    entries = []
    for path in sorted(PACKAGE.rglob("*")):
        if path.is_file() and path.name not in {"MANIFEST_SHA256.txt"}:
            entries.append(f"{sha256(path)}  {path.relative_to(PACKAGE).as_posix()}")
    (PACKAGE / "MANIFEST_SHA256.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")


def main() -> None:
    if PACKAGE.exists():
        raise SystemExit(f"Refusing to overwrite existing package: {PACKAGE}")
    for required in (CC, HPLC, OLD_ACCEPTANCE):
        if not required.exists():
            raise FileNotFoundError(required)
    PACKAGE.mkdir(parents=True)
    copy_models()
    copy_acceptance()
    write_docs()
    write_hashes()
    print(PACKAGE)


if __name__ == "__main__":
    main()
