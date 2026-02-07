import json
import os
import glob
from typing import Any

# Use env var for flexibility, fallback to hardcoded dev path
SESSION_GLOB = os.path.join(os.getenv("OPENCLAW_SESSIONS_DIR", "/home/xiaoxiong/.openclaw/agents/main/sessions"), "*.jsonl")
OUT_DIR = os.path.join(os.getenv("MEMU_DATA_DIR", "/home/xiaoxiong/.openclaw/workspace/memU/data"), "conversations")


def _extract_text_parts(content_list: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for part in content_list or []:
        # Only keep plain text. Ignore tool calls, thinking, etc.
        if isinstance(part, dict) and part.get("type") == "text":
            t = part.get("text")
            if isinstance(t, str) and t.strip():
                parts.append(t)
    return "\n".join(parts).strip()


def convert(*, since_ts: float | None = None) -> list[str]:
    os.makedirs(OUT_DIR, exist_ok=True)

    session_files = glob.glob(SESSION_GLOB)
    converted: list[str] = []

    for file_path in session_files:
        if since_ts is not None:
            try:
                if os.path.getmtime(file_path) <= since_ts:
                    continue
            except FileNotFoundError:
                continue

        session_id = os.path.basename(file_path).replace(".jsonl", "")
        output_path = os.path.join(OUT_DIR, f"{session_id}.json")

        messages: list[dict[str, str]] = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue

                    if entry.get("type") != "message":
                        continue

                    msg_obj = entry.get("message", {})
                    role = msg_obj.get("role")
                    content_list = msg_obj.get("content", [])

                    text = _extract_text_parts(content_list)
                    if text and role in ("user", "assistant"):
                        messages.append({"role": role, "content": text})
        except FileNotFoundError:
            continue

        if messages:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(messages, f, indent=2, ensure_ascii=False)
            converted.append(output_path)

    return converted


if __name__ == "__main__":
    paths = convert()
    print(f"Converted {len(paths)} sessions into {OUT_DIR}.")
    for p in paths[:20]:
        print(f"- {p}")
    if len(paths) > 20:
        print(f"... +{len(paths) - 20} more")
