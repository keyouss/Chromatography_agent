from __future__ import annotations

import re
import sys
import time
import traceback

from gradio_client import Client


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass


SMILES_PAIR = (
    "Fc1cccc(c1)C(=O)[C@]2(OCCC[C@H]2Br)c3cccc(F)c3,"
    "Fc1cccc(c1)C(=O)[C@@]2(OCCC[C@H]2Br)c3cccc(F)c3"
)

BASIC_QUESTIONS = [
    "我需要预测一个酯类化合物的 Rf 值，其 SMILES 表达式为 'O=C(C1=CC=CC=C1)CCCl'。我使用的是己烷与乙酸乙酯 49:1 的混合溶剂。你能帮我预测它的 Rf 值吗？",
    "我想使用柱层析分离两种化合物。化合物1 smiles为 CC(C)CCOC(=O)C1=CC=CC=C1，化合物2 smiles为 COC(=O)C1=CC=CC=C1。我使用的洗脱剂系统是石油醚/乙酸乙酯（PE/EA）90/10，柱子为4克硅胶柱，流速为8.0。请预测它们的分离效果。",
    "对于SMILES为 'Fc1cccc(c1)C(=O)[C@]2(OCCC[C@H]2Br)c3cccc(F)c3,Fc1cccc(c1)C(=O)[C@@]2(OCCC[C@H]2Br)c3cccc(F)c3' 的分子对，当流动相比例为0.1，流速为1.0时，选择ADH色谱柱进行分离，并给出详细的预测结果。",
]

GENERATED_CASES = [
    {
        "name": "TLC_hexane_ea_ester",
        "question": "请预测 SMILES 为 O=C(C1=CC=CC=C1)CCCl 的化合物在己烷/乙酸乙酯=49:1 展开剂中的 TLC Rf 值，并说明预测结果。",
        "expect": ["Rf", "TLC_Predictor"],
    },
    {
        "name": "TLC_dcm_meoh_anisole",
        "question": "我有一个化合物 COc1ccc(C=O)cc1，展开剂为二氯甲烷/甲醇=95:5。请用 TLC 模型预测它的 Rf 值。",
        "expect": ["Rf", "TLC_Predictor"],
    },
    {
        "name": "CC_pe_ea_4g",
        "question": "请用柱层析模型评估分离：化合物1 SMILES 为 CC(C)CCOC(=O)C1=CC=CC=C1，化合物2 SMILES 为 COC(=O)C1=CC=CC=C1；洗脱剂 PE/EA=90/10，4 g 硅胶柱，流速 8.0 mL/min。",
        "expect": ["CC_Predictor", "分离"],
    },
    {
        "name": "CC_pe_ea_12g",
        "question": "两个待分离化合物分别是 CCOC(=O)c1ccccc1 和 CCOC(=O)c1ccc(Cl)cc1，使用石油醚/乙酸乙酯 80/20，12克硅胶柱，流速 10.0。请预测柱层析分离效果。",
        "expect": ["CC_Predictor", "分离"],
    },
    {
        "name": "HPLC_adh_single_column",
        "question": "对于 SMILES 为 'Fc1cccc(c1)C(=O)[C@]2(OCCC[C@H]2Br)c3cccc(F)c3,Fc1cccc(c1)C(=O)[C@@]2(OCCC[C@H]2Br)c3cccc(F)c3' 的分子对，流动相比例 0.1，流速 1.0，候选色谱柱只用 ADH，请给出 HPLC 预测。",
        "expect": ["HPLC_Retention_Time_Predictor", "ADH"],
    },
    {
        "name": "HPLC_multi_column",
        "question": "请比较 ADH 和 IA 两根手性色谱柱：分子对 SMILES 为 'Fc1cccc(c1)C(=O)[C@]2(OCCC[C@H]2Br)c3cccc(F)c3,Fc1cccc(c1)C(=O)[C@@]2(OCCC[C@H]2Br)c3cccc(F)c3'，流动相比例 0.2，流速 0.8，给出推荐柱和保留时间。",
        "expect": ["HPLC_Retention_Time_Predictor", "推荐"],
    },
]

HPLC_QUESTIONS = [
    f"请用 HPLC 模型预测这个手性分子对：'{SMILES_PAIR}'，流动相比例 0.1，流速 1.0，色谱柱 ADH。",
    f"比较 ADH 和 IA 两根手性色谱柱，分子对 SMILES='{SMILES_PAIR}'，eluent=0.2，speed=0.8，给出推荐柱和保留时间。",
    f"For the enantiomer pair '{SMILES_PAIR}', run HPLC retention time prediction with mobile phase ratio 0.15, flow speed 1.0, columns ADH,IA.",
    f"分子对为 '{SMILES_PAIR}'，我想知道在 OJH 柱上 HPLC 的保留时间；流动相比例 0.1，流速 0.6。",
    f"请评估 ADH,IA,OJH 三个候选手性柱对 '{SMILES_PAIR}' 的分离概率 Sp；流动相比例 0.25，流速 1.2。",
    f"HPLC 条件筛选：SMILES pair '{SMILES_PAIR}'，候选柱为 IA，流动相 0.05，流速 1.0 mL/min。",
    f"给出手性 HPLC 预测结果：两个 SMILES 是 '{SMILES_PAIR}'；柱子 ADH；流动相比例是 0.3；流速是 0.9。",
    f"Retention time prediction needed. smiles: '{SMILES_PAIR}', eluent ratio: 0.12, speed: 1.1, chiral columns: ADH, OJH.",
    f"对于这一对对映体 '{SMILES_PAIR}'，在 ADH 和 IA 柱上哪个更推荐？HPLC 流动相比例 0.18，流速 0.7。",
    f"请用 HPLC_Retention_Time_Predictor 预测 '{SMILES_PAIR}'，columns='ADH,IA', eluent=0.1, speed=1.0，并解释 Sp 和 RT。",
]


def _content_preview(message: object) -> str:
    if isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = str(message)
    return str(content).replace("\n", " ")[:1200]


def _flatten_content(message: object) -> str:
    if isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = str(message)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", item)))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _looks_bad(text: str) -> bool:
    return any(
        pattern in text
        for pattern in [
            "Traceback",
            "Data incompatible",
            "Agent 初始化失败",
            "内部错误",
            "Missing some input keys",
            "HTTPConnectionPool",
            "Connection refused",
            "无法连接",
            "API请求失败",
        ]
    )


def _call_case(question: str, retries: int = 1):
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            client = Client("http://127.0.0.1:7860/")
            return client.predict(question, [], api_name="/chat_with_agent")
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                print(
                    f"WARN: client exception, retrying once after reconnect: {exc}",
                    flush=True,
                )
                time.sleep(3)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Gradio client call failed without an exception")


def _run_basic_questions() -> int:
    client = Client("http://127.0.0.1:7860/")
    history: list = []
    for index, question in enumerate(BASIC_QUESTIONS, 1):
        print(f"===== EXAMPLE {index} START =====", flush=True)
        try:
            textbox, history = client.predict(
                question,
                history,
                api_name="/chat_with_agent",
            )
        except Exception:
            print(f"===== EXAMPLE {index} FAIL =====", flush=True)
            traceback.print_exc()
            return 1
        print(f"textbox={textbox!r}", flush=True)
        print(f"history_len={len(history)}", flush=True)
        print(f"last_preview={_content_preview(history[-1])}", flush=True)
        text = _flatten_content(history[-1])
        if _looks_bad(text):
            print(f"===== EXAMPLE {index} FAIL bad response =====", flush=True)
            return 1
        print(f"===== EXAMPLE {index} OK =====", flush=True)
    print(f"FINAL_HISTORY_LEN={len(history)}", flush=True)
    return 0


def _run_generated_cases() -> int:
    failures: list[str] = []
    asked: list[str] = []

    for index, case in enumerate(GENERATED_CASES, 1):
        print(f"===== CASE {index}: {case['name']} =====", flush=True)
        print(f"QUESTION: {case['question']}", flush=True)
        asked.append(f"{index}. {case['question']}")
        try:
            textbox, history = _call_case(case["question"], retries=1)
        except Exception:
            print("RESULT: FAIL client exception", flush=True)
            traceback.print_exc()
            failures.append(case["name"])
            continue

        if textbox != "":
            print(f"WARN: textbox was not cleared: {textbox!r}", flush=True)
        if len(history) < 2:
            print(f"RESULT: FAIL short history: {len(history)}", flush=True)
            failures.append(case["name"])
            continue

        text = _flatten_content(history[-1])
        preview = re.sub(r"\s+", " ", text)[:1000]
        print(f"PREVIEW: {preview}", flush=True)
        has_panel = "<details" in text and "Agent 思考过程" in text
        has_expected = all(token in text for token in case["expect"])
        if _looks_bad(text) or not has_panel or not has_expected:
            print(
                f"RESULT: FAIL has_panel={has_panel} has_expected={has_expected}",
                flush=True,
            )
            failures.append(case["name"])
        else:
            print("RESULT: OK", flush=True)

    print("===== ASKED QUESTIONS =====", flush=True)
    for item in asked:
        print(item, flush=True)

    if failures:
        print(f"FAILED_CASES={failures}", flush=True)
        return 1
    print("ALL_GENERATED_CASES_OK", flush=True)
    return 0


def _run_hplc_cases() -> int:
    failures: list[int] = []
    for index, question in enumerate(HPLC_QUESTIONS, 1):
        print(f"===== HPLC CASE {index} START =====", flush=True)
        print(f"QUESTION: {question}", flush=True)
        try:
            textbox, history = _call_case(question, retries=1)
        except Exception:
            print("RESULT: FAIL client exception", flush=True)
            traceback.print_exc()
            failures.append(index)
            continue
        if textbox:
            print(f"WARN: textbox={textbox!r}", flush=True)
        if len(history) < 2:
            print(f"RESULT: FAIL short history={len(history)}", flush=True)
            failures.append(index)
            continue
        text = _flatten_content(history[-1])
        preview = re.sub(r"\s+", " ", text)[:1000]
        print(f"PREVIEW: {preview}", flush=True)
        has_tool_summary = "本次调用工具：HPLC_Retention_Time_Predictor" in text
        has_trace_panel = "<details" in text and "Agent 思考过程" in text
        has_hplc_result = any(token in text for token in ["保留时间", "RT", "Sp", "分离概率"])
        if _looks_bad(text) or not (has_tool_summary and has_trace_panel and has_hplc_result):
            print(
                "RESULT: FAIL "
                f"has_tool_summary={has_tool_summary} "
                f"has_trace_panel={has_trace_panel} "
                f"has_hplc_result={has_hplc_result}",
                flush=True,
            )
            failures.append(index)
        else:
            print("RESULT: OK", flush=True)
    if failures:
        print(f"FAILED_HPLC_CASES={failures}", flush=True)
        return 1
    print("ALL_HPLC_CASES_OK", flush=True)
    return 0


def main() -> int:
    exit_code = 0

    print("===== GROUP 1: BASIC E2E QUESTIONS =====", flush=True)
    if _run_basic_questions() != 0:
        exit_code = 1

    print("\n===== GROUP 2: GENERATED CASES =====", flush=True)
    if _run_generated_cases() != 0:
        exit_code = 1

    print("\n===== GROUP 3: HPLC EXHAUSTIVE CASES =====", flush=True)
    if _run_hplc_cases() != 0:
        exit_code = 1

    if exit_code == 0:
        print("ALL_E2E_TESTS_OK", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
