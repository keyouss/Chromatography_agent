"""
CC预测工具 - 柱色谱分离预测
适配 LangChain v1.x (langchain_core)
"""

import requests
import json
from langchain_core.tools import BaseTool
from typing import Type
from pydantic import BaseModel, Field
from config import config


class PredictCCInput(BaseModel):
    """
    CC预测工具的输入参数模型
    使用Pydantic进行参数验证和描述
    """
    # 第一个化合物的SMILES字符串
    smiles_compound1: str = Field(description="First compound SMILES.")
    # 第二个化合物的SMILES字符串
    smiles_compound2: str = Field(description="Second compound SMILES.")
    # 洗脱剂系统及比例，如 "PE/EA 90/10" 表示石油醚/乙酸乙酯 9:1
    eluent_system: str = Field(description="Column eluent and ratio, e.g. 'PE/EA 90/10'.")
    # 固定相质量（硅胶柱质量），单位克，如4.0表示4克硅胶柱
    m_param: float = Field(description="Silica/stationary phase mass in grams, e.g. 4.0.")
    # 流速，单位mL/min
    speed: float = Field(description="Flow rate in mL/min, e.g. 8.0.")
    # 训练好的模型文件路径
    model_path: str = Field(
        description="Optional model path. The service uses the best model copied to Services by default.",
        default="",
    )


class PredictCCValue(BaseTool):
    """
    CC预测工具类
    继承自LangChain的BaseTool，可被Agent调用
    用于预测两个化合物在柱色谱中的分离效果
    """
    # 工具名称，Agent通过此名称调用工具
    name: str = "CC_Predictor"
    # 工具描述，LLM根据此描述判断何时使用该工具
    description: str = (
        "Column chromatography separation predictor for two compounds. "
        "Use for CC/column/silica column separation questions. "
        "Required args: smiles_compound1, smiles_compound2, eluent_system, m_param, speed."
    )
    # 输入参数的Schema，用于参数验证和自动生成文档
    args_schema: Type[BaseModel] = PredictCCInput
    
    def _run(self, smiles_compound1: str, smiles_compound2: str, eluent_system: str, m_param: float, speed: float, model_path: str = ""):
        """
        执行CC预测工具
        
        Args:
            smiles_compound1: 第一个化合物的SMILES字符串
            smiles_compound2: 第二个化合物的SMILES字符串
            eluent_system: 洗脱剂系统描述
            m_param: 硅胶柱质量（克）
            speed: 流速
            model_path: 模型文件路径
            
        Returns:
            预测结果字符串，包含分离因子和保留时间等信息
        """
        # 从配置获取API地址
        api_url = config.CC_API_URL
        
        try:
            # 构建API请求载荷
            payload = {
                "smiles_list": [smiles_compound1, smiles_compound2],  # 两个化合物的SMILES列表
                "eluent_system": eluent_system,
                "m_param": m_param,
                "speed": speed,
                "model_path": model_path
            }
            
            # 发送POST请求到预测API
            response = requests.post(api_url, json=payload, timeout=60)
            response.raise_for_status()  # 检查HTTP错误
            result = response.json()
            
            # 返回格式化的预测结果
            return f"Prediction successful: {json.dumps(result, ensure_ascii=False, indent=2)}"
        
        # 处理网络连接错误
        except requests.exceptions.RequestException as e:
            return f"An error occurred while calling the CC API ({api_url}): {e}"
        # 处理其他未知错误
        except Exception as e:
            return f"An unexpected error occurred: {e}"
    
    def _arun(self, smiles_compound1: str, smiles_compound2: str, eluent_system: str, m_param: float, speed: float, model_path: str = ""):
        """异步执行方法，直接调用同步方法"""
        return self._run(smiles_compound1, smiles_compound2, eluent_system, m_param, speed, model_path)
