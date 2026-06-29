"""
HPLC预测服务 - 高效液相色谱保留时间预测 API
基于 FastAPI 框架，提供RESTful接口
预测手性化合物在各候选色谱柱中的分离概率和保留时间
"""

from __future__ import annotations

import contextlib
import io
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from model_service_utils import (
    HPLC_COLUMN_DESCRIPTOR_PATH,
    HPLC_MODEL_PATH,
    _HAS_TORCH_GEOMETRIC,
    _HAS_MORDRED,
    hplc_surrogate_prediction,
    hplc_predict_with_geognn,
    require_file,
)

# 条件导入 GINGraphPooling（仅在 torch_geometric 可用时）
if _HAS_TORCH_GEOMETRIC:
    from model_service_utils import GINGraphPooling

# ==================== FastAPI 应用 ====================
app = FastAPI(title="HPLC Retention Time Prediction Service", version="2.0")

# 全局状态：模型和柱描述符的加载状态
_model_loaded = False           # 模型是否已加载
_model_obj = None               # GINGraphPooling 模型实例
_column_descriptor = None       # 色谱柱描述符数组
_load_error = None              # 模型加载错误信息（如有）
_surrogate_only = False         # 是否仅使用替代预测
INFERENCE_DEVICE = "cpu"        # 服务端默认使用 CPU，避免 CUDA/CPU 张量混用


# ==================== 数据模型 ====================
class HPLCRequest(BaseModel):
    """HPLC预测请求的数据结构"""
    smiles: str           # 两个化合物的 SMILES，用逗号分隔
    eluent: float         # 流动相比例（如 0.1 = 10% 异丙醇）
    speed: float          # 流速（mL/min）
    columns: str          # 候选色谱柱名称，逗号分隔（如 "ADH,IA"）
    device: int = 0       # 设备编号（默认为0）


# ==================== 模型与数据加载 ====================
def load_artifacts() -> None:
    """
    加载 HPLC 预测所需的模型和色谱柱描述符

    优先尝试加载 GeoGNN 真实模型；若 torch_geometric 或 mordred 不可用则回退到替代预测。

    加载内容：
    1. HPLC 预训练 GeoGNN 模型 (.pth)
    2. 色谱柱描述符 (npy 文件)
    """
    global _model_loaded, _model_obj, _column_descriptor
    global _load_error, _surrogate_only

    # 检查文件是否存在
    require_file(HPLC_MODEL_PATH, "HPLC model")
    require_file(HPLC_COLUMN_DESCRIPTOR_PATH, "HPLC column descriptor")

    if not _model_loaded:
        if _HAS_TORCH_GEOMETRIC and _HAS_MORDRED:
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    import torch
                    _model_obj = GINGraphPooling(
                        num_tasks=3, num_layers=5, emb_dim=128,
                        drop_ratio=0, graph_pooling="sum",
                        descriptor_dim=1827)
                    state_dict = torch.load(
                        HPLC_MODEL_PATH, map_location="cpu",
                        weights_only=True)
                    # ── 处理 AtomEncoder embedding 维度不匹配 ──
                    # checkpoint 是旧版本 CompoundKit 训练的，部分 atom vocab
                    # 大小与当前环境不同。对于 size mismatch 的权重，将
                    # checkpoint 中较小的矩阵拷贝到模型较大的矩阵中（新词条
                    # 保留随机初始化，作为 OOV 处理）。
                    model_state = _model_obj.state_dict()
                    for key in list(state_dict.keys()):
                        ckpt_w = state_dict[key]
                        model_w = model_state.get(key)
                        if model_w is not None and model_w.shape != ckpt_w.shape:
                            # 逐维度拷贝，取两者中较小的尺寸
                            slices = tuple(
                                slice(0, min(ms, cs))
                                for ms, cs in zip(model_w.shape, ckpt_w.shape))
                            model_w[slices] = ckpt_w[slices]
                            del state_dict[key]  # 已手动处理，从 checkpoint 移除
                    _model_obj.load_state_dict(state_dict, strict=False)
                    _model_obj.to(INFERENCE_DEVICE)
                    _model_obj.eval()
                _surrogate_only = False
            except Exception as exc:
                print(exc)
                _load_error = str(exc)
                _surrogate_only = True
        else:
            _load_error = (
                f"GeoGNN 模型不可用（"
                f"torch_geometric={'OK' if _HAS_TORCH_GEOMETRIC else '缺失'}, "
                f"mordred={'OK' if _HAS_MORDRED else '缺失'}），"
                f"将使用替代预测。")
            _surrogate_only = True
        _model_loaded = True

    if _column_descriptor is None:
        _column_descriptor = np.load(
            HPLC_COLUMN_DESCRIPTOR_PATH, allow_pickle=True)


@app.on_event("startup")
def startup_load_model() -> None:
    """应用启动时自动加载模型和数据"""
    load_artifacts()


# ==================== 健康检查接口 ====================
@app.get("/health")
def health():
    """健康检查接口"""
    try:
        load_artifacts()
        return {
            "ok": True,
            "model": str(HPLC_MODEL_PATH),
            "column_descriptor": str(HPLC_COLUMN_DESCRIPTOR_PATH),
            "surrogate_only": _surrogate_only,          # 是否仅使用替代预测
            "load_warning": _load_error,                # 加载告警（如有）
            "torch_geometric": _HAS_TORCH_GEOMETRIC,
            "mordred": _HAS_MORDRED,
            "inference_device": INFERENCE_DEVICE,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ==================== 预测接口 ====================
@app.post("/predict")
def predict(request: HPLCRequest):
    """
    HPLC 保留时间预测接口

    对于给定的分子对和候选色谱柱，预测：
    - 各色谱柱的分离概率 (Sp)
    - 两个化合物的保留时间 (RTs)
    - 推荐最佳色谱柱

    Args:
        request: 包含 SMILES 对、流动相条件、候选色谱柱等的请求

    Returns:
        包含各色谱柱的 Sp 值、RT 值、推荐色谱柱的响应

    Raises:
        503: 模型文件未找到
        400: 输入参数错误（如未提供色谱柱）
        500: 预测过程其他错误
    """
    try:
        # 加载模型和柱描述符
        load_artifacts()

        # 解析色谱柱列表（按逗号分割，去除首尾空格）
        column_list = [c.strip() for c in request.columns.split(",") if c.strip()]
        if not column_list:
            raise ValueError("至少需要提供一个候选色谱柱。")

        # 调用预测函数：优先使用真实 GeoGNN 模型，不可用时回退到替代预测
        if not _surrogate_only and _model_obj is not None:
            sp_list, rt_list = hplc_predict_with_geognn(
                _model_obj,
                _column_descriptor,
                request.smiles,
                request.eluent,
                request.speed,
                column_list,
                device=INFERENCE_DEVICE,
            )
            model_used = "GeoGNN"
        else:
            sp_list, rt_list = hplc_surrogate_prediction(
                request.smiles,
                request.eluent,
                request.speed,
                column_list,
            )
            model_used = "surrogate"

        # 找到分离概率最大的色谱柱作为推荐
        max_index = int(np.argmax(sp_list))

        return {
            "success": True,
            "smiles": request.smiles,
            "eluent": request.eluent,
            "speed": request.speed,
            "columns": column_list,                    # 候选色谱柱列表
            "sp_list": [round(float(s), 4) for s in sp_list],          # 各色谱柱的分离概率
            "rt_list": [[round(float(v), 3) for v in pair] for pair in rt_list],  # 各色谱柱的保留时间对
            "max_sp": round(float(sp_list[max_index]), 4),   # 最高分离概率
            "recommend": column_list[max_index],              # 推荐的最佳色谱柱
            "plot_path": "",                                  # 图表路径（当前未使用）
            "model_used": model_used,
            "surrogate_only": _surrogate_only,
        }

    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    

load_artifacts()
