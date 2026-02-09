import glob
import re

SESSION_FILENAME_RE = re.compile(r"^(.+?)\.jsonl(?:\.deleted\.\d{4}-\d{2}-\d{2}T[\d:\-]+(?:\.\d+)?Z?)?$")

# UUID pattern to identify main sessions (vs sub-agent sessions with custom labels)
UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)

# Pattern to extract timestamp from .deleted filename for chronological sorting
DELETED_TIMESTAMP_RE = re.compile(r'\.deleted\.(\d{4}-\d{2}-\d{2}T[\d\-:.]+Z?)$')

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

# Use env var for flexibility, no default fallback to avoid writing to wrong place
sessions_dir = os.getenv("OPENCLAW_SESSIONS_DIR")
if not sessions_dir:
    raise ValueError("OPENCLAW_SESSIONS_DIR env var is not set")
SESSION_GLOB = os.path.join(sessions_dir, "*.jsonl")
DELETED_GLOB = os.path.join(sessions_dir, "*.jsonl.deleted.*")

memu_data_dir = os.getenv("MEMU_DATA_DIR")
if not memu_data_dir:
    raise ValueError("MEMU_DATA_DIR env var is not set")
OUT_DIR = os.path.join(memu_data_dir, "conversations")
STATE_FILE = os.path.join(OUT_DIR, "state.json")

STATE_PATH = os.path.join(OUT_DIR, "state.json")
STATE_VERSION = 1

# How many bytes to sample from head/tail to detect mid-file edits.
SAMPLE_BYTES = 64 * 1024

# Language instruction prefix for memory extraction
LANGUAGE_INSTRUCTIONS = {
    "zh": "[Language Context: This conversation is in Chinese. All memory summaries extracted from this conversation must be written in Chinese (中文).]",
    "en": "[Language Context: This conversation is in English. All memory summaries extracted from this conversation must be written in English.]",
    "ja": "[Language Context: This conversation is in Japanese. All memory summaries extracted from this conversation must be written in Japanese (日本語).]",
}


def _get_language_prefix() -> str | None:
    lang = os.getenv("MEMU_OUTPUT_LANG", "auto")
    if lang == "auto" or not lang:
        return None
    if lang in LANGUAGE_INSTRUCTIONS:
        return LANGUAGE_INSTRUCTIONS[lang]
    return f"[Language Context: All memory summaries extracted from this conversation must be written in {lang}.]"


def _extract_text_parts(content_list: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for part in content_list or []:
        # Only keep plain text. Ignore tool calls, thinking, etc.
        if isinstance(part, dict) and part.get("type") == "text":
            t = part.get("text")
            if isinstance(t, str) and t.strip():
                parts.append(t)
    return "\n".join(parts).strip()


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()



def _extract_session_id(filename: str) -> str | None:
    """Extract session_id from filename, handling both .jsonl and .deleted variants."""
    m = SESSION_FILENAME_RE.match(filename)
    return m.group(1) if m else None


def _is_main_session(session_id: str) -> bool:
    """Check if session_id is a main session (UUID format) vs sub-agent session (custom label).
    
    Main sessions use UUID format (e.g., '75fcef11-456c-42d9-beaf-4caa7c5d3eab').
    Sub-agent sessions use custom labels (e.g., 'verify-prepush', 'my-task-1').
    """
    return bool(UUID_PATTERN.match(session_id))


def _extract_deleted_timestamp(filename: str) -> str:
    """Extract timestamp from .deleted filename for chronological sorting.
    
    Returns empty string if no timestamp found (will sort to beginning).
    """
    m = DELETED_TIMESTAMP_RE.search(filename)
    return m.group(1) if m else ""


def _get_session_start_time(file_path: str) -> str:
    """Extract session start timestamp from the first line of a session file.
    
    Session files start with a header like:
    {"type":"session","version":3,"id":"...","timestamp":"2026-02-06T15:10:50.886Z",...}
    
    Returns the timestamp string for sorting, or empty string if not found.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            first_line = f.readline()
            if first_line:
                data = json.loads(first_line)
                if data.get("type") == "session":
                    return data.get("timestamp", "") or ""
    except Exception:
        pass
    return ""


def _sha256_file_sample(*, file_path: str, start: int, length: int) -> str:
    """Hash a slice of the file (best-effort)."""
    try:
        with open(file_path, "rb") as f:
            f.seek(max(0, start))
            return _sha256_bytes(f.read(max(0, length)))
    except FileNotFoundError:
        return ""


def _load_state() -> dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {"version": STATE_VERSION, "sessions": {}, "processed_deleted": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        if s.get("version") != STATE_VERSION:
            return {"version": STATE_VERSION, "sessions": {}, "processed_deleted": []}
        sessions = s.get("sessions", {})
        processed_deleted = s.get("processed_deleted", [])
        if not isinstance(processed_deleted, list):
            processed_deleted = []
        return {"version": STATE_VERSION, "sessions": sessions, "processed_deleted": processed_deleted}
    except Exception:
        return {"version": STATE_VERSION, "sessions": {}, "processed_deleted": []}

def _save_state(state: dict[str, Any]) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


def _part_path(session_id: str, part_idx: int) -> str:
    return os.path.join(OUT_DIR, f"{session_id}.part{part_idx:03d}.json")


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
        # If the existing file is unreadable, overwrite.
        pass

    with open(out_path, "wb") as f:
        f.write(encoded)
    return (True, new_sha)


@dataclass
class _ReadResult:
    messages: list[dict[str, str]]
    new_offset: int


def _read_messages_from_jsonl(*, file_path: str, start_offset: int) -> _ReadResult:
    """Read OpenClaw session JSONL from byte offset and extract user/assistant messages.

    Offset advances only to the end of the last *complete* line read. If the file ends
    with an incomplete JSON line, we do not advance past that line.
    """

    messages: list[dict[str, str]] = []
    new_offset = start_offset

    with open(file_path, "rb") as f:
        f.seek(max(0, start_offset))
        while True:
            line_start = f.tell()
            line = f.readline()
            if not line:
                break

            # If the writer is mid-line (no trailing newline) and JSON parsing fails,
            # keep the offset pinned so we can retry next sync.
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
            msg_obj = entry.get("message", {})
            role = msg_obj.get("role")
            content_list = msg_obj.get("content", [])
            text = _extract_text_parts(content_list)
            if text and role in ("user", "assistant"):
                messages.append({"role": role, "content": text})

    return _ReadResult(messages=messages, new_offset=new_offset)


def convert(*, since_ts: float | None = None) -> list[str]:
    os.makedirs(OUT_DIR, exist_ok=True)

    max_messages = int(os.getenv("MEMU_MAX_MESSAGES_PER_SESSION", "120") or "120")
    
    # Check if sub-agent sessions should be synced (default: only main sessions)
    sync_sub_sessions = os.getenv("MEMU_SYNC_SUB_SESSIONS", "false").lower() == "true"

    state = _load_state()
    sessions_state: dict[str, Any] = state.setdefault("sessions", {})

    # Get all session files and filter/sort them
    session_files = glob.glob(SESSION_GLOB)
    
    # Filter: only main sessions (UUID format) unless MEMU_SYNC_SUB_SESSIONS=true
    if not sync_sub_sessions:
        filtered_files = []
        for fp in session_files:
            sid = _extract_session_id(os.path.basename(fp))
            if sid and _is_main_session(sid):
                filtered_files.append(fp)
        session_files = filtered_files
    
    # Sort by session start time (oldest first) to ensure chronological processing
    # Uses the timestamp from session header, not file mtime (which changes on copy)
    session_files.sort(key=lambda p: _get_session_start_time(p))
    
    converted: list[str] = []

    for file_path in session_files:
        filename = os.path.basename(file_path)
        session_id = _extract_session_id(filename)
        if not session_id:
            continue
        lang_prefix = _get_language_prefix()

        try:
            st = os.stat(file_path)
        except FileNotFoundError:
            continue

        prev = (
            sessions_state.get(session_id) if isinstance(sessions_state, dict) else None
        )
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

        # since_ts is a fast-path hint, but it can miss appends on filesystems with
        # coarse mtime resolution. If we have state and the file grew past last_offset,
        # we still process even when mtime <= since_ts.
        if since_ts is not None and cur_mtime <= since_ts:
            if not prev or cur_size <= prev_offset:
                continue

        # Decide whether we can do append-only incremental processing.
        append_only = True
        if prev and (prev_dev is not None and prev_ino is not None):
            if int(prev_dev) != cur_dev or int(prev_ino) != cur_ino:
                append_only = False
        if cur_size < prev_offset:
            append_only = False
        if prev_lang != lang_prefix:
            # Changing language instruction changes the conversation payload => rebuild.
            append_only = False

        # Cheap edit detection: compare head/tail samples from last processed size.
        if append_only and prev_offset > 0 and (prev_head_sha or prev_tail_sha):
            head_len = min(SAMPLE_BYTES, cur_size)
            head_sha = _sha256_file_sample(
                file_path=file_path, start=0, length=head_len
            )
            tail_start = max(0, prev_offset - SAMPLE_BYTES)
            tail_len = max(0, min(SAMPLE_BYTES, prev_offset - tail_start))
            tail_sha = _sha256_file_sample(
                file_path=file_path, start=tail_start, length=tail_len
            )
            if (prev_head_sha and head_sha != prev_head_sha) or (
                prev_tail_sha and tail_sha != prev_tail_sha
            ):
                append_only = False

        # If no state or we can't trust append-only, do a full rebuild for this session.
        if not prev or not append_only:
            read_res = _read_messages_from_jsonl(file_path=file_path, start_offset=0)
            messages = read_res.messages

            # Always write parts for incremental stability.
            new_part_count = 0
            if max_messages > 0:
                for idx in range(0, len(messages), max_messages):
                    part_idx = idx // max_messages
                    part_path = _part_path(session_id, part_idx)
                    changed, _ = _write_part_json(
                        part_messages=messages[idx : idx + max_messages],
                        out_path=part_path,
                        lang_prefix=lang_prefix,
                    )
                    new_part_count += 1
                    if changed:
                        converted.append(part_path)
            else:
                # Fallback: single file overwrite mode.
                out_path = os.path.join(OUT_DIR, f"{session_id}.json")
                changed, _ = _write_part_json(
                    part_messages=messages,
                    out_path=out_path,
                    lang_prefix=lang_prefix,
                )
                new_part_count = 1 if messages else 0
                if changed:
                    converted.append(out_path)

            # Remove stale old part files if the session shrank.
            if (
                max_messages > 0
                and prev_part_count
                and new_part_count < prev_part_count
            ):
                for part_idx in range(new_part_count, prev_part_count):
                    try:
                        os.remove(_part_path(session_id, part_idx))
                    except FileNotFoundError:
                        pass
                    except Exception:
                        pass

            # Update state.
            head_len = min(SAMPLE_BYTES, cur_size)
            head_sha = _sha256_file_sample(
                file_path=file_path, start=0, length=head_len
            )
            tail_start = max(0, read_res.new_offset - SAMPLE_BYTES)
            tail_len = max(0, min(SAMPLE_BYTES, read_res.new_offset - tail_start))
            tail_sha = _sha256_file_sample(
                file_path=file_path, start=tail_start, length=tail_len
            )

            tail_count = 0
            if max_messages > 0 and new_part_count > 0:
                # Best-effort: count messages in the last part excluding system prefix.
                try:
                    last_part_path = _part_path(session_id, new_part_count - 1)
                    last_msgs = _read_part_messages(last_part_path)
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
                "lang_prefix": lang_prefix,
                "head_sha256": head_sha,
                "tail_sha256": tail_sha,
            }
            continue

        # Append-only: read from previous offset and only update tail/new parts.
        if cur_size == prev_offset:
            # No new bytes.
            continue

        read_res = _read_messages_from_jsonl(
            file_path=file_path, start_offset=prev_offset
        )
        new_messages = read_res.messages
        if not new_messages and read_res.new_offset == prev_offset:
            # Likely an incomplete trailing line; try again next sync.
            continue

        part_count = prev_part_count
        tail_count = prev_tail_count
        if max_messages <= 0:
            # Can't do incremental without parts; overwrite single file.
            out_path = os.path.join(OUT_DIR, f"{session_id}.json")
            # Rebuild full messages to avoid inconsistent state.
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
            prev_offset = 0
            read_res = full_res
        else:
            # Load last part (if any) to append into it until full.
            if part_count <= 0:
                part_count = 1
                tail_count = 0

            last_part_idx = max(0, part_count - 1)
            last_part_path = _part_path(session_id, last_part_idx)
            try:
                existing_part = _read_part_messages(last_part_path)
                existing_msgs = _strip_system_prefix(existing_part, lang_prefix)
            except FileNotFoundError:
                existing_msgs = []
            except Exception:
                # If the tail part is corrupted, fall back to full rebuild next time.
                existing_msgs = []

            # Append into last part (may rewrite it) and then spill into new parts.
            buf = list(existing_msgs)
            remain = list(new_messages)

            # Fill tail part up to max_messages.
            if len(buf) < max_messages and remain:
                take = min(max_messages - len(buf), len(remain))
                buf.extend(remain[:take])
                remain = remain[take:]
                changed, _ = _write_part_json(
                    part_messages=buf,
                    out_path=last_part_path,
                    lang_prefix=lang_prefix,
                )
                if changed:
                    converted.append(last_part_path)

            # If tail part reached max, subsequent messages go to new parts.
            if len(buf) >= max_messages:
                tail_count = max_messages
            else:
                tail_count = len(buf)

            while remain:
                part_idx = part_count
                chunk = remain[:max_messages]
                remain = remain[max_messages:]
                part_path = _part_path(session_id, part_idx)
                changed, _ = _write_part_json(
                    part_messages=chunk,
                    out_path=part_path,
                    lang_prefix=lang_prefix,
                )
                if changed:
                    converted.append(part_path)
                part_count += 1
                tail_count = len(chunk)

        # Update state (advance cursor to last complete line we consumed).
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
            "lang_prefix": lang_prefix,
            "head_sha256": head_sha,
            "tail_sha256": tail_sha,
        }


    # === PHASE 2: Process deleted files to catch rotation tails ===
    processed_deleted: set[str] = set(state.get("processed_deleted", []))
    
    # Prune old entries occasionally
    if len(processed_deleted) > 1000:
        existing_deleted = set()
        for fn in processed_deleted:
            if os.path.exists(os.path.join(sessions_dir, fn)):
                existing_deleted.add(fn)
        processed_deleted = existing_deleted

    deleted_files = glob.glob(DELETED_GLOB)
    
    # Filter: only main sessions (UUID format) unless MEMU_SYNC_SUB_SESSIONS=true
    if not sync_sub_sessions:
        filtered_deleted = []
        for fp in deleted_files:
            sid = _extract_session_id(os.path.basename(fp))
            if sid and _is_main_session(sid):
                filtered_deleted.append(fp)
        deleted_files = filtered_deleted
    
    # Sort by timestamp in filename (oldest first) for chronological processing
    # Sort by session start time (oldest first) - same as active files
    # This uses the timestamp from session header, not the deletion timestamp in filename
    deleted_files.sort(key=lambda p: _get_session_start_time(p))
    
    # Helper function for writing parts (defined once, not in loop)
    def _write_deleted_part(part_messages: list[dict[str, str]], out_path: str, lang_prefix: str | None) -> None:
        if lang_prefix:
            part_messages = [{"role": "system", "content": lang_prefix}, *part_messages]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(part_messages, f, indent=2, ensure_ascii=False)

    for file_path in deleted_files:
        filename = os.path.basename(file_path)
        
        # Skip if already processed (deleted files are immutable)
        if filename in processed_deleted:
            continue
        
        session_id = _extract_session_id(filename)
        if not session_id:
            continue
        
        # Get existing state or start fresh (for sessions deleted before first sync)
        prev = sessions_state.get(session_id)
        if not isinstance(prev, dict):
            prev = {}  # No prior state - treat as new, read from beginning
        
        prev_offset = int(prev.get("last_offset", 0) or 0)
        
        try:
            st = os.stat(file_path)
            cur_size = int(st.st_size)
        except FileNotFoundError:
            processed_deleted.add(filename)
            continue
        
        # If deleted file is smaller than or equal to last offset, no new data
        if cur_size <= prev_offset:
            processed_deleted.add(filename)
            continue
        
        # Read from last known offset to end, with error handling
        try:
            read_res = _read_messages_from_jsonl(file_path=file_path, start_offset=prev_offset)
            new_messages = read_res.messages
        except Exception as e:
            # Log error and skip corrupted file
            import sys
            print(f"[convert_sessions] Error reading deleted file {filename}: {e}", file=sys.stderr)
            processed_deleted.add(filename)
            continue
        
        if new_messages:
            # Generate new parts for the tail content
            lang_prefix = _get_language_prefix()
            
            # Use part_count (not last_part_idx) to determine next part index
            # This prevents overwriting existing parts
            next_part_idx = int(prev.get("part_count", 0))
            
            # Write new parts
            if max_messages > 0:
                for idx in range(0, len(new_messages), max_messages):
                    part_path = os.path.join(OUT_DIR, f"{session_id}.part{next_part_idx:03d}.json")
                    _write_deleted_part(new_messages[idx : idx + max_messages], part_path, lang_prefix)
                    converted.append(part_path)
                    next_part_idx += 1
            else:
                part_path = os.path.join(OUT_DIR, f"{session_id}.part{next_part_idx:03d}.json")
                _write_deleted_part(new_messages, part_path, lang_prefix)
                converted.append(part_path)
                next_part_idx += 1
            
            # Log progress for debugging
            import sys
            print(f"[convert_sessions] Processed deleted file: {filename} -> {next_part_idx - int(prev.get('part_count', 0))} new part(s)", file=sys.stderr)
        
        processed_deleted.add(filename)

    state["processed_deleted"] = list(processed_deleted)

    _save_state(state)
    return converted


if __name__ == "__main__":
    paths = convert()
    print(f"Converted {len(paths)} sessions into {OUT_DIR}.")
    for p in paths[:20]:
        print(f"- {p}")
    if len(paths) > 20:
        print(f"... +{len(paths) - 20} more")
