"""
OpenClaw Session Converter

Converts OpenClaw session JSONL files to memU conversation format.

Key behaviors:
1. Process all tracked sessions for the current agent from sessions indexes
2. Skip all .deleted files (they are sub-agent archives, not user conversations)
3. Skip all other UUID sessions (they are active sub-agents)
4. Filter out system-injected messages that look like user messages
5. Only extract text content from user/assistant messages
6. Clean up metadata tags and normalize formatting
"""

import glob
import hashlib
import json
import logging
import os
import re
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_sessions_dir = os.getenv("OPENCLAW_SESSIONS_DIR")
if not _sessions_dir:
    raise ValueError("OPENCLAW_SESSIONS_DIR env var is not set")
sessions_dir: str = _sessions_dir

_memu_data_dir = os.getenv("MEMU_DATA_DIR")
if not _memu_data_dir:
    raise ValueError("MEMU_DATA_DIR env var is not set")
memu_data_dir: str = _memu_data_dir
CONVERSATIONS_ROOT_DIR = os.path.join(memu_data_dir, "conversations")
STATE_ROOT_DIR = os.path.join(memu_data_dir, "state", "convert")
STATE_VERSION = 4

SAMPLE_BYTES = 64 * 1024

logger = logging.getLogger(__name__)


def _conversation_dir(agent_name: str) -> str:
    return os.path.join(CONVERSATIONS_ROOT_DIR, agent_name)


def _legacy_conversation_dir() -> str:
    return CONVERSATIONS_ROOT_DIR


def _state_path(agent_name: str) -> str:
    return os.path.join(STATE_ROOT_DIR, f"{agent_name}.json")


def _legacy_state_path() -> str:
    return os.path.join(CONVERSATIONS_ROOT_DIR, "state.json")

SESSION_FILENAME_RE = re.compile(
    r"^(?P<session_id>.+?)\.jsonl(?:\.deleted\.(?P<deleted_ts>.+))?$"
)
DELETED_GLOB = "*.jsonl.deleted.*"
UUID_SESSION_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _extract_session_id(filename: str) -> str | None:
    m = SESSION_FILENAME_RE.match(filename)
    if not m:
        return None
    session_id = m.group("session_id")
    return session_id if session_id else None


def _extract_deleted_timestamp(filename: str) -> str:
    m = SESSION_FILENAME_RE.match(filename)
    if not m:
        return ""
    return m.group("deleted_ts") or ""


def _is_main_session(session_id: str) -> bool:
    return bool(UUID_SESSION_RE.match(session_id or ""))

# Scheme 3 (tail gating): keep an appendable tail buffer that is NOT ingested until finalized.
# Finalize when either:
# - tail reaches max_messages, or
# - no new messages for FLUSH_IDLE_SECONDS.
FLUSH_IDLE_SECONDS = int(os.getenv("MEMU_FLUSH_IDLE_SECONDS", "1800") or "1800")


def _is_force_flush_enabled() -> bool:
    """Read force-flush flag dynamically from environment.

    NOTE: convert_sessions is imported by auto_sync, so reading this at import
    time is error-prone (env may be set later by a wrapper script).
    """
    return str(os.getenv("MEMU_FORCE_FLUSH", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }


LANGUAGE_INSTRUCTIONS = {
    "zh": "[Language Context: This conversation is in Chinese. All memory summaries extracted from this conversation must be written in Chinese (中文).]",
    "en": "[Language Context: This conversation is in English. All memory summaries extracted from this conversation must be written in English.]",
    "ja": "[Language Context: This conversation is in Japanese. All memory summaries extracted from this conversation must be written in Japanese (日本語).]",
}


def _get_language_prefix() -> str | None:
    lang = os.getenv("MEMU_OUTPUT_LANG")
    if lang is None or not str(lang).strip():
        lang = os.getenv("MEMU_LANGUAGE", "auto")
    if lang == "auto" or not lang:
        return None
    if lang in LANGUAGE_INSTRUCTIONS:
        return LANGUAGE_INSTRUCTIONS[lang]
    return f"[Language Context: All memory summaries extracted from this conversation must be written in {lang}.]"


def discover_session_files(data_root: str | Path, agents: list[str]) -> dict[str, str]:
    discovered = discover_all_session_files(data_root, agents)
    return {
        agent_name: paths[0]
        for agent_name, paths in discovered.items()
        if isinstance(paths, list) and paths
    }


def discover_all_session_files(
    data_root: str | Path, agents: list[str]
) -> dict[str, list[str]]:
    root_input = Path(data_root).expanduser()
    sessions_roots: list[Path] = []
    if root_input.name == "sessions":
        sessions_roots.append(root_input)
    sessions_roots.append(root_input / "sessions")

    deduped_roots: list[Path] = []
    seen_roots: set[str] = set()
    for root in sessions_roots:
        key = str(root)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        deduped_roots.append(root)

    enabled_agents = [a.strip() for a in agents if isinstance(a, str) and a.strip()]
    enabled_agents = list(dict.fromkeys(enabled_agents))
    if not enabled_agents:
        return {}

    def _is_agent_scoped_sessions_root(root: Path, agent_name: str) -> bool:
        try:
            return (
                root.name == "sessions"
                and root.parent.name == agent_name
                and root.parent.parent.name == "agents"
            )
        except Exception:
            return False

    def _load_sessions_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except PermissionError:
            logger.warning(f"sessions index is not accessible: {path}")
            return None
        except json.JSONDecodeError as exc:
            logger.warning(f"sessions index is invalid JSON: {path} ({exc})")
            return None
        except OSError as exc:
            logger.warning(f"failed reading sessions index: {path} ({exc})")
            return None

        if not isinstance(payload, dict):
            logger.warning(f"sessions index has unexpected format (expected object): {path}")
            return None
        return payload

    def _extract_session_ids_for_agent(
        payload: dict[str, Any],
        agent_name: str,
        *,
        allow_unscoped_keys: bool = False,
    ) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            matches_agent_scope = key.startswith(f"agent:{agent_name}:")
            matches_unscoped = allow_unscoped_keys and not key.startswith("agent:")
            if not (matches_agent_scope or matches_unscoped):
                continue
            if not isinstance(value, dict):
                continue
            session_id = value.get("sessionId")
            if not isinstance(session_id, str):
                continue
            resolved = session_id.strip()
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)
            out.append(resolved)
        return out

    out: dict[str, list[str]] = {}
    legacy_cache: dict[str, dict[str, Any] | None] = {}

    for agent_name in enabled_agents:
        resolved = False

        for sessions_root in deduped_roots:
            per_agent_index = sessions_root / agent_name / "sessions.json"
            payload = _load_sessions_json(per_agent_index)
            if payload is None:
                continue

            session_ids = _extract_session_ids_for_agent(
                payload,
                agent_name,
                allow_unscoped_keys=True,
            )
            if not session_ids:
                logger.warning(
                    f"no session id found for agent '{agent_name}' in {per_agent_index}"
                )
                continue

            resolved_paths: list[str] = []
            seen_paths: set[str] = set()
            missing_session_ids: list[str] = []
            for session_id in session_ids:
                transcript_candidates = [
                    sessions_root / agent_name / f"{session_id}.jsonl",
                    sessions_root / f"{session_id}.jsonl",
                ]
                found_path = None
                for candidate in transcript_candidates:
                    candidate_key = str(candidate)
                    if candidate.exists():
                        found_path = candidate_key
                        break
                if found_path is None:
                    missing_session_ids.append(session_id)
                    continue
                if found_path in seen_paths:
                    continue
                seen_paths.add(found_path)
                resolved_paths.append(found_path)

            if resolved_paths:
                out[agent_name] = resolved_paths
                resolved = True
            elif missing_session_ids:
                logger.warning(
                    f"session file missing for agent '{agent_name}': "
                    f"{per_agent_index} (session_ids={missing_session_ids})"
                )
            if resolved:
                break

        if resolved:
            continue

        for sessions_root in deduped_roots:
            legacy_index = sessions_root / "sessions.json"
            cache_key = str(legacy_index)
            if cache_key not in legacy_cache:
                legacy_cache[cache_key] = _load_sessions_json(legacy_index)
            payload = legacy_cache[cache_key]
            if payload is None:
                continue

            session_ids = _extract_session_ids_for_agent(
                payload,
                agent_name,
                allow_unscoped_keys=_is_agent_scoped_sessions_root(sessions_root, agent_name),
            )
            if not session_ids:
                continue

            resolved_paths = []
            seen_paths: set[str] = set()
            missing_session_ids: list[str] = []
            for session_id in session_ids:
                transcript_candidates = [
                    sessions_root / f"{session_id}.jsonl",
                    sessions_root / agent_name / f"{session_id}.jsonl",
                ]
                found_path = None
                for candidate in transcript_candidates:
                    candidate_key = str(candidate)
                    if candidate.exists():
                        found_path = candidate_key
                        break
                if found_path is None:
                    missing_session_ids.append(session_id)
                    continue
                if found_path in seen_paths:
                    continue
                seen_paths.add(found_path)
                resolved_paths.append(found_path)

            if resolved_paths:
                out[agent_name] = resolved_paths
                resolved = True
            elif missing_session_ids:
                logger.warning(
                    f"legacy session file missing for agent '{agent_name}': "
                    f"{legacy_index} (session_ids={missing_session_ids})"
                )
            if resolved:
                break

    return out


def _get_agent_session_ids(sessions_dir: str, agent_name: str) -> list[str]:
    """
    获取指定 agent 的所有 session IDs。

    Args:
        sessions_dir: sessions 目录路径
        agent_name: agent 名称 (例如 "main", "prometheus")

    Returns:
        session ID 列表
    """
    discovered = discover_all_session_files(sessions_dir, [agent_name])
    session_files = discovered.get(agent_name, [])
    out: list[str] = []
    seen: set[str] = set()
    for session_file in session_files:
        session_id = _extract_session_id(os.path.basename(session_file))
        if not session_id:
            session_id = Path(session_file).stem
        if not session_id or session_id in seen:
            continue
        seen.add(session_id)
        out.append(session_id)
    return out


def _get_main_session_id(agent_name: str = "main") -> str | None:
    session_ids = _get_agent_session_ids(sessions_dir, agent_name)
    return session_ids[0] if session_ids else None


def _resolve_session_file(session_id: str, agent_name: str | None = None) -> str | None:
    """Resolve transcript file for a session id.

    Prefer active `<id>.jsonl`; fall back to latest reset/archive transcript
    generated by OpenClaw session reset flow.
    """
    primary_candidates = []
    if agent_name:
        primary_candidates.append(os.path.join(sessions_dir, agent_name, f"{session_id}.jsonl"))
    primary_candidates.append(os.path.join(sessions_dir, f"{session_id}.jsonl"))
    for primary in primary_candidates:
        if os.path.exists(primary):
            return primary

    # OpenClaw reset archives old transcript as:
    #   <id>.jsonl.reset.<timestamp>
    # Keep a conservative fallback set for historical variants.
    candidates = []
    if agent_name:
        candidates.extend(
            glob.glob(os.path.join(sessions_dir, agent_name, f"{session_id}.jsonl.reset.*"))
        )
        candidates.extend(
            glob.glob(os.path.join(sessions_dir, agent_name, f"{session_id}.jsonl.bak*"))
        )
    candidates.extend(glob.glob(os.path.join(sessions_dir, f"{session_id}.jsonl.reset.*")))
    candidates.extend(glob.glob(os.path.join(sessions_dir, f"{session_id}.jsonl.bak*")))
    if not candidates:
        return None

    try:
        return max(candidates, key=lambda p: os.path.getmtime(p))
    except Exception:
        return candidates[-1]


# Regex patterns for filtering
RE_NO_REPLY = re.compile(r"\bNO_REPLY\b\W*$")
RE_TOOL_INVOKE = re.compile(r"^Call the tool \w+ with .*\.$")
RE_SYSTEM_PREFIX = re.compile(r"^System:\s*\[")
RE_SYSTEM_ENVELOPE = re.compile(
    r"^System:\s*\[[^\]]+\]\s*([A-Za-z][\w\- ]{1,40}):\s*(.*)$",
    re.DOTALL,
)

# Directive patterns (assistant responses to slash commands)
DIRECTIVE_PATTERNS = [
    r"^Model set to .+\.$",
    r"^Model reset to default .+\.$",
    r"^Thinking level set to .+\.$",
    r"^Thinking disabled\.$",
    r"^Verbose logging (enabled|disabled|set to .+)\.$",
    r"^Reasoning (visibility|stream) (enabled|disabled)\.$",
    r"^Elevated mode (disabled|set to .+)\.$",
    r"^Queue mode (set to .+|reset to default)\.$",
    r"^Queue debounce set to .+\.$",
    r"^Auth profile set to .+\.$",
    r"^Exec defaults set .+\.$",
    r"^Current: .+\n\nSwitch: /model",
]
RE_DIRECTIVE = re.compile("|".join(DIRECTIVE_PATTERNS), re.MULTILINE | re.DOTALL)

# Cleanup patterns
RE_MESSAGE_ID = re.compile(r"\[message_id:\s*[a-f0-9-]+\]\s*", re.IGNORECASE)
RE_TELEGRAM_FULL = re.compile(
    r"\[Telegram\s+(?:DU\s+)?(?:id:\d+\s+)?(?:\+\d+[smhd]\s+)?(?:\d{4}-\d{2}-\d{2}\s+)?(\d{1,2}:\d{2})\s+(UTC|GMT[+-]?\d*)\]",
    re.IGNORECASE,
)
RE_SYSTEM_LINE = re.compile(r"^System:\s*\[[^\]]+\][^\n]*\n+", re.MULTILINE)
RE_COMPACTION_LINE = re.compile(
    r"^.*Compacted \([^)]+\).*Context [^\n]+\n*", re.MULTILINE
)


def _is_system_injected_content(text: str) -> bool:
    """Detect system-injected messages that masquerade as user messages."""
    if not text:
        return False
    text_stripped = text.strip()

    if RE_NO_REPLY.search(text):
        return True
    if RE_SYSTEM_PREFIX.match(text_stripped):
        return True
    if text_stripped.startswith("This session is being continued"):
        return True
    if "A new session was started via /new or /reset" in text_stripped:
        return True
    if RE_TOOL_INVOKE.match(text_stripped):
        return True

    return False


def _is_directive_response(text: str) -> bool:
    """Check if assistant message is a directive acknowledgement."""
    if not text:
        return False
    return bool(RE_DIRECTIVE.match(text.strip()))


def _is_system_injected_entry(entry: dict) -> bool:
    """Check if a JSONL entry is a system-injected message."""
    if entry.get("type") != "message":
        return False

    if "toolUseResult" in entry:
        return True
    if "sourceToolUseID" in entry:
        return True
    if entry.get("isMeta"):
        return True

    msg = entry.get("message", {})
    if msg.get("role") != "user":
        return False

    content_list = msg.get("content", [])
    for part in content_list:
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text", "")
            if _is_system_injected_content(text):
                return True

    return False


def _handle_scheduled_system_payload(text: str) -> str:
    """Handle long system envelopes (e.g., scheduled cron payloads) in a generic way.

    Controlled by env vars:
      - MEMU_FILTER_SCHEDULED_SYSTEM_MESSAGES: true|false (default true)
      - MEMU_SCHEDULED_SYSTEM_MODE: event|drop|keep (default event)
      - MEMU_SCHEDULED_SYSTEM_MIN_CHARS: int (default 500)
    """
    enabled = str(os.getenv("MEMU_FILTER_SCHEDULED_SYSTEM_MESSAGES", "true")).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    if not enabled:
        return text

    m = RE_SYSTEM_ENVELOPE.match(text.strip())
    if not m:
        return text

    event_name = (m.group(1) or "System").strip()
    body = (m.group(2) or "").strip()

    try:
        min_chars = int(os.getenv("MEMU_SCHEDULED_SYSTEM_MIN_CHARS", "500") or "500")
    except Exception:
        min_chars = 500
    min_chars = max(64, min_chars)

    # Only treat as scheduled payload when body is large enough.
    if len(body) < min_chars:
        return text

    mode = str(os.getenv("MEMU_SCHEDULED_SYSTEM_MODE", "event")).strip().lower()
    if mode == "keep":
        return text
    if mode == "drop":
        return ""

    # default: event
    return f"[System event: {event_name} delivered]"


def _clean_message_text(text: str) -> str:
    """Remove metadata tags and normalize formatting for memory storage."""
    if not text:
        return ""

    text = _handle_scheduled_system_payload(text)
    if not text:
        return ""

    text = RE_MESSAGE_ID.sub("", text)
    text = RE_SYSTEM_LINE.sub("", text)
    text = RE_COMPACTION_LINE.sub("", text)
    text = RE_TELEGRAM_FULL.sub(r"[Telegram \1 \2]", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_text_parts(content_list: list[dict[str, Any]]) -> str:
    """Extract only plain text content, ignoring tool calls, thinking, images, etc."""
    parts: list[str] = []
    for part in content_list or []:
        if isinstance(part, dict) and part.get("type") == "text":
            t = part.get("text")
            if isinstance(t, str) and t.strip():
                parts.append(t)
    return "\n".join(parts).strip()


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _sha256_file_sample(*, file_path: str, start: int, length: int) -> str:
    """Hash a slice of the file (best-effort)."""
    try:
        with open(file_path, "rb") as f:
            f.seek(max(0, start))
            return _sha256_bytes(f.read(max(0, length)))
    except FileNotFoundError:
        return ""


def _load_state(
    *, state_path: str | None = None, legacy_state_path: str | None = None
) -> dict[str, Any]:
    resolved_state_path = state_path or _legacy_state_path()
    candidates: list[str] = [resolved_state_path]
    if legacy_state_path and legacy_state_path != resolved_state_path:
        candidates.append(legacy_state_path)
    if resolved_state_path not in candidates:
        candidates.insert(0, resolved_state_path)

    for candidate in candidates:
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                s = json.load(f)
            ver = s.get("version")
            # Migrate v3 -> v4 in-place to avoid reprocessing and (critically) avoid
            # overwriting already-ingested part files with different chunk sizing.
            if ver == 3 and STATE_VERSION == 4:
                return {
                    "version": STATE_VERSION,
                    "sessions": s.get("sessions", {}),
                }
            if ver != STATE_VERSION:
                return {"version": STATE_VERSION, "sessions": {}}
            return {"version": STATE_VERSION, "sessions": s.get("sessions", {})}
        except Exception:
            continue

    return {"version": STATE_VERSION, "sessions": {}}


def _save_state(state: dict[str, Any], *, state_path: str | None = None) -> None:
    resolved_state_path = state_path or _legacy_state_path()
    os.makedirs(os.path.dirname(resolved_state_path), exist_ok=True)
    tmp = resolved_state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, resolved_state_path)


def _part_path(session_id: str, part_idx: int, *, out_dir: str | None = None) -> str:
    resolved_out_dir = out_dir or _legacy_conversation_dir()
    return os.path.join(resolved_out_dir, f"{session_id}.part{part_idx:03d}.json")


def _tail_tmp_path(session_id: str, *, out_dir: str | None = None) -> str:
    resolved_out_dir = out_dir or _legacy_conversation_dir()
    return os.path.join(resolved_out_dir, f"{session_id}.tail.tmp.json")


def _read_part_messages(part_path: str) -> list[dict[str, str]]:
    """Return messages in a part file (including system if present)."""
    with open(part_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    msgs: list[dict[str, str]] = []
    for m in data:
        if (
            isinstance(m, dict)
            and isinstance(m.get("role"), str)
            and isinstance(m.get("content"), str)
        ):
            msgs.append({"role": m["role"], "content": m["content"]})
    return msgs


def _strip_system_prefix(
    part_messages: list[dict[str, str]], lang_prefix: str | None
) -> list[dict[str, str]]:
    if not part_messages:
        return []
    if (
        lang_prefix
        and part_messages[0].get("role") == "system"
        and part_messages[0].get("content") == lang_prefix
    ):
        return part_messages[1:]
    return part_messages


def _write_part_json(
    *,
    part_messages: list[dict[str, str]],
    out_path: str,
    lang_prefix: str | None,
) -> tuple[bool, str]:
    """Write part file if content differs. Returns (changed, sha256)."""
    if lang_prefix:
        payload = [{"role": "system", "content": lang_prefix}, *part_messages]
    else:
        payload = part_messages

    encoded = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    new_sha = _sha256_bytes(encoded)

    try:
        with open(out_path, "rb") as f:
            old_sha = _sha256_bytes(f.read())
        if old_sha == new_sha:
            return (False, new_sha)
    except FileNotFoundError:
        pass
    except Exception:
        pass

    with open(out_path, "wb") as f:
        f.write(encoded)
    return (True, new_sha)


@dataclass
class _ReadResult:
    messages: list[dict[str, str]]
    new_offset: int


def _read_messages_from_jsonl(*, file_path: str, start_offset: int) -> _ReadResult:
    """Read OpenClaw session JSONL from byte offset and extract user/assistant messages."""
    messages: list[dict[str, str]] = []
    new_offset = start_offset

    with open(file_path, "rb") as f:
        f.seek(max(0, start_offset))
        while True:
            line_start = f.tell()
            line = f.readline()
            if not line:
                break

            complete_line = line.endswith(b"\n")
            try:
                entry = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                if not complete_line:
                    break
                new_offset = f.tell()
                continue

            new_offset = f.tell()

            if entry.get("type") != "message":
                continue

            if _is_system_injected_entry(entry):
                continue

            msg_obj = entry.get("message", {})
            role = msg_obj.get("role")

            if role not in ("user", "assistant"):
                continue

            content_list = msg_obj.get("content", [])
            text = _extract_text_parts(content_list)

            if not text:
                continue

            if role == "user" and _is_system_injected_content(text):
                continue

            if role == "assistant" and _is_directive_response(text):
                continue

            if RE_NO_REPLY.search(text):
                continue

            text = _clean_message_text(text)
            if not text:
                continue

            messages.append({"role": role, "content": text})

    return _ReadResult(messages=messages, new_offset=new_offset)


def convert(
    *,
    since_ts: float | None = None,
    session_id: str | None = None,
    agent_name: str | None = None,
    memory_root: str | Path | None = None,
    force_flush: bool = False,
) -> list[str]:
    """Convert one OpenClaw session transcript to memU conversation parts.

    - default: current tracked sessions from sessions.json
    - optional: explicit session_id (used to salvage reset/archived sessions)
    """
    try:
        from memu.storage_layout import memory_root_path as _memory_root_path

        resolved_memory_root = str(_memory_root_path(memory_root))
    except Exception:
        fallback_root = (
            Path(memory_root).expanduser()
            if memory_root is not None
            else Path(
                os.getenv("MEMU_MEMORY_ROOT", "~/.openclaw/memUdata/memory")
            ).expanduser()
        )
        resolved_memory_root = str(fallback_root)
    os.environ["MEMU_MEMORY_ROOT"] = resolved_memory_root

    # Default to 60 messages per finalized part (Scheme 4).
    max_messages = int(os.getenv("MEMU_MAX_MESSAGES_PER_SESSION", "60") or "60")

    resolved_agent = (agent_name or os.getenv("MEMU_AGENT_NAME") or "main").strip() or "main"
    if session_id is None:
        discovered = discover_all_session_files(sessions_dir, [resolved_agent])
        session_files = discovered.get(resolved_agent, [])
        if not session_files:
            print(
                f"[convert_sessions] No session found in sessions indexes for agent: {resolved_agent}",
                file=__import__("sys").stderr,
            )
            return []

        converted_all: list[str] = []
        seen_paths: set[str] = set()
        for session_file in session_files:
            target_session_id = (
                _extract_session_id(os.path.basename(session_file))
                or Path(session_file).stem
            )
            if not target_session_id:
                continue
            converted_paths = convert(
                since_ts=since_ts,
                session_id=target_session_id,
                agent_name=resolved_agent,
                memory_root=resolved_memory_root,
                force_flush=force_flush,
            )
            for path in converted_paths:
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                converted_all.append(path)
        return converted_all

    out_dir = _conversation_dir(resolved_agent)
    legacy_out_dir = _legacy_conversation_dir()
    state_path = _state_path(resolved_agent)
    legacy_state_path = _legacy_state_path()
    os.makedirs(out_dir, exist_ok=True)
    if session_id:
        target_session_id = session_id
        session_file = _resolve_session_file(target_session_id, resolved_agent)
        if not session_file:
            print(
                f"[convert_sessions] Session file not found for: {target_session_id}",
                file=__import__("sys").stderr,
            )
            return []

    state = _load_state(state_path=state_path, legacy_state_path=legacy_state_path)
    sessions_state: dict[str, Any] = state.setdefault("sessions", {})
    converted: list[str] = []
    lang_prefix = _get_language_prefix()

    file_path = session_file
    session_id = target_session_id

    try:
        st = os.stat(file_path)
    except FileNotFoundError:
        _save_state(state, state_path=state_path)
        return converted

    prev = sessions_state.get(session_id) if isinstance(sessions_state, dict) else None
    if not isinstance(prev, dict):
        prev = {}

    prev_offset = int(prev.get("last_offset", 0) or 0)
    prev_size = int(prev.get("last_size", 0) or 0)
    prev_dev = prev.get("device")
    prev_ino = prev.get("inode")
    prev_lang = prev.get("lang_prefix")
    prev_part_count = int(prev.get("part_count", 0) or 0)
    prev_tail_count = int(prev.get("tail_part_messages", 0) or 0)
    prev_head_sha = str(prev.get("head_sha256", "") or "")
    prev_tail_sha = str(prev.get("tail_sha256", "") or "")

    cur_size = int(st.st_size)
    cur_mtime = float(st.st_mtime)
    cur_dev = int(getattr(st, "st_dev", 0))
    cur_ino = int(getattr(st, "st_ino", 0))

    def _should_idle_flush(prev_state: dict[str, Any]) -> bool:
        # If there's a staged tail and it's been idle for long enough, finalize it.
        tail_count0 = int(prev_state.get("tail_part_messages", 0) or 0)
        if tail_count0 <= 0:
            return False

        # Manual override: allow callers (eg. a tool) to force-finalize the staged tail.
        if force_flush or _is_force_flush_enabled():
            return True
        last_activity = prev_state.get("tail_last_activity_ts")
        try:
            last_activity_f = (
                float(last_activity) if last_activity is not None else None
            )
        except Exception:
            last_activity_f = None
        if last_activity_f is None:
            # Fall back to the session file mtime; less precise but avoids starvation.
            last_activity_f = cur_mtime
        return (time.time() - last_activity_f) >= FLUSH_IDLE_SECONDS

    if since_ts is not None and cur_mtime <= since_ts:
        # Even if the session file hasn't changed, we may still need to finalize a staged tail
        # after the idle window.
        if (not prev or cur_size <= prev_offset) and not _should_idle_flush(prev):
            _save_state(state, state_path=state_path)
            return converted

    append_only = True
    if prev and (prev_dev is not None and prev_ino is not None):
        if int(prev_dev) != cur_dev or int(prev_ino) != cur_ino:
            append_only = False
    if cur_size < prev_offset:
        append_only = False
    if prev_lang != lang_prefix:
        append_only = False

    if append_only and prev_offset > 0 and (prev_head_sha or prev_tail_sha):
        head_len = min(SAMPLE_BYTES, cur_size)
        head_sha = _sha256_file_sample(file_path=file_path, start=0, length=head_len)
        tail_start = max(0, prev_offset - SAMPLE_BYTES)
        tail_len = max(0, min(SAMPLE_BYTES, prev_offset - tail_start))
        tail_sha = _sha256_file_sample(
            file_path=file_path, start=tail_start, length=tail_len
        )
        if (prev_head_sha and head_sha != prev_head_sha) or (
            prev_tail_sha and tail_sha != prev_tail_sha
        ):
            append_only = False

    def _load_tail_messages() -> list[dict[str, str]]:
        candidates = [
            _tail_tmp_path(session_id, out_dir=out_dir),
            _tail_tmp_path(session_id, out_dir=legacy_out_dir),
        ]
        seen: set[str] = set()
        for tail_path in candidates:
            if tail_path in seen:
                continue
            seen.add(tail_path)
            try:
                tail_part = _read_part_messages(tail_path)
                return _strip_system_prefix(tail_part, lang_prefix)
            except FileNotFoundError:
                continue
            except Exception:
                continue
        return []

    def _write_tail_messages(msgs: list[dict[str, str]]) -> None:
        tail_path = _tail_tmp_path(session_id, out_dir=out_dir)
        if not msgs:
            for candidate in {
                tail_path,
                _tail_tmp_path(session_id, out_dir=legacy_out_dir),
            }:
                try:
                    os.remove(candidate)
                except FileNotFoundError:
                    pass
            return
        _write_part_json(
            part_messages=msgs, out_path=tail_path, lang_prefix=lang_prefix
        )

    def _now_ts() -> float:
        return time.time()

    def _finalize_tail_if_due(
        *,
        tail_msgs: list[dict[str, str]],
        is_idle: bool,
        part_count_in: int,
    ) -> tuple[bool, int, list[str]]:
        """Finalize current tail into an immutable part if flush conditions are met."""
        if not tail_msgs:
            return (False, part_count_in, [])

        should_flush = (len(tail_msgs) >= max_messages) or is_idle
        if not should_flush:
            return (False, part_count_in, [])

        new_paths: list[str] = []
        # Flush in fixed-size chunks.
        buf = list(tail_msgs)
        while len(buf) >= max_messages:
            chunk = buf[:max_messages]
            buf = buf[max_messages:]
            out_path = _part_path(session_id, part_count_in, out_dir=out_dir)
            changed, _ = _write_part_json(
                part_messages=chunk, out_path=out_path, lang_prefix=lang_prefix
            )
            if changed:
                new_paths.append(out_path)
            part_count_in += 1

        # If idle-flush and there is remainder (< max_messages), flush remainder as its own part.
        if buf and is_idle:
            out_path = _part_path(session_id, part_count_in, out_dir=out_dir)
            changed, _ = _write_part_json(
                part_messages=buf, out_path=out_path, lang_prefix=lang_prefix
            )
            if changed:
                new_paths.append(out_path)
            part_count_in += 1
            buf = []

        # Update staged tail file.
        _write_tail_messages(buf)
        return (True, part_count_in, new_paths)

    if not prev or not append_only:
        read_res = _read_messages_from_jsonl(file_path=file_path, start_offset=0)
        messages = read_res.messages

        tail_count = 0

        new_part_count = 0
        if max_messages <= 0:
            out_path = os.path.join(out_dir, f"{session_id}.json")
            changed, _ = _write_part_json(
                part_messages=messages, out_path=out_path, lang_prefix=lang_prefix
            )
            new_part_count = 1 if messages else 0
            if changed:
                converted.append(out_path)
        else:
            # Write only full parts as immutable .partNNN.json
            full_count = len(messages) // max_messages
            for part_idx in range(full_count):
                start = part_idx * max_messages
                end = start + max_messages
                part_path = _part_path(session_id, part_idx, out_dir=out_dir)
                changed, _ = _write_part_json(
                    part_messages=messages[start:end],
                    out_path=part_path,
                    lang_prefix=lang_prefix,
                )
                new_part_count += 1
                if changed:
                    converted.append(part_path)

            # Remainder becomes staged tail (NOT ingested until finalized).
            tail_msgs = messages[full_count * max_messages :]
            if tail_msgs and _should_idle_flush({"tail_part_messages": len(tail_msgs)}):
                # Session is already idle; finalize remainder immediately to avoid leaving tail forever.
                part_path = _part_path(session_id, new_part_count, out_dir=out_dir)
                changed, _ = _write_part_json(
                    part_messages=tail_msgs, out_path=part_path, lang_prefix=lang_prefix
                )
                new_part_count += 1
                if changed:
                    converted.append(part_path)
                tail_msgs = []

            _write_tail_messages(tail_msgs)
            tail_count = len(tail_msgs)

        if max_messages > 0 and prev_part_count and new_part_count < prev_part_count:
            for part_idx in range(new_part_count, prev_part_count):
                try:
                    os.remove(_part_path(session_id, part_idx, out_dir=out_dir))
                except FileNotFoundError:
                    pass

        head_len = min(SAMPLE_BYTES, cur_size)
        head_sha = _sha256_file_sample(file_path=file_path, start=0, length=head_len)
        tail_start = max(0, read_res.new_offset - SAMPLE_BYTES)
        tail_len = max(0, min(SAMPLE_BYTES, read_res.new_offset - tail_start))
        tail_sha = _sha256_file_sample(
            file_path=file_path, start=tail_start, length=tail_len
        )

        # tail_count already computed for max_messages>0 rebuild; otherwise compute from file.
        if max_messages <= 0:
            tail_count = 0
            if new_part_count > 0:
                try:
                    last_msgs = _read_part_messages(
                        os.path.join(out_dir, f"{session_id}.json")
                    )
                    tail_count = len(_strip_system_prefix(last_msgs, lang_prefix))
                except Exception:
                    tail_count = 0

        sessions_state[session_id] = {
            "file_path": file_path,
            "device": cur_dev,
            "inode": cur_ino,
            "last_offset": int(read_res.new_offset),
            "last_size": cur_size,
            "last_mtime": cur_mtime,
            "part_count": int(new_part_count),
            "tail_part_messages": int(tail_count),
            "tail_last_activity_ts": _now_ts() if tail_count > 0 else None,
            "lang_prefix": lang_prefix,
            "head_sha256": head_sha,
            "tail_sha256": tail_sha,
        }
        _save_state(state, state_path=state_path)
        return converted

    if cur_size == prev_offset:
        # No new bytes. Still allow idle flush of staged tail.
        if max_messages > 0 and _should_idle_flush(prev):
            tail_msgs = _load_tail_messages()
            did, part_count2, new_paths = _finalize_tail_if_due(
                tail_msgs=tail_msgs,
                is_idle=True,
                part_count_in=prev_part_count,
            )
            if did:
                converted.extend(new_paths)
                prev_part_count = part_count2
                prev_tail_count = 0
                sessions_state[session_id] = {
                    **prev,
                    "part_count": int(part_count2),
                    "tail_part_messages": 0,
                }
        _save_state(state, state_path=state_path)
        return converted

    read_res = _read_messages_from_jsonl(file_path=file_path, start_offset=prev_offset)
    new_messages = read_res.messages
    if not new_messages and read_res.new_offset == prev_offset:
        _save_state(state, state_path=state_path)
        return converted

    part_count = prev_part_count
    tail_count = prev_tail_count

    if max_messages <= 0:
        out_path = os.path.join(out_dir, f"{session_id}.json")
        full_res = _read_messages_from_jsonl(file_path=file_path, start_offset=0)
        changed, _ = _write_part_json(
            part_messages=full_res.messages,
            out_path=out_path,
            lang_prefix=lang_prefix,
        )
        if changed:
            converted.append(out_path)
        part_count = 1 if full_res.messages else 0
        tail_count = len(full_res.messages)
        read_res = full_res
    else:
        # Scheme 3: stage all incremental messages in tail.tmp, and only emit finalized parts.
        tail_buf = _load_tail_messages()
        tail_buf.extend(new_messages)

        # Finalize any full chunks immediately; keep remainder staged.
        while len(tail_buf) >= max_messages:
            chunk = tail_buf[:max_messages]
            tail_buf = tail_buf[max_messages:]
            part_path = _part_path(session_id, part_count, out_dir=out_dir)
            changed, _ = _write_part_json(
                part_messages=chunk, out_path=part_path, lang_prefix=lang_prefix
            )
            if changed:
                converted.append(part_path)
            part_count += 1

        # If idle window already exceeded (or manual force flush), flush remainder too.
        did_flush_idle = bool(tail_buf) and _should_idle_flush(
            {"tail_part_messages": len(tail_buf)}
        )
        if did_flush_idle:
            part_path = _part_path(session_id, part_count, out_dir=out_dir)
            changed, _ = _write_part_json(
                part_messages=tail_buf, out_path=part_path, lang_prefix=lang_prefix
            )
            if changed:
                converted.append(part_path)
            part_count += 1
            tail_buf = []

        _write_tail_messages(tail_buf)
        tail_count = len(tail_buf)

    head_len = min(SAMPLE_BYTES, cur_size)
    head_sha = _sha256_file_sample(file_path=file_path, start=0, length=head_len)
    tail_start = max(0, read_res.new_offset - SAMPLE_BYTES)
    tail_len = max(0, min(SAMPLE_BYTES, read_res.new_offset - tail_start))
    tail_sha = _sha256_file_sample(
        file_path=file_path, start=tail_start, length=tail_len
    )

    sessions_state[session_id] = {
        "file_path": file_path,
        "device": cur_dev,
        "inode": cur_ino,
        "last_offset": int(read_res.new_offset),
        "last_size": cur_size,
        "last_mtime": cur_mtime,
        "part_count": int(part_count),
        "tail_part_messages": int(tail_count),
        "tail_last_activity_ts": (
            prev.get("tail_last_activity_ts") if tail_count > 0 else None
        ),
        "lang_prefix": lang_prefix,
        "head_sha256": head_sha,
        "tail_sha256": tail_sha,
    }

    _save_state(state, state_path=state_path)
    return converted


def convert_agents(
    *,
    agents: list[str],
    since_ts: float | None = None,
    memory_root: str | Path | None = None,
    force_flush: bool = False,
) -> dict[str, Any]:
    results: dict[str, Any] = {"success": [], "failed": [], "errors": [], "paths": {}}

    for agent_name in agents:
        if not isinstance(agent_name, str) or not agent_name.strip():
            continue
        resolved_agent = agent_name.strip()
        try:
            converted_paths = convert(
                since_ts=since_ts,
                agent_name=resolved_agent,
                memory_root=memory_root,
                force_flush=force_flush,
            )
            results["success"].append(resolved_agent)
            results["paths"][resolved_agent] = converted_paths
        except Exception as exc:
            tb = traceback.format_exc()
            err = {
                "agent": resolved_agent,
                "error": str(exc),
                "traceback": tb,
            }
            logger.exception(
                "per-agent conversion failed",
                extra={"agent": resolved_agent, "operation": "convert"},
            )
            results["failed"].append({"agent": resolved_agent, "error": str(exc)})
            results["errors"].append(err)

    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--debug":
        current_agent = (os.getenv("MEMU_AGENT_NAME") or "main").strip() or "main"
        main_id = _get_main_session_id(current_agent)
        print(f"Main session ID: {main_id}")
        if main_id:
            main_file = os.path.join(sessions_dir, f"{main_id}.jsonl")
            print(f"Main session file: {main_file}")
            print(f"Exists: {os.path.exists(main_file)}")
        sys.exit(0)

    current_agent = (os.getenv("MEMU_AGENT_NAME") or "main").strip() or "main"
    paths = convert(agent_name=current_agent)
    print(
        f"Converted main session into {len(paths)} part(s) in {_conversation_dir(current_agent)}."
    )
    for p in paths[:20]:
        print(f"- {p}")
    if len(paths) > 20:
        print(f"... +{len(paths) - 20} more")
