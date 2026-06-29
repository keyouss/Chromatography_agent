"""
TLC预测服务 - 薄层色谱 Rf 值预测 API
基于 FastAPI 框架，提供RESTful接口
支持 scikit-learn 和 PyTorch 两种模型格式
"""

from __future__ import annotations

import joblib
import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from model_service_utils import TLC_MODEL_PATH, TLC_TORCH_MODEL_PATH, require_file, tlc_features
from model_service_utils import TLCANN


# ==================== FastAPI 应用 ====================
app = FastAPI(title="TLC Rf Prediction Service", version="2.0")

# 全局缓存：已加载的模型工件（避免每次请求都重新加载）
_artifact = None


# ==================== 数据模型 ====================
class TLCRequest(BaseModel):
    """TLC预测请求的数据结构"""
    smiles: str           # 化合物的 SMILES 字符串
    eluent_ratios: list[float]  # 5种溶剂的比例列表 [己烷, 乙酸乙酯, DCM, 甲醇, 乙醚]


class TLCResponse(BaseModel):
    """TLC预测响应的数据结构"""
    smiles: str           # 输入化合物的 SMILES
    predicted_rf: float   # 预测的 Rf 值（0~1之间）
    model_used: str       # 使用的模型文件路径


# ==================== 模型加载 ====================
def load_artifact():
    """
    加载训练好的 TLC 预测模型

    支持两种模型格式：
    1. PyTorch 模型 (.pth) - 优先加载
    2. joblib/scikit-learn 模型 (.joblib) - 备选

    模型工件包含：模型权重、特征均值/标准差等预处理参数
    """
    global _artifact
    if _artifact is None:
        # 优先加载 PyTorch 模型
        if TLC_TORCH_MODEL_PATH.exists():
            # 加载 PyTorch 模型工件
            artifact = torch.load(TLC_TORCH_MODEL_PATH, map_location="cpu")

            # 根据保存的输入维度创建ANN模型实例
            model = TLCANN(
                in_features=int(artifact["input_dim"]),
                hidden_features=int(artifact.get("hidden_dim", 128)),
            )
            # 加载训练好的模型权重
            model.load_state_dict(artifact["state_dict"])
            model.eval()
            artifact["model"] = model
            artifact["model_name"] = artifact.get("model_type", "tlc_ann")
            artifact["model_path"] = str(TLC_TORCH_MODEL_PATH)
            _artifact = artifact
        else:
            # 备选：加载 scikit-learn 模型
            require_file(TLC_MODEL_PATH, "TLC model")
            _artifact = joblib.load(TLC_MODEL_PATH)
            _artifact["model_path"] = str(TLC_MODEL_PATH)
    return _artifact


@app.on_event("startup")
def startup_load_model() -> None:
    """应用启动时自动加载模型，预热服务"""
    load_artifact()


# ==================== 健康检查接口 ====================
@app.get("/health")
def health():
    """
    健康检查接口
    UI_app.py 通过此接口判断服务是否就绪
    """
    try:
        artifact = load_artifact()
        return {
            "ok": True,
            "model": artifact.get("model_path", str(TLC_MODEL_PATH)),
            "model_name": artifact.get("model_name", "unknown"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ==================== 预测接口 ====================
@app.post("/predict", response_model=TLCResponse)
def predict(request: TLCRequest):
    """
    TLC Rf 值预测接口

    Args:
        request: 包含 SMILES 和洗脱剂比例的请求

    Returns:
        预测的 Rf 值（0~1 之间的浮点数）

    Raises:
        503: 模型文件未找到
        400: 输入参数错误
        500: 预测过程其他错误
    """
    try:
        # 加载模型
        artifact = load_artifact()

        # 提取特征：将 SMILES 和洗脱剂比例转换为模型输入
        features = tlc_features(request.smiles, request.eluent_ratios)

        # ===== 使用 PyTorch ANN 模型进行预测 =====
        if artifact.get("model_type") == "tlc_ann":
            # 标准化特征（减去均值除以标准差）
            x = ((features.reshape(-1) - artifact["feature_mean"]) / artifact["feature_std"]).astype(np.float32)
            with torch.no_grad():
                value = float(artifact["model"](torch.tensor(x.reshape(1, -1))).numpy()[0, 0])

        # ===== 使用 scikit-learn 模型进行预测 =====
        else:
            value = float(artifact["model"].predict(features)[0])

        # 确保 Rf 值在合理范围 [0, 1] 内
        value = max(0.0, min(1.0, value))

        return TLCResponse(
            smiles=request.smiles,
            predicted_rf=round(value, 4),
            model_used=artifact.get("model_path", str(TLC_MODEL_PATH)),
        )

    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))