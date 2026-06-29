"""
启动预测服务脚本 - 启动所有色谱预测服务（TLC/CC/HPLC）
使用 uvicorn 运行 FastAPI 应用作为后台服务
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# ==================== 路径配置 ====================
# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent
# 服务代码目录
SERVICES_DIR = ROOT_DIR / "Services"
LOG_DIR = ROOT_DIR / "Log"
# 使用当前 Python 解释器
DEFAULT_SERVICE_PYTHON = Path(sys.executable)
PYTHON_EXE = Path(os.getenv("CHROMA_SERVICE_PYTHON", str(DEFAULT_SERVICE_PYTHON)))
# PID 文件路径（记录启动的服务进程ID）
PID_FILE = SERVICES_DIR / ".service_pids"

# 服务定义列表：(显示名称, uvicorn应用路径, 端口号)
SERVICES = [
    ("TLC",  "tlc_server:app",  8000),   # 薄层色谱 Rf值预测
    ("CC",   "cc_server:app",   8001),   # 柱色谱分离预测
    ("HPLC", "hplc_server:app", 8002),   # 高效液相色谱预测
]


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """检查指定端口是否已被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


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
    # 将 Services 目录添加到 PYTHONPATH，确保服务模块可以相互导入
    python_path_key = next((key for key in env if key.lower() == "pythonpath"), "PYTHONPATH")
    env[python_path_key] = str(SERVICES_DIR) + os.pathsep + env.get(python_path_key, "")
    return env


def _build_log_config(name: str, port: int) -> dict:
    """
    构建 uvicorn 日志配置，在访问日志中显示工具名称、端口和时间戳。

    默认 uvicorn 输出格式：
        INFO:     127.0.0.1:58165 - "POST /predict HTTP/1.1" 200 OK
    自定义后输出格式：
        [TLC:8000]   2026-06-27 10:30:45 | 127.0.0.1:58165 - "POST /predict HTTP/1.1" 200 OK
        [CC:8001]    2026-06-27 10:30:45 | 127.0.0.1:58165 - "POST /predict HTTP/1.1" 200 OK
        [HPLC:8002]  2026-06-27 10:30:45 | 127.0.0.1:58165 - "POST /predict HTTP/1.1" 200 OK
    """
    # 计算对齐宽度：最长服务名+端口格式为 HPLC:8002，共8字符，加2个方括号共10字符
    prefix = f"[{name}:{port}]"
    # 使用制表符或空格进行对齐，这里使用空格对齐到12字符宽度
    padding = " " * (12 - len(prefix))
    
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": f"{prefix}{padding}%(asctime)s | %(client_addr)s - \"%(request_line)s\" %(status_code)s",
            },
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": f"{prefix}{padding}%(asctime)s | %(levelprefix)s %(message)s",
            },
        },
        "handlers": {
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "access",
                "stream": "ext://sys.stdout",
            },
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn.access": {
                "handlers": ["access"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }


def start_service(
    name: str, app: str, port: int
) -> tuple[subprocess.Popen | None, str | None]:
    """
    启动一个 Uvicorn 服务进程

    Args:
        name: 服务名称（仅用于显示）
        app: uvicorn 应用路径，格式 "模块名:应用实例名"
        port: 监听端口

    Returns:
        (子进程对象, 日志配置文件路径)，如果端口已被占用则返回 (None, None)
    """
    if is_port_open(port):
        print(f"[{name}] 端口 {port} 已被占用，认为服务已在运行。")
        return None, None
    if not PYTHON_EXE.exists():
        raise FileNotFoundError(f"Python解释器未找到: {PYTHON_EXE}")

    # 生成临时日志配置文件（自定义访问日志格式）
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_config = _build_log_config(name, port)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, dir=str(LOG_DIR)
    ) as tmp:
        json.dump(log_config, tmp)
        log_config_path = tmp.name

    # 使用 uvicorn 启动 FastAPI 应用
    command = [
        str(PYTHON_EXE),
        "-m", "uvicorn",             # 以模块方式运行 uvicorn
        app,                         # FastAPI 应用路径
        "--host", "127.0.0.1",       # 监听本地地址
        "--port", str(port),         # 监听端口
        "--log-config", log_config_path,
    ]
    env = normalized_environment()
    print(f"[{name}] 启动中: http://127.0.0.1:{port}/predict")
    # 启动子进程，不阻塞当前脚本
    return subprocess.Popen(command, cwd=str(SERVICES_DIR), env=env), log_config_path


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--duration",
        type=float,
        default=0,
        help="服务运行时长（秒），0 表示一直运行直到 Ctrl+C",
    )
    return parser.parse_args()


def main() -> int:
    """主函数：启动所有服务并保持运行"""
    args = parse_args()
    print("Starting real chromatography prediction services...")
    processes: list[subprocess.Popen] = []
    log_config_files: list[str] = []

    try:
        # 清空 PID 文件
        PID_FILE.write_text("", encoding="utf-8")

        # 逐个启动服务
        for name, app, port in SERVICES:
            process, log_cfg = start_service(name, app, port)
            if process is not None:
                processes.append(process)
                log_config_files.append(log_cfg)
                # 记录 PID 到文件
                with PID_FILE.open("a", encoding="utf-8") as handle:
                    handle.write(f"{process.pid}\n")
        print("Services are ready or starting. Press Ctrl+C to stop services started by this script.")
        started_at = time.time()

        # 保持运行，监控子进程状态
        while True:
            for process in list(processes):
                if process.poll() is not None:
                    print(f"A service process exited with code {process.returncode}.")
                    processes.remove(process)
            # 如果指定了运行时长，到时自动退出
            if args.duration and time.time() - started_at >= args.duration:
                print("Service duration elapsed; stopping services.")
                break
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping services...")
    finally:
        # 清理：终止所有启动的子进程
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        # 删除 PID 文件
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass
        # 删除临时日志配置文件
        for path_str in log_config_files:
            try:
                Path(path_str).unlink()
            except (FileNotFoundError, PermissionError):
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
