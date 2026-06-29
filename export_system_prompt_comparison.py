from __future__ import annotations

from datetime import datetime
from pathlib import Path

from chemistry_agent import ChemistryAgent


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "Log"
ORIGINAL_PROMPT_PATH = LOG_DIR / "system_prompt_original_before_update.txt"
OUTPUT_PATH = LOG_DIR / "system_prompt_comparison.md"


def _read_original_prompt() -> str:
    if ORIGINAL_PROMPT_PATH.exists():
        return ORIGINAL_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "(Original prompt snapshot was not found.)"


def _current_prompt() -> str:
    agent = ChemistryAgent()
    return agent.agent_chain.agent.llm_chain.prompt.messages[0].prompt.template


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    original = _read_original_prompt()
    current = _current_prompt()
    markdown = f"""# System Prompt Comparison

- Generated at: {datetime.now().isoformat(timespec="seconds")}
- Original prompt characters: {len(original)}
- Updated prompt characters: {len(current)}
- Character reduction: {len(original) - len(current)}

## Original Runtime System Prompt

```text
{original}
```

## Updated Runtime System Prompt

```text
{current}
```
"""
    OUTPUT_PATH.write_text(markdown, encoding="utf-8")
    print(OUTPUT_PATH)
    print(f"original_chars={len(original)}")
    print(f"updated_chars={len(current)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
