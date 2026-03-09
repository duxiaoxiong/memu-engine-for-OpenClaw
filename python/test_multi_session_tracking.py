#!/usr/bin/env python3
"""Regression checks for multi-session discovery and conversion."""

import json
import os
import shutil
import tempfile


TEST_DIR = tempfile.mkdtemp(prefix="memu_multi_session_")
SESSIONS_DIR = os.path.join(TEST_DIR, "sessions")
DATA_DIR = os.path.join(TEST_DIR, "data")

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

os.environ["OPENCLAW_SESSIONS_DIR"] = SESSIONS_DIR
os.environ["MEMU_DATA_DIR"] = DATA_DIR
os.environ["MEMU_MAX_MESSAGES_PER_SESSION"] = "1"

from convert_sessions import convert, discover_all_session_files


def _write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _write_session(session_id: str, message_text: str) -> None:
    with open(os.path.join(SESSIONS_DIR, f"{session_id}.jsonl"), "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "type": "message",
                    "id": f"{session_id}-1",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": message_text}],
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _write_session_in(root: str, session_id: str, message_text: str) -> None:
    with open(os.path.join(root, f"{session_id}.jsonl"), "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "type": "message",
                    "id": f"{session_id}-1",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": message_text}],
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def main() -> int:
    try:
        _write_json(
            os.path.join(SESSIONS_DIR, "sessions.json"),
            {
                "agent:main:discord:direct:xxx": {
                    "sessionId": "session-1",
                    "updatedAt": 1773013882506,
                },
                "agent:main:main": {
                    "sessionId": "session-2",
                    "updatedAt": 1773013874506,
                },
                "agent:main:discord:channel:yyy": {
                    "sessionId": "session-3",
                    "updatedAt": 1773013868506,
                },
            },
        )
        _write_session("session-1", "from discord dm")
        _write_session("session-2", "from main channel")
        _write_session("session-3", "from discord channel")

        discovered = discover_all_session_files(SESSIONS_DIR, ["main"])
        assert discovered["main"] == [
            os.path.join(SESSIONS_DIR, "session-1.jsonl"),
            os.path.join(SESSIONS_DIR, "session-2.jsonl"),
            os.path.join(SESSIONS_DIR, "session-3.jsonl"),
        ]

        converted = convert(agent_name="main")
        basenames = sorted(os.path.basename(path) for path in converted)
        assert basenames == [
            "session-1.part000.json",
            "session-2.part000.json",
            "session-3.part000.json",
        ], basenames

        per_agent_dir = os.path.join(TEST_DIR, "agents", "main", "sessions")
        os.makedirs(per_agent_dir, exist_ok=True)
        _write_json(
            os.path.join(per_agent_dir, "sessions.json"),
            {
                "agent:main:main": {
                    "sessionId": "scoped-session",
                    "updatedAt": 1773013882506,
                },
                "telegram:slash:5843264473": {
                    "sessionId": "unscoped-session",
                    "updatedAt": 1773013882507,
                },
            },
        )
        _write_session_in(per_agent_dir, "scoped-session", "from scoped key")
        _write_session_in(per_agent_dir, "unscoped-session", "from unscoped key")

        discovered_per_agent = discover_all_session_files(per_agent_dir, ["main"])
        assert discovered_per_agent["main"] == [
            os.path.join(per_agent_dir, "scoped-session.jsonl"),
            os.path.join(per_agent_dir, "unscoped-session.jsonl"),
        ], discovered_per_agent["main"]

        print("multi-session discovery and conversion: ok")
        return 0
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
