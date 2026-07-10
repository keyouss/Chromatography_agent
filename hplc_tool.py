"""
HPLC预测工具 - 高效液相色谱保留时间预测
适配 LangChain v1.x (langchain_core)
"""

from langchain_core.tools import BaseTool
from typing import Type
from pydantic import BaseModel, Field
import requests
import json
from config import config
from tool_response import tool_response


class HPLCInputSchema(BaseModel):
    """
    HPLC预测工具的输入参数模型
    使用Pydantic进行参数验证和描述
    """
    # 两个分子的SMILES字符串，用英文逗号分隔（通常用于手性分离）
    smiles: str = Field(description="Two molecule SMILES separated by one English comma.")
    # 流动相比例，通常为正己烷/异丙醇的比例，如0.1表示10%异丙醇
    eluent: float = Field(description="Mobile phase ratio as a decimal, e.g. 0.1 or 0.2.")
    # 流速，单位mL/min
    speed: float = Field(description="Flow rate in mL/min, e.g. 1.0.")
    # 候选色谱柱名称，多个色谱柱用英文逗号分隔
    columns: str = Field(description="Candidate HPLC/chiral column names separated by commas, e.g. 'ADH,IA,OJH'.")


class PredictHPLCValue(BaseTool):
    """
    HPLC预测工具类
    继承自LangChain的BaseTool，可被Agent调用
    用于预测手性化合物在HPLC中的保留时间和分离概率
    """
    # 工具名称，Agent通过此名称调用工具
    name: str = "HPLC_Retention_Time_Predictor"
    # 工具描述，LLM根据此描述判断何时使用该工具
    description: str = (
        "HPLC/chiral column retention-time predictor. Use for HPLC, chiral separation, "
        "retention time, Sp, or column names such as ADH, IA, IB, IC, OD, ODH, OJ, OJH. "
        "Required args: smiles (two comma-separated SMILES), eluent, speed, columns."
    )
    # 输入参数的Schema，用于参数验证和自动生成文档
    args_schema: Type[BaseModel] = HPLCInputSchema
    
    def _run(self, smiles: str, eluent: float, speed: float, columns: str, device: int = 0) -> str:
        """
        执行HPLC预测工具
        
        Args:
            smiles: 两个分子的SMILES字符串，用逗号分隔
            eluent: 流动相比例
            speed: 流速
            columns: 候选色谱柱名称列表
            device: 设备编号
            
        Returns:
            预测结果字符串，包含推荐色谱柱和详细数据
        """
        # 从配置获取API地址
        api_url = config.HPLC_API_URL
        
        # 设置请求头，指定JSON格式
        headers = {"Content-Type": "application/json"}
        
        # 构建API请求载荷
        payload = {
            "smiles": smiles,
            "eluent": eluent,
            "speed": speed,
            "columns": columns,
            "device": device
        }
        
        # 发送POST请求到预测API
        try:
            # 设置超时为300秒，因为HPLC预测可能耗时较长
            response = requests.post(api_url, headers=headers, data=json.dumps(payload), timeout=300)
            response.raise_for_status()  # 检查HTTP错误
            result = response.json()
            
            column_results = []
            for i, col_name in enumerate(result["columns"]):
                column_results.append({
                    "column": col_name,
                    "sp": result["sp_list"][i],
                    "retention_times": result["rt_list"][i],
                })

            return tool_response(
                ok=bool(result.get("success", True)),
                tool=self.name,
                input_data={
                    "smiles": smiles,
                    "eluent": eluent,
                    "speed": speed,
                    "columns": columns,
                    "device": device,
                },
                result={
                    "smiles": result.get("smiles", smiles),
                    "eluent": result.get("eluent", eluent),
                    "speed": result.get("speed", speed),
                    "recommend": result.get("recommend"),
                    "max_sp": result.get("max_sp"),
                    "columns": column_results,
                    "model_used": result.get("model_used", ""),
                    "surrogate_only": result.get("surrogate_only", False),
                    "plot_path": result.get("plot_path", ""),
                },
                summary=(
                    "HPLC prediction succeeded. "
                    f"Recommended column={result.get('recommend')}, "
                    f"max Sp={result.get('max_sp')}."
                ),
                warnings=["HPLC service is using surrogate prediction."] if result.get("surrogate_only") else [],
            )
        
        # 处理网络连接错误
        except requests.exceptions.RequestException as e:
            return tool_response(
                ok=False,
                tool=self.name,
                input_data={"smiles": smiles, "eluent": eluent, "speed": speed, "columns": columns, "device": device},
                error=f"Could not connect to the HPLC prediction API ({api_url}): {e}",
            )
        # 处理JSON解析错误
        except json.JSONDecodeError:
            return tool_response(
                ok=False,
                tool=self.name,
                input_data={"smiles": smiles, "eluent": eluent, "speed": speed, "columns": columns, "device": device},
                error="HPLC API did not return valid JSON.",
            )
        # 处理其他未知错误
        except Exception as e:
            return tool_response(
                ok=False,
                tool=self.name,
                input_data={"smiles": smiles, "eluent": eluent, "speed": speed, "columns": columns, "device": device},
                error=f"Unexpected HPLC tool error: {e}",
            )
    
    async def _arun(self, smiles: str, eluent: float, speed: float, columns: str, device: int = 0) -> str:
        """异步执行方法，直接调用同步方法"""
        return self._run(smiles, eluent, speed, columns, device)
