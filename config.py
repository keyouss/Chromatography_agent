"""
配置管理模块 - 集中管理项目的所有配置项
从 .env 文件或系统环境变量中读取配置
"""

import os
from dotenv import load_dotenv

# 从当前目录加载 .env 文件（如果存在）
# .env 文件中的变量会覆盖系统环境变量
load_dotenv()


class Config:
    """
    项目配置类 - 集中管理所有配置项

    所有配置项都有默认值，不配置 .env 文件也能运行（但API密钥为空时无法调用LLM）。
    配置优先级: 系统环境变量 > .env 文件 > 代码默认值。
    """

    # ==================== LLM配置 ====================
    # 支持的LLM类型: openai, qianfan(百度), dashscope(阿里), zhipu(智谱), moonshot(月之暗面), siliconflow
    LLM_TYPE = os.getenv("LLM_TYPE", "siliconflow")

    # ---------- SiliconFlow配置（优先使用） ----------
    # SiliconFlow 提供 DeepSeek 等开源模型的 API 服务
    # API_KEY 是通用环境变量名，与 OPENAI_API_KEY 格式兼容
    SILICONFLOW_API_KEY = os.getenv("API_KEY", "") or os.getenv("SILICONFLOW_API_KEY", "")
    # API 基础地址，兼容 OpenAI 格式
    SILICONFLOW_API_BASE = os.getenv("BASE_URL", "https://api.siliconflow.cn/v1")
    # 使用的模型 ID，默认为 DeepSeek 最新版本
    SILICONFLOW_MODEL_ID = os.getenv("MODEL_ID", "deepseek-ai/DeepSeek-V4-Flash")

    # ---------- OpenAI 兼容配置 ----------
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.nuwaapi.com/v1")
    # 通用模型ID，可被其他提供商复用
    MODEL_ID = os.getenv("MODEL_ID", "")

    # ---------- 百度千帆配置 ----------
    QIANFAN_AK = os.getenv("QIANFAN_AK", "")
    QIANFAN_SK = os.getenv("QIANFAN_SK", "")

    # ---------- 阿里 DashScope 配置 ----------
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

    # ---------- 智谱AI配置 ----------
    ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")

    # ---------- 月之暗面 Moonshot (Kimi) 配置 ----------
    MOONSHOT_API_KEY = os.getenv("MOONSHOT_API_KEY", "")

    # ==================== 搜索API配置 ====================
    # Tavily搜索API：用于网络搜索增强（Agent可调用搜索工具获取实时信息）
    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
    # SerpAPI：Google搜索的API接口
    SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")

    # ==================== 色谱预测服务API配置 ====================
    # TLC（薄层色谱）Rf值预测服务地址
    # 由 Services/tlc_server.py 提供
    TLC_API_URL = os.getenv("TLC_API_URL", "http://127.0.0.1:8000/predict")

    # CC（柱色谱）分离预测服务地址
    # 由 Services/cc_server.py 提供
    CC_API_URL = os.getenv("CC_API_URL", "http://127.0.0.1:8001/predict")

    # HPLC（高效液相色谱）保留时间预测服务地址
    # 由 Services/hplc_server.py 提供
    HPLC_API_URL = os.getenv("HPLC_API_URL", "http://127.0.0.1:8002/predict")

    # ==================== Agent运行配置 ====================
    # Agent最大迭代次数：防止工具调用进入死循环
    AGENT_MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", "5"))
    # 对话记忆保留轮数：最近几轮对话会被保留在上下文中
    AGENT_MEMORY_K = int(os.getenv("AGENT_MEMORY_K", "5"))
    # LLM温度参数：0=确定性输出，越大越随机
    AGENT_TEMPERATURE = float(os.getenv("AGENT_TEMPERATURE", "0"))

    # ==================== Gradio Web界面配置 ====================
    GRADIO_SERVER_NAME = os.getenv("GRADIO_SERVER_NAME", "127.0.0.1")
    GRADIO_SERVER_PORT = int(os.getenv("GRADIO_SERVER_PORT", "7860"))
    GRADIO_SHARE = bool(os.getenv("GRADIO_SHARE", "false").lower() == "true")


# 导出单例配置实例
# 其他模块统一通过 `from config import config` 获取配置
config = Config()