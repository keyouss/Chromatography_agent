"""
TLC预测工具 - 薄层色谱Rf值预测
适配 LangChain v1.x (langchain_core)
"""

import json
import requests
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type, List, Optional
import re
from config import config


class PredictTLCInput(BaseModel):
    """
    TLC预测工具的输入参数模型
    使用Pydantic进行参数验证和描述
    """
    # 化合物的SMILES字符串表示
    smiles: str = Field(description="Single compound SMILES.")
    # 洗脱剂系统描述，如 "Hexane/Ethyl Acetate 9:1"
    eluent_system: str = Field(description="TLC eluent and ratio, e.g. 'Hexane/Ethyl Acetate 49:1'.")


class PredictTLCValue(BaseTool):
    """
    TLC预测工具类
    继承自LangChain的BaseTool，可被Agent调用
    """
    # 工具名称，Agent通过此名称调用工具
    name: str = "TLC_Predictor"
    # 工具描述，LLM根据此描述判断何时使用该工具
    description: str = (
        "TLC Rf predictor. Use for thin-layer chromatography Rf questions. "
        "Required args: smiles, eluent_system."
    )
    # 输入参数的Schema，用于参数验证和自动生成文档
    args_schema: Type[BaseModel] = PredictTLCInput
    
    def _parse_eluent_system(self, eluent_system: str) -> List[float]:
        """
        解析洗脱剂系统字符串，转换为5个溶剂的比例列表
        
        溶剂索引映射:
        - 索引0: 己烷 (Hexane) - 非极性
        - 索引1: 乙酸乙酯 (Ethyl Acetate) - 中等极性
        - 索引2: 二氯甲烷 (DCM) - 中等极性
        - 索引3: 甲醇 (Methanol) - 高极性
        - 索引4: 乙醚 (Ether) - 低极性
        
        Args:
            eluent_system: 洗脱剂系统字符串，如 "Hexane/Ethyl Acetate 49:1"
            
        Returns:
            长度为5的浮点数列表，表示各溶剂的比例
        """
        # 溶剂名称到索引的映射（支持中英文）
        eluent_map = {
            'ethylacetate': 1, '乙酸乙酯': 1,
            'hexane': 0, 'hex': 0, '己烷': 0,
            'dcm': 2, 'dichloromethane': 2, '二氯甲烷': 2,
            'methanol': 3, 'meoh': 3, '甲醇': 3,
            'ether': 4, 'et2o': 4, '乙醚': 4
        }
        
        system_lower = eluent_system.lower()
        
        # 使用正则表达式提取所有数字（比例值）
        ratios_str = re.findall(r'(\d+\.?\d*)', system_lower)
        ratios = [float(x) for x in ratios_str]
        
        # 如果没有找到比例值，抛出异常
        if not ratios:
            raise ValueError(f"Could not find any ratios in the eluent system string: '{eluent_system}'")
        
        # 移除数字和分隔符，提取溶剂名称部分
        solvent_part = re.sub(r'[\d\s:./]+', '', system_lower).strip()
        found_solvents = []
        temp_solvent_part = solvent_part
        
        # 按长度降序排列溶剂键，优先匹配长名称（如 ethylacetate 优于 eth）
        sorted_eluent_keys = sorted(eluent_map.keys(), key=len, reverse=True)
        
        # 逐个识别溶剂名称
        while temp_solvent_part:
            found_match = False
            for key in sorted_eluent_keys:
                if temp_solvent_part.startswith(key):
                    found_solvents.append(key)
                    temp_solvent_part = temp_solvent_part[len(key):]
                    found_match = True
                    break
            if not found_match:
                if temp_solvent_part:
                    raise ValueError(f"Unknown solvent starting with '{temp_solvent_part}' in eluent system.")
                break
        
        # 验证溶剂数量与比例数量是否匹配
        if len(ratios) != len(found_solvents):
            raise ValueError(f"Mismatch between number of solvents and ratios found in '{eluent_system}'. Found {len(found_solvents)} solvents and {len(ratios)} ratios.")
        
        # 计算总比例，用于归一化
        total_ratio = sum(ratios)
        if total_ratio == 0:
            raise ValueError("Total ratio cannot be zero.")
        
        # 初始化5个溶剂的比例数组
        final_ratios = [0.0] * 5
        
        # 将各溶剂比例归一化后填入对应位置
        for i, solvent_name in enumerate(found_solvents):
            mapped_index = eluent_map[solvent_name]
            final_ratios[mapped_index] = ratios[i] / total_ratio
        
        return final_ratios
    
    def _run(self, smiles: str, eluent_system: str):
        """
        执行TLC预测工具
        
        Args:
            smiles: 化合物的SMILES字符串
            eluent_system: 洗脱剂系统描述
            
        Returns:
            预测结果字符串
        """
        # 从配置获取API地址
        api_url = config.TLC_API_URL
        
        # 解析洗脱剂系统字符串
        try:
            eluent_ratios_list = self._parse_eluent_system(eluent_system)
        except ValueError as e:
            return f"Error parsing eluent system: {e}. Please provide a valid eluent string like 'Hexane/EtOAc 9:1'."
        
        # 构建API请求载荷
        payload = {
            "smiles": smiles,
            "eluent_ratios": eluent_ratios_list,
        }
        
        # 发送POST请求到预测API
        try:
            response = requests.post(api_url, json=payload, timeout=60)
            response.raise_for_status()  # 检查HTTP错误
            result = response.json()
            
            # 返回格式化的预测结果
            return (
                f"Successfully predicted the Rf value for SMILES '{result['smiles']}' "
                f"with eluent system '{eluent_system}'. "
                f"The predicted Rf value is: {result['predicted_rf']:.4f}"
            )
        
        # 处理网络连接错误
        except requests.exceptions.RequestException as e:
            return f"Error: Could not connect to the TLC prediction API ({api_url}). Please ensure the API server is running. Details: {e}"
        # 处理其他未知错误
        except Exception as e:
            return f"An unexpected error occurred while using the TLC tool: {e}"
    
    def _arun(self, *args, **kwargs):
        """异步执行方法（暂未实现）"""
        raise NotImplementedError("Async operation not supported yet")
