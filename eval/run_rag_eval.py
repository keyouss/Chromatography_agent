"""Lightweight evaluation harness for RAG/tool-calling questions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = ROOT_DIR / "eval" / "rag_eval_questions.jsonl"
TOOL_NAMES = (
    "TLC_Predictor",
    "CC_Predictor",
    "HPLC_Retention_Time_Predictor",
    "Knowledge_Base_Search",
)


def load_questions(path: Path) -> list[dict]:
    questions = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            required = {"id", "question", "expected_tool", "expected_sources", "rubric"}
            missing = sorted(required - set(item))
            if missing:
                raise ValueError(f"Line {line_number} is missing keys: {missing}")
            questions.append(item)
    return questions


def expected_tool_was_called(trace: str, expected_tool: str) -> bool:
    if expected_tool == "Final Answer":
        return "调用工具:" not in trace
    pattern = rf"调用工具:\s*{re.escape(expected_tool)}\b"
    return re.search(pattern, trace) is not None


def expected_tool_was_called(trace: str, expected_tool: str) -> bool:
    """Detect actual callback tool calls without relying on localized text."""
    if expected_tool == "Final Answer":
        return all(f"{tool_name}(" not in trace for tool_name in TOOL_NAMES)
    pattern = rf"{re.escape(expected_tool)}\("
    return re.search(pattern, trace) is not None


def missing_expected_sources(output: str, expected_sources: list[str]) -> list[str]:
    return [source for source in expected_sources if source and source not in output]


def run_agent_eval(questions: list[dict], limit: int | None = None) -> int:
    sys.path.insert(0, str(ROOT_DIR))
    from chemistry_agent import ChemistryAgent

    failures = 0
    for item in questions[:limit]:
        agent = ChemistryAgent()
        output, trace = agent.run_with_thinking(item["question"])
        expected_tool = item["expected_tool"]
        reasons = []
        if not expected_tool_was_called(trace, expected_tool):
            reasons.append(f"tool_not_called:{expected_tool}")
        missing_sources = missing_expected_sources(output, item["expected_sources"])
        if missing_sources:
            reasons.append("missing_sources:" + ",".join(missing_sources))

        if reasons:
            failures += 1
            status = "FAIL"
        else:
            status = "PASS"
        reason_text = "" if not reasons else " reasons=" + ";".join(reasons)
        print(f"[{status}] {item['id']} expected={expected_tool}{reason_text}")
        print(output[:800].replace("\n", " "))
        print("-" * 80)
    total = len(questions[:limit])
    print(f"Live Agent evaluation: {total - failures}/{total} passed, {failures} failed.")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-agent", action="store_true", help="Actually call the Agent and LLM.")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    questions = load_questions(QUESTIONS_PATH)
    print(f"Loaded {len(questions)} evaluation questions from {QUESTIONS_PATH}.")
    if not args.run_agent:
        print("Format check only. Use --run-agent to run live Agent evaluation.")
        return 0
    return run_agent_eval(questions, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
