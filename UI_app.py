"""
Web UI 入口 - 基于 Gradio 的化学智能助手聊天界面
提供 TLC/CC/HPLC 色谱预测的交互式对话界面
"""

import argparse
import os
import subprocess
import sys
import time
import html
from pathlib import Path

import gradio as gr
import requests
from chemistry_agent import ChemistryAgent


# ==================== 路径配置 ====================
# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent
# 默认使用当前 Python 解释器来启动子进程
DEFAULT_SERVICE_PYTHON = Path(sys.executable)
PYTHON_EXE = Path(os.getenv("CHROMA_SERVICE_PYTHON", str(DEFAULT_SERVICE_PYTHON)))

# ==================== 服务健康检查配置 ====================
# 三个预测服务的健康检查地址
SERVICE_HEALTH_URLS = [
    "http://127.0.0.1:8000/health",   # TLC 服务
    "http://127.0.0.1:8001/health",   # CC 服务
    "http://127.0.0.1:8002/health",   # HPLC 服务
]
# 服务 PID 存储文件（用于清理子进程）
SERVICE_PID_FILE = ROOT_DIR / "Services" / ".service_pids"


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--with-services",
        action="store_true",
        help="启动UI前自动启动预测服务（start_services.py）",
    )
    return parser.parse_args()


def should_start_services(args) -> bool:
    """判断是否需要在启动UI前自动启动预测服务"""
    env_value = os.getenv("START_SERVICES_WITH_UI", "").strip().lower()
    return args.with_services or env_value in {"1", "true", "yes", "on"}


def start_services_if_requested(args):
    """
    如果用户要求，启动预测服务子进程
    返回子进程对象，用于后续清理
    """
    if not should_start_services(args):
        return None
    python = PYTHON_EXE if PYTHON_EXE.exists() else Path(sys.executable)
    script = ROOT_DIR / "start_services.py"
    print(f"正在启动预测服务: {script} ...")
    process = subprocess.Popen(
        [str(python), str(script)],
        cwd=str(ROOT_DIR),
        env=normalized_environment(),
    )
    wait_for_services()  # 等待服务就绪
    return process


def normalized_environment() -> dict[str, str]:
    """
    规范化环境变量，去重键名大小写
    避免 Windows 上环境变量大小写不敏感导致的重复问题
    """
    env: dict[str, str] = {}
    seen: set[str] = set()
    for key, value in os.environ.items():
        lower_key = key.lower()
        if lower_key in seen:
            continue
        seen.add(lower_key)
        env[key] = value
    return env


def wait_for_services(timeout: int = 120) -> None:
    """
    等待所有预测服务就绪
    轮询健康检查接口，超时则抛出异常

    Args:
        timeout: 最大等待时间（秒），默认120秒
    """
    deadline = time.time() + timeout
    pending = set(SERVICE_HEALTH_URLS)
    while pending and time.time() < deadline:
        for url in list(pending):
            try:
                response = requests.get(url, timeout=2)
                payload = response.json()
                if response.ok and payload.get("ok"):
                    pending.remove(url)
            except Exception:
                pass  # 服务尚未就绪，继续等待
        if pending:
            time.sleep(2)
    if pending:
        raise RuntimeError(f"预测服务启动失败: {sorted(pending)}")


def stop_started_services(process) -> None:
    """停止由本脚本启动的预测服务子进程"""
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


# ==================== 可选联动启动预测服务 ====================
# 默认不会自动启动 start_services.py。
# 只有显式传入 --with-services，或设置 START_SERVICES_WITH_UI=true/1/yes/on，
# 才会在 UI 初始化前启动 start_services.py 并等待三个预测服务健康检查通过。
ARGS = parse_args()
SERVICE_PROCESS = start_services_if_requested(ARGS)

print("正在初始化化学智能助手，这可能需要几秒钟...")
try:
    # 创建全局 Agent 实例（单例模式）
    # 让Agent的记忆在整个会话中保持，而非每次点击都重置
    agent = ChemistryAgent()
    print("化学智能助手已准备就绪。")
except Exception as e:
    print(f"初始化Agent时发生严重错误: {e}")
    # Agent初始化失败时提供降级方案，避免Gradio崩溃
    class FailedAgent:
        def run(self, user_input):
            return f"Agent初始化失败: {e}"
    agent = FailedAgent()


# ==================== Gradio 聊天逻辑 ====================
def build_assistant_message(output: str, thinking: str) -> str:
    """把本轮思考过程和最终答案组合到同一条 assistant 消息中。"""
    safe_thinking = html.escape(thinking or "本轮没有可用的思考过程。")
    panel_id = f"thinking-{time.time_ns()}"
    return (
        f"<div class='agent-thinking' data-thinking-id='{panel_id}'>"
        f"<button class='agent-thinking__summary' type='button' aria-expanded='false' aria-controls='{panel_id}'>"
        "<span>Agent 思考过程</span>"
        "<span class='agent-thinking__chevron'>▾</span>"
        "</button>\n\n"
        f"<div id='{panel_id}' class='agent-thinking__body' hidden>"
        "<pre><code>"
        f"{safe_thinking}"
        "</code></pre>"
        "</div>\n"
        "</div>\n\n"
        f"{output}"
    )


def normalize_chat_history(history: list | None) -> list[dict[str, str]]:
    """Normalize old tuple history and messages history for Gradio Chatbot."""
    normalized: list[dict[str, str]] = []
    for item in history or []:
        if isinstance(item, dict) and "role" in item and "content" in item:
            normalized.append({
                "role": str(item["role"]),
                "content": str(item["content"]),
            })
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            user_msg, assistant_msg = item
            if user_msg is not None:
                normalized.append({"role": "user", "content": str(user_msg)})
            if assistant_msg is not None:
                normalized.append({"role": "assistant", "content": str(assistant_msg)})
        elif hasattr(item, "role") and hasattr(item, "content"):
            normalized.append({
                "role": str(item.role),
                "content": str(item.content),
            })
    return normalized


def chat_with_agent(message: str, history: list):
    """
    Gradio 聊天处理函数
    接收用户输入，调用 Agent 处理，返回回复 + 思考过程

    Args:
        message: 用户在聊天框中输入的消息文本
        history: Gradio 聊天记录列表 [(user_message, assistant_message), ...]

    Returns:
        (清空的输入框, 更新后的历史, 思考过程文本)
    """
    print(f"收到用户输入: {message}")
    history = normalize_chat_history(history)
    if hasattr(agent, "run_with_thinking"):
        output, thinking = agent.run_with_thinking(message)
    else:
        output = agent.run(message)
        thinking = "Agent 初始化失败，思考过程不可用。"
    print(f"Agent回复: {output}")
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": build_assistant_message(output, thinking)})
    return "", history


def clear_chat():
    """清除对话历史和思考面板"""
    return []


# ==================== 定义 Gradio 界面 ====================
EXAMPLE_QUESTIONS = [
    "我需要预测一个酮类化合物的 Rf 值，其 SMILES 表达式为 'O=C(C1=CC=CC=C1)CCCl'。我使用的是己烷与乙酸乙酯 49:1 的混合溶剂。你能帮我预测它的 Rf 值吗？",
    "我想使用柱层析分离两种化合物。化合物1 smiles为 CC(C)CCOC(=O)C1=CC=CC=C1，化合物2 smiles为 COC(=O)C1=CC=CC=C1。我使用的洗脱剂系统是石油醚/乙酸乙酯（PE/EA）40/10，柱子为4克硅胶柱，流速为8.0。",
    "对于SMILES为 'Fc1cccc(c1)C(=O)[C@]2(OCCC[C@H]2Br)c3cccc(F)c3,Fc1cccc(c1)C(=O)[C@@]2(OCCC[C@H]2Br)c3cccc(F)c3' 的分子对，当流动相比例为0.1，流速为1.0时，选择ADH色谱柱进行分离，并给出详细的预测结果。",
]

THINKING_PANEL_HEAD = """
<script>
(() => {
  if (window.__agentThinkingInitialized) return;
  window.__agentThinkingInitialized = true;

  function setOpen(panel, open) {
    const summary = panel.querySelector('.agent-thinking__summary');
    const body = panel.querySelector('.agent-thinking__body');
    if (!summary || !body) return;
    panel.classList.toggle('is-open', open);
    summary.setAttribute('aria-expanded', open ? 'true' : 'false');
    body.hidden = !open;
  }

  function hasSelection() {
    const selection = window.getSelection ? window.getSelection() : null;
    return Boolean(selection && String(selection).trim());
  }

  document.addEventListener('click', (event) => {
    const summary = event.target.closest('.agent-thinking__summary');
    if (summary) {
      const panel = summary.closest('.agent-thinking');
      if (!panel) return;
      setOpen(panel, !panel.classList.contains('is-open'));
      return;
    }

    const body = event.target.closest('.agent-thinking__body');
    if (!body) return;
    const panel = body.closest('.agent-thinking');
    if (!panel || !panel.classList.contains('is-open')) return;
    if (hasSelection()) return;
    if (event.target.closest('a, button, input, textarea, select')) return;
    setOpen(panel, false);
  });
})();
</script>
"""

with gr.Blocks(title="化学智能助手 (TLC & CC & HPLC)") as demo:
    # ---- 标题与描述 ----
    gr.Markdown("# 化学智能助手 (TLC & CC & HPLC)")
    gr.Markdown(
        "您好！我是您的化学研究助手，支持多种色谱分析预测。"
        "您可以使用我来预测 TLC（薄层色谱）的 Rf 值、"
        "模拟 CC（柱色谱）的分离过程，或进行 HPLC（高效液相色谱）的保留时间预测。"
        "欢迎输入您的化合物信息与需求！"
    )
    gr.HTML(
        """
<style>
.agent-thinking {
    margin: 0 0 0.85rem 0;
    border: 1px solid #d7dce5;
    border-radius: 10px;
    background: #f7f9fc;
    overflow: hidden;
}
.agent-thinking__summary {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.8rem 0.95rem;
    border: 0;
    background: transparent;
    color: #1f2937;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
    text-align: left;
}
.agent-thinking__summary:hover {
    background: rgba(59, 130, 246, 0.06);
}
.agent-thinking__summary:focus-visible {
    outline: 2px solid #3b82f6;
    outline-offset: -2px;
}
.agent-thinking__chevron {
    flex: 0 0 auto;
    transition: transform 0.18s ease;
}
.agent-thinking.is-open .agent-thinking__chevron {
    transform: rotate(180deg);
}
.agent-thinking__body {
    padding: 0 0.95rem 0.95rem;
    cursor: pointer;
}
.agent-thinking__body pre {
    margin: 0;
    padding: 0.9rem;
    border-radius: 8px;
    background: #111827;
    color: #e5e7eb;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    cursor: text;
}
.agent-thinking__body code {
    font-size: 0.88rem;
    line-height: 1.55;
}
</style>
        """
    )

    # ---- 聊天区域 ----
    chatbot = gr.Chatbot(
        label="对话",
        height=550,
        sanitize_html=False,
    )

    with gr.Row():
        msg = gr.Textbox(
            placeholder="请输入您的问题...",
            label="输入",
            scale=9,
        )
        send_btn = gr.Button("发送", variant="primary", scale=1)

    clear_btn = gr.Button("清除对话", size="sm")

    # ---- 预设示例 ----
    gr.Examples(
        examples=[[q] for q in EXAMPLE_QUESTIONS],
        inputs=[msg],
        label="快速示例（点击即可填充输入框）",
        cache_examples=False,
    )

    # ---- 事件绑定 ----
    msg.submit(chat_with_agent, [msg, chatbot], [msg, chatbot])
    send_btn.click(chat_with_agent, [msg, chatbot], [msg, chatbot])
    clear_btn.click(clear_chat, None, [chatbot])


# ==================== 启动应用 ====================
if __name__ == "__main__":
    print("正在以严格本地模式启动服务器... 请在浏览器中打开 http://127.0.0.1:7860")
    try:
        # server_name="127.0.0.1" 仅本地可访问
        # 如需局域网访问请改为 "0.0.0.0"
        # 如需公网临时链接请添加 share=True
        demo.launch(server_name="127.0.0.1", head=THINKING_PANEL_HEAD)
    finally:
        # 确保退出时清理启动的子进程
        stop_started_services(SERVICE_PROCESS)
