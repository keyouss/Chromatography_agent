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
            
            # 构建输出结果
            output_parts = [
                f"根据预测，对于 SMILES '{smiles}'，在流动相比例 {eluent} 和流速 {speed} 的条件下：",
                f"**推荐的最佳色谱柱是: {result['recommend']}**，其分离概率(Sp)为: {result['max_sp']:.4f}。",
                "\n详细预测结果如下:",
            ]
            
            # 遍历所有色谱柱，输出各自的预测结果
            for i, col_name in enumerate(result['columns']):
                sp_value = result['sp_list'][i]  # 分离概率
                rt_values = result['rt_list'][i]  # 两个化合物的保留时间
                output_parts.append(f"- 色谱柱 '{col_name}': 分离概率 (Sp) = {sp_value:.4f}, 保留时间 (RTs) = {rt_values[0]:.2f}, {rt_values[1]:.2f}")
            
            # 添加图表保存路径信息
            output_parts.append(f"\n结果图表已保存在服务器路径: {result['plot_path']}")
            
            return "\n".join(output_parts)
        
        # 处理网络连接错误
        except requests.exceptions.RequestException as e:
            return f"API请求失败 ({api_url}): {e}"
        # 处理JSON解析错误
        except json.JSONDecodeError:
            return "API返回的不是有效的JSON格式。"
        # 处理其他未知错误
        except Exception as e:
            return f"处理API响应时发生未知错误: {e}"
    
    async def _arun(self, smiles: str, eluent: float, speed: float, columns: str, device: int = 0) -> str:
        """异步执行方法，直接调用同步方法"""
        return self._run(smiles, eluent, speed, columns, device)
