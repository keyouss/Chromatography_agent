"""
Gradio Web UI for the local chromatography Agent.
"""

from __future__ import annotations

import argparse
import html
import os
import subprocess
import sys
import time
from pathlib import Path

import gradio as gr
import requests

from chemistry_agent import ChemistryAgent


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_SERVICE_PYTHON = Path(sys.executable)
PYTHON_EXE = Path(os.getenv("CHROMA_SERVICE_PYTHON", str(DEFAULT_SERVICE_PYTHON)))

SERVICE_HEALTH_URLS = [
    "http://127.0.0.1:8000/health",
    "http://127.0.0.1:8001/health",
    "http://127.0.0.1:8002/health",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--with-services",
        action="store_true",
        help="Start prediction services before launching the UI.",
    )
    return parser.parse_args()


def normalized_environment() -> dict[str, str]:
    env: dict[str, str] = {}
    seen: set[str] = set()
    for key, value in os.environ.items():
        lower_key = key.lower()
        if lower_key in seen:
            continue
        seen.add(lower_key)
        env[key] = value
    return env


def should_start_services(args: argparse.Namespace) -> bool:
    env_value = os.getenv("START_SERVICES_WITH_UI", "").strip().lower()
    return args.with_services or env_value in {"1", "true", "yes", "on"}


def wait_for_services(timeout: int = 120) -> None:
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
                pass
        if pending:
            time.sleep(2)
    if pending:
        raise RuntimeError(f"预测服务启动失败: {sorted(pending)}")


def start_services_if_requested(args: argparse.Namespace):
    if not should_start_services(args):
        return None

    python = PYTHON_EXE if PYTHON_EXE.exists() else Path(sys.executable)
    script = ROOT_DIR / "start_services.py"
    print(f"正在启动预测服务: {script}")
    process = subprocess.Popen(
        [str(python), str(script)],
        cwd=str(ROOT_DIR),
        env=normalized_environment(),
    )
    wait_for_services()
    return process


def stop_started_services(process) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


ARGS = parse_args()
SERVICE_PROCESS = start_services_if_requested(ARGS)

print("正在初始化化学智能助手，这可能需要几秒钟...")
try:
    agent = ChemistryAgent()
    print("化学智能助手已准备就绪。")
except Exception as exc:
    print(f"初始化 Agent 时发生严重错误: {exc}")

    class FailedAgent:
        def run(self, user_input: str) -> str:
            return f"Agent 初始化失败: {exc}"

    agent = FailedAgent()


def build_assistant_message(output: str, thinking: str) -> str:
    safe_thinking = html.escape(thinking or "本轮没有可用的思考过程。")
    return (
        "<details class='agent-thinking'>"
        "<summary>Agent 思考过程</summary>\n\n"
        "<div class='agent-thinking__body' "
        "onclick=\"if(!window.getSelection().toString().trim()){this.closest('details').removeAttribute('open');}\">"
        "<pre><code>"
        f"{safe_thinking}"
        "</code></pre>"
        "</div>\n"
        "</details>\n\n"
        f"{output}"
    )


def normalize_chat_history(history: list | None) -> list[dict[str, str]]:
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
    return []


EXAMPLE_QUESTIONS = [
    "我需要预测一个酯类化合物的 Rf 值，其 SMILES 表达式为 'O=C(C1=CC=CC=C1)CCCl'。我使用的是己烷与乙酸乙酯 49:1 的混合溶剂。你能帮我预测它的 Rf 值吗？",
    "我想使用柱层析分离两种化合物。化合物1 smiles为 CC(C)CCOC(=O)C1=CC=CC=C1，化合物2 smiles为 COC(=O)C1=CC=CC=C1。我使用的洗脱剂系统是石油醚/乙酸乙酯（PE/EA）90/10，柱子为4克硅胶柱，流速为8.0。",
    "对于SMILES为 'Fc1cccc(c1)C(=O)[C@]2(OCCC[C@H]2Br)c3cccc(F)c3,Fc1cccc(c1)C(=O)[C@@]2(OCCC[C@H]2Br)c3cccc(F)c3' 的分子对，当流动相比例为0.1，流速为1.0时，选择ADH色谱柱进行分离，并给出详细的预测结果。",
]

UI_CSS = """
<style>
.agent-thinking {
    margin: 0 0 0.85rem 0;
    border: 1px solid #d7dce5;
    border-radius: 8px;
    background: #f7f9fc;
    overflow: hidden;
}
.agent-thinking summary {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.8rem 0.95rem;
    color: #1f2937;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
    text-align: left;
    list-style: none;
}
.agent-thinking summary::-webkit-details-marker {
    display: none;
}
.agent-thinking summary::after {
    content: "▾";
    flex: 0 0 auto;
    transition: transform 0.18s ease;
}
.agent-thinking[open] summary::after {
    transform: rotate(180deg);
}
.agent-thinking summary:hover {
    background: rgba(59, 130, 246, 0.06);
}
.agent-thinking summary:focus-visible {
    outline: 2px solid #3b82f6;
    outline-offset: -2px;
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
.send-button-column {
    flex: 0 0 112px !important;
    max-width: 112px !important;
    min-width: 112px !important;
}
.send-button-column button {
    min-width: 96px !important;
    width: 100% !important;
    white-space: nowrap !important;
}
</style>
"""

with gr.Blocks(title="化学智能助手 (TLC & CC & HPLC)") as demo:
    gr.Markdown("# 化学智能助手 (TLC & CC & HPLC)")
    gr.Markdown(
        "您好！我是您的化学研究助手，支持 TLC Rf 预测、CC 柱层析分离预测和 HPLC 保留时间预测。"
        "请输入化合物信息与实验条件，我会调用对应的本地预测服务。"
    )
    gr.HTML(UI_CSS)

    chatbot = gr.Chatbot(
        label="对话",
        height=550,
        sanitize_html=False,
    )

    with gr.Row():
        msg = gr.Textbox(
            placeholder="请输入您的问题...",
            label="输入",
            scale=1,
            min_width=320,
        )
        with gr.Column(elem_classes=["send-button-column"], scale=0, min_width=112):
            send_btn = gr.Button("发送", variant="primary", min_width=96)

    clear_btn = gr.Button("清除对话", size="sm")

    gr.Examples(
        examples=[[q] for q in EXAMPLE_QUESTIONS],
        inputs=[msg],
        label="快速示例（点击即可填充输入框）",
        cache_examples=False,
    )

    msg.submit(chat_with_agent, [msg, chatbot], [msg, chatbot])
    send_btn.click(chat_with_agent, [msg, chatbot], [msg, chatbot])
    clear_btn.click(clear_chat, None, [chatbot])


if __name__ == "__main__":
    print("正在以严格本地模式启动服务器... 请在浏览器中打开 http://127.0.0.1:7860")
    try:
        demo.launch(server_name="127.0.0.1")
    finally:
        stop_started_services(SERVICE_PROCESS)
