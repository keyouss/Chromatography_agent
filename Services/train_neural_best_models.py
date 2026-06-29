from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import train_test_split

from model_service_utils import (
    HPLC_COLUMN_DESCRIPTOR_PATH,
    HPLC_MODEL_PATH,
    SERVICES_DIR,
    TLC_TORCH_MODEL_PATH,
    TLCANN,
    tlc_features,
)


TLC_PROJECT = SERVICES_DIR / "ML-Prediction-for-Rf-values-main"
CC_PROJECT = SERVICES_DIR / "column_chromatography_prediction-main"
HPLC_PROJECT = SERVICES_DIR / "Retention-Time-Prediction-for-Chromatographic-Enantioseparation-main"


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def standardize(train: np.ndarray, valid: np.ndarray, test: np.ndarray):
    mean = train.mean(axis=0)
    std = train.std(axis=0)
    std[std < 1e-6] = 1.0
    return (train - mean) / std, (valid - mean) / std, (test - mean) / std, mean, std


def write_history_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def train_tlc_ann(epochs: int, seed: int, device: torch.device) -> dict:
    record_dir = TLC_PROJECT / "training_records" / datetime.now().strftime("ann_%Y%m%d_%H%M%S")
    record_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(TLC_PROJECT / "TLC_dataset.xlsx")
    rows: list[np.ndarray] = []
    targets: list[float] = []
    for row in df.itertuples(index=False):
        smiles = getattr(row, "COMPOUND_SMILES")
        ratios = [getattr(row, name) for name in ["H", "EA", "DCM", "MeOH", "Et2O"]]
        try:
            rows.append(tlc_features(smiles, ratios).reshape(-1))
            targets.append(float(getattr(row, "Rf")))
        except Exception:
            continue

    X = np.asarray(rows, dtype=np.float32)
    y = np.asarray(targets, dtype=np.float32).reshape(-1, 1)
    X_train, X_tmp, y_train, y_tmp = train_test_split(X, y, test_size=0.2, random_state=seed)
    X_valid, X_test, y_valid, y_test = train_test_split(X_tmp, y_tmp, test_size=0.5, random_state=seed)
    X_train, X_valid, X_test, mean, std = standardize(X_train, X_valid, X_test)

    model = TLCANN(in_features=X_train.shape[1], hidden_features=128).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-5)
    loss_fn = torch.nn.MSELoss()

    tx = torch.tensor(X_train, dtype=torch.float32, device=device)
    ty = torch.tensor(y_train, dtype=torch.float32, device=device)
    vx = torch.tensor(X_valid, dtype=torch.float32, device=device)
    vy = torch.tensor(y_valid, dtype=torch.float32, device=device)

    history: list[dict] = []
    best_state = None
    best_valid_mae = float("inf")
    best_epoch = 0
    for epoch in range(1, epochs + 1):
        model.train()
        optimizer.zero_grad()
        pred = model(tx)
        loss = loss_fn(pred, ty)
        loss.backward()
        optimizer.step()

        if epoch == 1 or epoch % 25 == 0 or epoch == epochs:
            model.eval()
            with torch.no_grad():
                valid_pred = model(vx).detach().cpu().numpy()
            valid_mae = mean_absolute_error(y_valid, valid_pred)
            valid_rmse = mean_squared_error(y_valid, valid_pred) ** 0.5
            row = {
                "epoch": epoch,
                "train_loss": float(loss.detach().cpu()),
                "valid_mae": float(valid_mae),
                "valid_rmse": float(valid_rmse),
            }
            history.append(row)
            if valid_mae < best_valid_mae:
                best_valid_mae = float(valid_mae)
                best_epoch = epoch
                best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}

    if best_state is None:
        best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}

    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        test_pred = model(torch.tensor(X_test, dtype=torch.float32, device=device)).detach().cpu().numpy()
    test_mae = mean_absolute_error(y_test, test_pred)
    test_rmse = mean_squared_error(y_test, test_pred) ** 0.5

    artifact = {
        "model_type": "tlc_ann",
        "source": "ML-Prediction-for-Rf-values-main/Rf_prediction.py ANN architecture",
        "state_dict": best_state,
        "input_dim": int(X_train.shape[1]),
        "hidden_dim": 128,
        "feature_mean": mean.astype(np.float32),
        "feature_std": std.astype(np.float32),
        "feature_version": "tlc_maccs_descriptor_v1",
        "best_epoch": best_epoch,
        "validation_mae": best_valid_mae,
        "test_mae": float(test_mae),
        "test_rmse": float(test_rmse),
        "seed": seed,
    }
    project_model = record_dir / "best_tlc_model.pth"
    torch.save(artifact, project_model)
    shutil.copy2(project_model, TLC_TORCH_MODEL_PATH)
    write_history_csv(record_dir / "training_history.csv", history)
    (record_dir / "summary.json").write_text(
        json.dumps({k: v for k, v in artifact.items() if k not in {"state_dict", "feature_mean", "feature_std"}}, indent=2),
        encoding="utf-8",
    )
    return {
        "project_model": str(project_model),
        "services_model": str(TLC_TORCH_MODEL_PATH),
        "record_dir": str(record_dir),
        "best_epoch": best_epoch,
        "validation_mae": best_valid_mae,
        "test_mae": float(test_mae),
        "test_rmse": float(test_rmse),
    }


def _infer_column_mass(column_specs: str) -> float:
    text = str(column_specs).lower()
    for value in [40, 25, 8, 4]:
        if f"{value}g" in text or f"{value} g" in text:
            return float(value)
    return 4.0


def _cc_training_frame() -> pd.DataFrame:
    dataset_dir = CC_PROJECT / "dataset"
    frames = [pd.read_csv(dataset_dir / filename) for filename in ["dataset_4g.csv", "dataset_8g.csv", "dataset_25g.csv", "dataset_40g.csv"]]
    return pd.concat(frames, ignore_index=True)


def train_cc_geognn(epochs=500, seed=42, device=None):
    import torch
    from torch_geometric.data import DataLoader as GeoDataLoader
    import torch.optim as optim
    from torch.optim.lr_scheduler import StepLR

    from model_service_utils import (
        GINGraphPooling, CC_TORCH_MODEL_PATH,
        CC_BOND_FLOAT_NAMES, CC_BOND_ANGLE_FLOAT_NAMES,
        obtain_3D_mol, _compute_mordred_descriptor, _cc_convert_eluent,
        _cc_build_graph_pair, CC_SELECTED_MORDRED_INDICES,
    )
    from rdkit.Chem import Descriptors

    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    set_seed(seed)

    record_dir = CC_PROJECT / "training_records" / datetime.now().strftime("geognn_%Y%m%d_%H%M%S")
    record_dir.mkdir(parents=True, exist_ok=True)

    df = _cc_training_frame()
    records = df.dropna(subset=["smiles", "t1", "t2", "PE/EA", "Flow mL/min"]).to_dict("records")
    if len(records) < 20:
        raise RuntimeError("Not enough valid CC GeoGNN training rows were produced.")

    unique_smiles = sorted({record["smiles"] for record in records})

    print(f"Building 3D conformers and Mordred descriptors for {len(unique_smiles)} unique compounds...")

    mordred_cache = {}
    basic_desc_cache = {}
    failed_smiles = set()

    for smi in unique_smiles:
        try:
            mol = obtain_3D_mol(smi)
            if mol is None:
                failed_smiles.add(smi)
                continue
            mordred_cache[smi] = _compute_mordred_descriptor(mol, nBits=1827)
            basic_desc = [
                Descriptors.MolWt(mol),
                Descriptors.NumRotatableBonds(mol),
                Descriptors.NumHDonors(mol),
                Descriptors.NumHAcceptors(mol),
                Descriptors.MolLogP(mol),
            ]
            basic_desc_cache[smi] = basic_desc
        except Exception:
            failed_smiles.add(smi)
            continue

    valid_records = [record for record in records if record["smiles"] not in failed_smiles]
    print(f"Valid rows after filtering bad molecules: {len(valid_records)} / {len(records)}")

    if len(valid_records) < 10:
        raise RuntimeError("Not enough valid CC GeoGNN training rows after filtering bad molecules.")

    dataset_save_dir = CC_PROJECT / "dataset_save"
    dataset_save_dir.mkdir(parents=True, exist_ok=True)
    descriptor_rows = []
    eluent_rows = []
    for record in valid_records:
        descriptor_rows.append(np.array(
            basic_desc_cache[record["smiles"]] +
            [mordred_cache[record["smiles"]][i] for i in CC_SELECTED_MORDRED_INDICES],
            dtype=np.float32,
        ))
        eluent_rows.append(_cc_convert_eluent(str(record["PE/EA"])).reshape(-1))

    descriptor_array = np.stack(descriptor_rows, axis=0)
    eluent_array = np.stack(eluent_rows, axis=0)
    np.save(str(dataset_save_dir / "X_max_eluent.npy"), np.max(eluent_array, axis=0))
    np.save(str(dataset_save_dir / "X_min_eluent.npy"), np.min(eluent_array, axis=0))
    np.save(str(dataset_save_dir / "X_max_descriptor.npy"), np.max(descriptor_array, axis=0))
    np.save(str(dataset_save_dir / "X_min_descriptor.npy"), np.min(descriptor_array, axis=0))

    print("Building graph data batches...")

    graph_atom_bond_list = []
    graph_bond_angle_list = []

    for record in valid_records:
        speed = float(record["Flow mL/min"])
        if speed <= 0:
            continue
        descriptor = np.array(
            basic_desc_cache[record["smiles"]] +
            [mordred_cache[record["smiles"]][i] for i in CC_SELECTED_MORDRED_INDICES],
            dtype=np.float32,
        )
        graph_pair = _cc_build_graph_pair(
            f"{record['smiles']},{record['smiles']}",
            str(record["PE/EA"]),
            e="PE",
            m=_infer_column_mass(record.get("column_specs", "")),
            V_e=40,
            descriptor_override=[descriptor, descriptor],
        )
        data_atom_bond, data_bond_angle = graph_pair[0]
        data_atom_bond.y = torch.tensor(
            [[float(record["t1"]) / 60.0 * speed, float(record["t2"]) / 60.0 * speed]],
            dtype=torch.float32,
        )
        graph_atom_bond_list.append(data_atom_bond)
        graph_bond_angle_list.append(data_bond_angle)

    total_num = len(graph_atom_bond_list)
    indices = np.arange(total_num)
    np.random.shuffle(indices)

    train_ratio = 0.8
    val_ratio = 0.1
    train_num = int(total_num * train_ratio)
    val_num = int(total_num * val_ratio)
    train_idx = indices[:train_num]
    valid_idx = indices[train_num:train_num + val_num]
    test_idx = indices[train_num + val_num:]

    train_atom_bond = [graph_atom_bond_list[i] for i in train_idx]
    valid_atom_bond = [graph_atom_bond_list[i] for i in valid_idx]
    test_atom_bond = [graph_atom_bond_list[i] for i in test_idx]
    train_bond_angle = [graph_bond_angle_list[i] for i in train_idx]
    valid_bond_angle = [graph_bond_angle_list[i] for i in valid_idx]
    test_bond_angle = [graph_bond_angle_list[i] for i in test_idx]

    train_loader_atom_bond = GeoDataLoader(train_atom_bond, batch_size=32, shuffle=False, num_workers=0)
    valid_loader_atom_bond = GeoDataLoader(valid_atom_bond, batch_size=32, shuffle=False, num_workers=0)
    test_loader_atom_bond = GeoDataLoader(test_atom_bond, batch_size=32, shuffle=False, num_workers=0)
    train_loader_bond_angle = GeoDataLoader(train_bond_angle, batch_size=32, shuffle=False, num_workers=0)
    valid_loader_bond_angle = GeoDataLoader(valid_bond_angle, batch_size=32, shuffle=False, num_workers=0)
    test_loader_bond_angle = GeoDataLoader(test_bond_angle, batch_size=32, shuffle=False, num_workers=0)

    nn_params = {
        'num_tasks': 6,
        'num_layers': 5,
        'emb_dim': 128,
        'drop_ratio': 0.0,
        'JK': "last",
        'graph_pooling': "sum",
        'descriptor_dim': 1827,
        'bond_float_names': CC_BOND_FLOAT_NAMES,
        'bond_angle_float_names': CC_BOND_ANGLE_FLOAT_NAMES,
        'use_relu_output': True,
    }

    model = GINGraphPooling(**nn_params).to(device)
    num_params = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {num_params}")

    optimizer = optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-5)
    scheduler = StepLR(optimizer, step_size=100, gamma=0.5)
    criterion_fn = torch.nn.MSELoss()

    def _quantile_loss(q_value, y_true, y_pred):
        error = y_true - y_pred
        return torch.mean(torch.maximum(q_value * error, (q_value - 1) * error))

    history = []
    best_state = None
    best_valid_loss = float("inf")
    best_epoch = 0

    for epoch in range(1, epochs + 1):
        model.train()
        loss_accum = 0.0
        step_count = 0

        for batch_atom_bond, batch_bond_angle in zip(train_loader_atom_bond, train_loader_bond_angle):
            batch_atom_bond = batch_atom_bond.to(device)
            batch_bond_angle = batch_bond_angle.to(device)
            pred = model(batch_atom_bond, batch_bond_angle)[0]
            true_1 = batch_atom_bond.y[:, 0]
            true_2 = batch_atom_bond.y[:, 1]

            optimizer.zero_grad()

            loss_1 = (
                _quantile_loss(0.1, true_1, pred[:, 0]) +
                criterion_fn(pred[:, 1], true_1) +
                _quantile_loss(0.9, true_1, pred[:, 2]) +
                torch.mean(torch.relu(pred[:, 0] - pred[:, 1])) +
                torch.mean(torch.relu(pred[:, 1] - pred[:, 2]))
            )
            loss_2 = (
                _quantile_loss(0.1, true_2, pred[:, 3]) +
                criterion_fn(pred[:, 4], true_2) +
                _quantile_loss(0.9, true_2, pred[:, 5]) +
                torch.mean(torch.relu(pred[:, 3] - pred[:, 4])) +
                torch.mean(torch.relu(pred[:, 4] - pred[:, 5]))
            )
            loss = loss_1 + loss_2 * 0.5
            loss.backward()
            optimizer.step()
            loss_accum += loss.detach().cpu().item()
            step_count += 1

        scheduler.step()
        avg_train_loss = loss_accum / max(step_count, 1)

        if epoch == 1 or epoch % 25 == 0 or epoch == epochs:
            model.eval()
            valid_loss_accum = 0.0
            valid_step = 0
            with torch.no_grad():
                for batch_atom_bond, batch_bond_angle in zip(valid_loader_atom_bond, valid_loader_bond_angle):
                    batch_atom_bond = batch_atom_bond.to(device)
                    batch_bond_angle = batch_bond_angle.to(device)
                    pred = model(batch_atom_bond, batch_bond_angle)[0]
                    true_1 = batch_atom_bond.y[:, 0]
                    true_2 = batch_atom_bond.y[:, 1]
                    v_loss = (
                        torch.mean((true_1 - pred[:, 1]) ** 2).item() +
                        torch.mean((true_2 - pred[:, 4]) ** 2).item() * 0.5
                    )
                    valid_loss_accum += v_loss
                    valid_step += 1

            avg_valid_loss = valid_loss_accum / max(valid_step, 1)
            row = {
                "epoch": epoch,
                "train_loss": avg_train_loss,
                "valid_loss": avg_valid_loss,
            }
            history.append(row)
            print(f"Epoch {epoch}: train_loss={avg_train_loss:.4f}, valid_loss={avg_valid_loss:.4f}")

            if avg_valid_loss < best_valid_loss:
                best_valid_loss = avg_valid_loss
                best_epoch = epoch
                best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}

    if best_state is None:
        best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}

    model.load_state_dict(best_state)
    model.eval()

    print(f"Best epoch: {best_epoch}, best valid loss: {best_valid_loss}")

    artifact = {
        "model_state_dict": best_state,
        "model_type": "cc_geognn",
        "source": "GINGraphPooling GeoGNN for CC retention time prediction",
        "nn_params": nn_params,
        "num_model_params": num_params,
        "best_epoch": best_epoch,
        "best_valid_loss": best_valid_loss,
        "target_unit": "minutes",
        "seed": seed,
    }

    project_model = record_dir / "best_cc_geognn_model.pth"
    torch.save(artifact, project_model)
    shutil.copy2(project_model, CC_TORCH_MODEL_PATH)
    write_history_csv(record_dir / "training_history.csv", history)
    (record_dir / "summary.json").write_text(
        json.dumps({k: v for k, v in artifact.items() if k not in {"model_state_dict", "nn_params"}}, indent=2, default=str),
        encoding="utf-8",
    )

    return {
        "project_model": str(project_model),
        "services_model": str(CC_TORCH_MODEL_PATH),
        "record_dir": str(record_dir),
        "best_epoch": best_epoch,
        "best_valid_loss": best_valid_loss,
        "num_params": num_params,
    }


def copy_hplc() -> None:
    source_model = HPLC_PROJECT / "application" / "GeoGNN_model.pth"
    source_descriptor = HPLC_PROJECT / "application" / "column_descriptor.npy"
    if source_model.exists():
        shutil.copy2(source_model, HPLC_MODEL_PATH)
    if source_descriptor.exists():
        shutil.copy2(source_descriptor, HPLC_COLUMN_DESCRIPTOR_PATH)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--seed", type=int, default=324)
    parser.add_argument("--skip-hplc", action="store_true")
    parser.add_argument("--device", type=str, default=None)
    args = parser.parse_args()

    set_seed(args.seed)
    if args.device:
        device = torch.device(args.device)
    else:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    results = {
        "device": str(device),
        "tlc": train_tlc_ann(args.epochs, args.seed, device),
        "cc": train_cc_geognn(args.epochs, args.seed, device),
    }
    if not args.skip_hplc:
        copy_hplc()
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
