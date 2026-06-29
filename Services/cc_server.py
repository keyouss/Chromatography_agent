"""
CC预测服务 - 柱色谱分离预测 API
基于 FastAPI 框架，提供RESTful接口
预测两个化合物在柱色谱中的分离效果和保留时间
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from model_service_utils import (
    CC_TORCH_MODEL_PATH,
    cc_predict_with_geognn,
    require_file,
    _cc_load_geognn_model,
)


app = FastAPI(title="Column Chromatography Prediction Service", version="2.2")

_geognn_model = None


class CCRequest(BaseModel):
    smiles_list: list[str]
    eluent_system: str            # 洗脱剂系统描述，如 "PE/EA 90/10"
    m_param: float                # 固定相质量（克），如 4.0
    speed: float                  # 流速（mL/min），如 3.0
    V_e: Optional[float] = None   # 柱体积参数（GeoGNN 模型用）
    model_path: Optional[str] = None


def _parse_eluent_system(eluent_system: str):
    parts = eluent_system.strip().split()
    if len(parts) >= 1:
        solvent_part = parts[0]
        if '/' in solvent_part:
            first_solvent = solvent_part.split('/')[0]
        else:
            first_solvent = "PE"
    else:
        first_solvent = "PE"

    if len(parts) >= 2:
        ratio = parts[1]
    else:
        parts2 = eluent_system.strip().split('/')
        if len(parts2) >= 3:
            ratio = f"{parts2[1]}/{parts2[2]}"
        else:
            ratio = "50/50"

    return first_solvent, ratio


def load_artifact():
    global _geognn_model
    require_file(CC_TORCH_MODEL_PATH, "CC GeoGNN model")
    if _geognn_model is None:
        _geognn_model = _cc_load_geognn_model("cpu")
    return {
        "model_type": "cc_geognn",
        "model_path": str(CC_TORCH_MODEL_PATH),
        "model_name": "cc_geognn",
    }


@app.on_event("startup")
def startup_load_model() -> None:
    load_artifact()


# ==================== 健康检查接口 ====================
@app.get("/health")
def health():
    """健康检查接口"""
    try:
        artifact = load_artifact()
        return {
            "ok": True,
            "model": artifact.get("model_path", str(CC_TORCH_MODEL_PATH)),
            "model_name": artifact.get("model_name", "unknown"),
            "model_type": artifact.get("model_type", "unknown"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/predict")
def predict(request: CCRequest):
    try:
        artifact = load_artifact()

        smiles = ",".join(request.smiles_list)
        e, eluent = _parse_eluent_system(request.eluent_system)
        V_e = request.V_e if request.V_e is not None else 40
        sp, rt_list = cc_predict_with_geognn(
            smiles,
            eluent,
            e=e,
            m=request.m_param,
            V_e=V_e,
            device="cpu",
            model=_geognn_model,
        )
        rt1, rt2 = rt_list[0], rt_list[1]
        separation_factor = max(rt1, rt2) / max(min(rt1, rt2), 1e-6)
        return {
            "success": True,
            "compound1": request.smiles_list[0],
            "compound2": request.smiles_list[1],
            "eluent_system": request.eluent_system,
            "separation_factor": round(separation_factor, 3),
            "peak1_retention_time": round(rt1, 3),
            "peak2_retention_time": round(rt2, 3),
            "separation_probability": round(sp, 4),
            "prediction": "良好分离" if separation_factor >= 1.5 else "部分分离",
            "model_used": artifact.get("model_path", str(CC_TORCH_MODEL_PATH)),
            "ignored_model_path": request.model_path,
        }

    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
