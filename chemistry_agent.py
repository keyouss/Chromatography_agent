"""
化学智能助手核心模块
管理 LLM、工具和 Agent 的初始化和执行
支持多种 LLM 提供商和可配置的色谱预测工具
适配 LangChain v1.x (langchain_classic)
"""

# ==================== 导入 ====================
# 使用 langchain_classic（LangChain v1.x 的兼容包）
from langchain_classic.agents import initialize_agent, AgentType
from langchain_classic.chains.conversation.memory import ConversationBufferWindowMemory
from langchain_core.callbacks import BaseCallbackHandler

# 导入三个色谱预测工具
from tlc_tool import PredictTLCValue      # 薄层色谱 Rf 值预测
from cc_tool import PredictCCValue        # 柱色谱分离预测
from hplc_tool import PredictHPLCValue    # 高效液相色谱保留时间预测

from config import config                  # 项目配置单例

import os
import re
from typing import Any
from dotenv import load_dotenv

# 从 .env 文件加载环境变量（API密钥等敏感信息）
load_dotenv()


AGENT_PROMPT_PREFIX = """You are a chromatography prediction assistant.
Choose exactly one tool when the user asks for model prediction.
Routing:
- TLC, thin-layer chromatography, or Rf -> TLC_Predictor.
- Column chromatography, CC, silica column, PE/EA separation -> CC_Predictor.
- HPLC, chiral separation, retention time, Sp, ADH, IA, IB, IC, OD, ODH, OJ, OJH -> HPLC_Retention_Time_Predictor.
For HPLC, extract two comma-separated SMILES, eluent ratio, flow speed, and candidate columns. Do not invent missing numeric values.
After observing a tool result, answer in Chinese and summarize the predicted values."""

AGENT_FORMAT_INSTRUCTIONS = """Return one JSON action only:
```json
{{{{
  "action": "<one of: Final Answer, {tool_names}>",
  "action_input": <tool args object or final answer string>
}}}}
```"""

AGENT_PROMPT_SUFFIX = """Begin.
If a prediction tool is needed, call it before answering.
If required inputs are missing, ask a concise clarification instead of guessing.
Thought:"""


class AgentTraceRecorder(BaseCallbackHandler):
    """Collect a per-request, structured trace from LangChain callbacks."""

    _SENSITIVE_RE = re.compile(
        r"(?i)(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s,'\"}]+)"
    )

    def __init__(self, user_input: str, max_iterations: int):
        self.user_input = user_input
        self.max_iterations = max_iterations
        self.loop_index = 0
        self.lines: list[str] = [
            "==================================================",
            "🔄 正在处理您的请求...",
            f"👤 用户输入: {self._clean(user_input, 2000)}",
            "==================================================",
        ]

    def _clean(self, value: Any, limit: int = 4000) -> str:
        raw = str(value)
        text = self._SENSITIVE_RE.sub(r"\1\2***", raw)
        text = text.replace("```", "'''")
        if len(text) > limit:
            text = text[:limit] + f"\n...（已截断，原始长度 {len(raw)} 字符）"
        return text

    def _model_name(self, serialized: Any) -> str:
        if isinstance(serialized, dict):
            model = serialized.get("name") or serialized.get("id")
            if isinstance(model, list):
                model = ".".join(str(part) for part in model[-2:])
            if model:
                return str(model)
        return getattr(config, "MODEL_ID", None) or getattr(config, "SILICONFLOW_MODEL_ID", None) or "LLM"

    def _message_to_text(self, message: Any) -> str:
        role = getattr(message, "type", None) or getattr(message, "role", None) or message.__class__.__name__
        content = getattr(message, "content", message)
        return f"{role}: {self._clean(content, 2500)}"

    def _format_messages(self, messages: Any) -> str:
        chunks: list[str] = []
        for conversation in messages:
            if isinstance(conversation, (list, tuple)):
                chunks.extend(self._message_to_text(message) for message in conversation)
            else:
                chunks.append(self._message_to_text(conversation))
        return "\n\n".join(chunks)

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        self.loop_index += 1
        self.lines.extend([
            "",
            f"🔄 循环 {self.loop_index}/{self.max_iterations}",
            f"正在调用 LLM 模型: {self._model_name(serialized)}",
            "📥 LLM 结构化输入:",
            "```text",
            self._clean(self._format_messages(messages), 8000),
            "```",
        ])

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        self.loop_index += 1
        self.lines.extend([
            "",
            f"🔄 循环 {self.loop_index}/{self.max_iterations}",
            f"正在调用 LLM 模型: {self._model_name(serialized)}",
            "📥 LLM 结构化输入:",
            "```text",
            self._clean("\n\n".join(prompts), 8000),
            "```",
        ])

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        outputs: list[str] = []
        for generation_group in getattr(response, "generations", []) or []:
            for generation in generation_group:
                message = getattr(generation, "message", None)
                text = getattr(generation, "text", None)
                if message is not None:
                    outputs.append(self._message_to_text(message))
                elif text:
                    outputs.append(str(text))
        self.lines.extend([
            "✅ LLM 调用成功",
            "🤖 模型输出:",
            "```text",
            self._clean("\n\n".join(outputs) if outputs else response, 8000),
            "```",
        ])

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        self.lines.append(f"❌ LLM 调用失败: {self._clean(error, 2000)}")

    def on_agent_action(self, action: Any, **kwargs: Any) -> None:
        self.lines.append(
            f"🛠️ 调用工具: {getattr(action, 'tool', 'unknown')}("
            f"{self._clean(getattr(action, 'tool_input', ''), 3000)})"
        )

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        self.lines.append(f"📊 观察结果: {self._clean(output, 4000)}")
        self.lines.append("==================================================")

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        self.lines.append(f"📊 观察结果: 工具调用错误 - {self._clean(error, 3000)}")
        self.lines.append("==================================================")

    def on_agent_finish(self, finish: Any, **kwargs: Any) -> None:
        final = ""
        return_values = getattr(finish, "return_values", None)
        if isinstance(return_values, dict):
            final = return_values.get("output", "")
        self.lines.extend([
            "🎉 任务完成!",
            f"📋 最终答案: {self._clean(final, 4000)}",
        ])

    def fallback_steps(self, steps: list[Any]) -> None:
        if not steps:
            return
        self.lines.append("")
        self.lines.append("🧭 工具调用后备记录:")
        for i, step in enumerate(steps, 1):
            action = step[0]
            observation = str(step[1]) if len(step) > 1 else ""
            self.lines.extend([
                f"步骤 {i}: {getattr(action, 'tool', 'unknown')}",
                f"参数: {self._clean(getattr(action, 'tool_input', ''), 2000)}",
                f"结果: {self._clean(observation, 3000)}",
            ])

    def render(self) -> str:
        return "\n".join(self.lines)


class ChemistryAgent:
    """
    化学智能助手主类

    负责：
    1. 根据配置选择合适的 LLM（大语言模型）
    2. 初始化色谱预测工具
    3. 构建 Agent 执行链（将 LLM 与工具结合）

    典型用法：
        agent = ChemistryAgent()
        response = agent.run("预测这个化合物的Rf值...")
    """

    def __init__(self):
        """
        初始化化学智能助手
        按顺序初始化 LLM → 工具 → Agent 链
        """
        self.llm = self._init_llm()           # 初始化大语言模型
        self.tools = self._init_tools()        # 初始化色谱预测工具
        self.agent_chain = self._init_agent()  # 初始化 Agent 执行链

    def _init_llm(self):
        """
        根据配置文件中的 LLM_TYPE 初始化对应的大语言模型

        支持的 LLM 类型：
        - openai:       OpenAI / OpenAI 兼容接口
        - zhipu:        智谱 AI (ChatGLM)
        - qianfan:      百度千帆 (ERNIE)
        - dashscope:    阿里通义千问
        - siliconflow:  SiliconFlow（DeepSeek 等开源模型）

        各提供商需要对应配置 API 密钥，请在 .env 中设置。

        Returns:
            配置好的 LLM 实例
        """
        llm_type = config.LLM_TYPE.lower()  # 获取 LLM 类型（转为小写）
        temperature = config.AGENT_TEMPERATURE  # LLM 温度参数

        # ---------- OpenAI / 兼容接口 ----------
        if llm_type == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                openai_api_key=config.OPENAI_API_KEY,
                model_name=config.MODEL_ID or "gpt-3.5-turbo",
                temperature=temperature,
                openai_api_base=config.OPENAI_API_BASE,
                request_timeout=120
            )

        # ---------- 智谱 AI (GLM-4) ----------
        elif llm_type == "zhipu":
            from langchain_community.chat_models import ChatZhipuAI
            return ChatZhipuAI(
                api_key=config.ZHIPU_API_KEY,
                model="glm-4",
                temperature=temperature
            )

        # ---------- 百度千帆 (ERNIE) ----------
        elif llm_type == "qianfan":
            from langchain_community.llms import QianfanLLM
            return QianfanLLM(
                qianfan_ak=config.QIANFAN_AK,
                qianfan_sk=config.QIANFAN_SK,
                model="ernie-4.0",
                temperature=temperature
            )

        # ---------- SiliconFlow (DeepSeek 等) ----------
        elif llm_type == "siliconflow":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                openai_api_key=config.SILICONFLOW_API_KEY,
                model_name=config.SILICONFLOW_MODEL_ID,
                temperature=temperature,
                openai_api_base=config.SILICONFLOW_API_BASE,
                request_timeout=120
            )

        else:
            raise ValueError(f"不支持的LLM类型: {llm_type}")

    def _init_tools(self):
        """
        初始化 Agent 可用的色谱预测工具列表

        每个工具都是 BaseTool 的子类，包含：
        - name:        工具名称（Agent 通过名称调用）
        - description: 工具描述（LLM 根据描述决定是否使用）
        - _run():      工具的执行逻辑

        Returns:
            工具实例列表
        """
        return [
            PredictTLCValue(),      # TLC 薄层色谱 Rf 值预测
            PredictCCValue(),       # CC 柱色谱分离预测
            PredictHPLCValue()      # HPLC 高效液相色谱预测
        ]

    def _init_agent(self):
        """
        初始化 Agent 执行链

        Agent 是 LangChain 的核心组件，它将 LLM 与工具结合，工作流程：
        1. 接收用户问题 → 2. 决定是否需要使用工具
        3. 调用工具获取数据 → 4. 基于数据生成回答

        Agent 类型说明：
        - STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION:
          - 结构化聊天 Agent，支持多参数工具
          - 零样本启动（不需要提供示例）
          - 基于 ReAct 框架（思考→行动→观察循环）

        Returns:
            配置好的 AgentExecutor 实例
        """
        # ==================== 记忆组件 ====================
        # ConversationBufferWindowMemory: 滑动窗口对话记忆
        # 保留最近 k 轮对话，超出部分自动遗忘，控制上下文长度
        memory = ConversationBufferWindowMemory(
            memory_key='chat_history',   # 记忆在 prompt 中的键名
            k=config.AGENT_MEMORY_K,     # 保留的对话轮数
            return_messages=True,        # 返回消息格式（而非纯文本）
            output_key="output"          # 输出结果在字典中的键名
        )

        # ==================== Agent 初始化 ====================
        return initialize_agent(
            agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,  # Agent 类型
            tools=self.tools,                   # 可用工具列表
            llm=self.llm,                       # 大语言模型
            verbose=True,                       # 打印详细执行日志（便于调试）
            max_iterations=config.AGENT_MAX_ITERATIONS,  # 最大迭代次数，防止死循环
            memory=memory,                      # 对话记忆组件
            return_intermediate_steps=True,      # 返回工具调用步骤，供 UI 展示每轮思考过程
            agent_kwargs={
                "prefix": AGENT_PROMPT_PREFIX,
                "format_instructions": AGENT_FORMAT_INSTRUCTIONS,
                "suffix": AGENT_PROMPT_SUFFIX,
            },
            handle_parsing_errors="在解析时出现错误，请尝试不同的提问方式。",  # 解析失败提示
            early_stopping_method='generate'    # 提前停止：让LLM生成最终答案
        )

    def _tool_summary(self, steps: list[Any]) -> str:
        """Build a stable, user-visible list of tools actually called."""
        tool_names: list[str] = []
        for step in steps:
            if not step:
                continue
            action = step[0]
            tool_name = getattr(action, "tool", "")
            if tool_name and tool_name not in tool_names:
                tool_names.append(tool_name)
        if not tool_names:
            return ""
        return "本次调用工具：" + "、".join(tool_names)

    def run_with_thinking(self, user_input: str) -> tuple[str, str]:
        """
        运行 Agent 处理用户输入，同时提取思考过程

        将用户问题送入 Agent 执行链，Agent 会：
        1. 分析问题意图
        2. 决定是否调用色谱预测工具
        3. 汇总结果生成自然语言回复

        Args:
            user_input: 用户提出的问题

        Returns:
            (回复文本, 思考过程文本) 的元组
        """
        trace = AgentTraceRecorder(user_input, config.AGENT_MAX_ITERATIONS)
        try:
            response = self.agent_chain.invoke(
                {"input": user_input},
                config={"callbacks": [trace]},
            )
            output = response.get("output", "抱歉，我无法生成有效的回复。")

            # ---- 提取 intermediate_steps 并格式化为思考过程 ----
            steps = response.get("intermediate_steps", [])
            if steps and not any("🛠️ 调用工具" in line for line in trace.lines):
                trace.fallback_steps(steps)

            # 保护：当 early_stopping 导致 LLM 将原始 Action JSON 当作 Final Answer
            # 返回时，尝试从 intermediate_steps 中提取实际的工具调用来生成有意义的回复
            if output.lstrip().startswith("{\n") or output.lstrip().startswith("Action"):
                raw_output = output
                if steps:
                    last_step = steps[-1]
                    tool_result = str(last_step[1]) if len(last_step) > 1 else ""
                    output = (
                        f"根据工具调用结果：\n{tool_result}\n\n"
                        f"（原始结构化输出：{raw_output.strip()}）"
                    )
                else:
                    output = f"Agent 未能完成工具调用。原始输出：{raw_output.strip()}"
            tool_summary = self._tool_summary(steps)
            if tool_summary and tool_summary not in output:
                output = f"{tool_summary}\n\n{output}"
            return output, trace.render()
        except Exception as e:
            print(f"Agent执行过程中发生错误: {e}")
            trace.lines.append(f"❌ Agent 执行过程中发生错误: {e}")
            return "抱歉，处理您的请求时遇到了内部错误。", trace.render()

    def run(self, user_input: str) -> str:
        """简化接口：只返回回复文本"""
        output, _ = self.run_with_thinking(user_input)
        return output
