import asyncio
import os
import sys
import time
from datetime import datetime
import json

from memu.app.service import MemoryService

import sqlite3
import tempfile


def _db_has_column(conn: sqlite3.Connection, *, table: str, column: str) -> bool:
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cur.fetchall() if len(row) > 1]
        return column in set(cols)
    except Exception:
        return False


def resource_exists(resource_url: str, user_id: str) -> bool:
    try:
        dsn = get_db_dsn()
        # dsn is sqlite:///path/to/db
        db_path = dsn.replace("sqlite:///", "")
        if not os.path.exists(db_path):
            return False

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if table exists first
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memu_resources'"
        )
        if not cursor.fetchone():
            conn.close()
            return False

        # NOTE: Keep the dedupe key consistent with what Memorize stores:
        # memorize(resource_url=...) ultimately creates Resource(url=resource_url).
        if _db_has_column(conn, table="memu_resources", column="user_id"):
            cursor.execute(
                "SELECT 1 FROM memu_resources WHERE url = ? AND user_id = ? LIMIT 1",
                (resource_url, user_id),
            )
        else:
            cursor.execute(
                "SELECT 1 FROM memu_resources WHERE url = ? LIMIT 1",
                (resource_url,),
            )
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    except Exception as e:
        _log(f"DB check failed: {e}")
        return False


def _log(msg: str) -> None:
    """Log to both stdout and log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)

    log_dir = os.getenv("MEMU_DATA_DIR", os.path.dirname(__file__))
    log_file = os.path.join(log_dir, "sync.log")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


from memu.app.settings import (
    CustomPrompt,
    DatabaseConfig,
    LLMConfig,
    MemorizeConfig,
    MetadataStoreConfig,
    PromptBlock,
)

from convert_sessions import convert


def _try_acquire_lock(lock_path: str):
    """Best-effort non-blocking lock using O_EXCL."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(lock_path, flags)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        return fd
    except FileExistsError:
        return None


def _release_lock(lock_path: str, fd) -> None:
    try:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    finally:
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


def _get_data_dir() -> str:
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        # Fallback for standalone dev: use local 'data' dir relative to repo root.
        # This file lives at: <repo>/python/auto_sync.py
        base = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base, "data")
    return data_dir


def _get_sync_marker_path() -> str:
    return os.path.join(_get_data_dir(), "last_sync_ts")


def _get_pending_ingest_path() -> str:
    return os.path.join(_get_data_dir(), "pending_ingest.json")


def _load_pending_ingest() -> list[str]:
    try:
        with open(_get_pending_ingest_path(), "r", encoding="utf-8") as f:
            payload = json.load(f)
        paths = payload.get("paths") if isinstance(payload, dict) else None
        if isinstance(paths, list):
            return [p for p in paths if isinstance(p, str) and p.strip()]
    except Exception:
        pass
    return []


def _save_pending_ingest(paths: list[str]) -> None:
    marker = _get_pending_ingest_path()
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    tmp = marker + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "paths": paths}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, marker)


def get_db_dsn() -> str:
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        # Fallback for standalone dev: use local 'data' dir relative to script root
        # Assuming script is in python/ or python/scripts/
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base, "data")

    os.makedirs(data_dir, exist_ok=True)
    return f"sqlite:///{os.path.join(data_dir, 'memu.db')}"


def _env(name: str, default: str | None = None) -> str | None:
    # Try actual environment first
    v = os.getenv(name)
    if v is not None and str(v).strip():
        return v

    # Fallback: manual parse .env if it exists in the same dir
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, val = line.split("=", 1)
                    if k.strip() == name:
                        return val.strip().strip("'").strip('"')
        except Exception:
            pass

    return default


LANGUAGE_PROMPTS = {
    "zh": """
## Language Override (CRITICAL - MUST FOLLOW)
- ALL output MUST be in Chinese (中文), regardless of example language.
- Use "用户" instead of "the user" or "User".
- The examples in this prompt are in English for reference only.
- You MUST write all memory content in Chinese.
""",
    "en": """
## Language Override
- ALL output MUST be in English.
- Use "the user" to refer to the user.
""",
    "ja": """
## Language Override (重要)
- ALL output MUST be in Japanese (日本語).
- Use "ユーザー" instead of "the user".
""",
}


def _build_language_aware_memorize_config(lang: str | None) -> MemorizeConfig:
    memory_types = ["profile", "event", "knowledge", "behavior", "skill", "tool"]

    base_config = {
        "memory_types": memory_types,
        "enable_item_references": True,
        "enable_item_reinforcement": True,
    }

    if not lang or lang not in LANGUAGE_PROMPTS:
        return MemorizeConfig(**base_config)

    lang_prompt = LANGUAGE_PROMPTS[lang]
    lang_block = PromptBlock(ordinal=35, prompt=lang_prompt)

    type_prompts = {}
    for mt in memory_types:
        type_prompts[mt] = CustomPrompt(root={"language": lang_block})

    return MemorizeConfig(
        **base_config,
        memory_type_prompts=type_prompts,
    )


def build_service() -> MemoryService:
    chat_kwargs = {}
    if p := _env("MEMU_CHAT_PROVIDER"):
        chat_kwargs["provider"] = p
    if u := _env("MEMU_CHAT_BASE_URL"):
        chat_kwargs["base_url"] = u
    if k := _env("MEMU_CHAT_API_KEY"):
        chat_kwargs["api_key"] = k
    if m := _env("MEMU_CHAT_MODEL"):
        chat_kwargs["chat_model"] = m
    chat_config = LLMConfig(**chat_kwargs)

    embed_kwargs = {}
    if p := _env("MEMU_EMBED_PROVIDER"):
        embed_kwargs["provider"] = p
    if u := _env("MEMU_EMBED_BASE_URL"):
        embed_kwargs["base_url"] = u
    if k := _env("MEMU_EMBED_API_KEY"):
        embed_kwargs["api_key"] = k
    if m := _env("MEMU_EMBED_MODEL"):
        embed_kwargs["embed_model"] = m
    embed_config = LLMConfig(**embed_kwargs)

    db_config = DatabaseConfig(
        metadata_store=MetadataStoreConfig(provider="sqlite", dsn=get_db_dsn())
    )

    output_lang = _env("MEMU_OUTPUT_LANG", "")
    memorize_config = _build_language_aware_memorize_config(output_lang)

    return MemoryService(
        llm_profiles={"default": chat_config, "embedding": embed_config},
        database_config=db_config,
        memorize_config=memorize_config,
    )


def _read_last_sync() -> float:
    try:
        with open(_get_sync_marker_path(), "r", encoding="utf-8") as f:
            return float(f.read().strip() or "0")
    except Exception:
        return 0.0


def _write_last_sync(ts: float) -> None:
    marker = _get_sync_marker_path()
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as f:
        f.write(str(ts))


async def sync_once(user_id: str = "default") -> None:
    # Prevent concurrent runs (watcher + manual tool) to avoid duplicated ingestion.
    lock_name = os.path.join(tempfile.gettempdir(), "memu_sync.lock_auto_sync")
    lock_fd = _try_acquire_lock(lock_name)
    if lock_fd is None:
        _log("auto_sync already running; skip")
        return

    last_sync = _read_last_sync()
    sync_start_ts = time.time()

    _log(f"sync start. since_ts={last_sync}")

    # Load any previously converted-but-not-ingested parts.
    # This prevents data loss when convert() advances its internal state.json
    # but downstream memorize() fails mid-batch.
    pending_paths = _load_pending_ingest()

    # 1) Convert updated OpenClaw session jsonl -> memU JSON resources
    converted_paths = convert(since_ts=last_sync)

    # Merge (preserve order) and persist pending queue BEFORE ingest.
    merged: list[str] = []
    seen: set[str] = set()
    for p in [*pending_paths, *converted_paths]:
        if not isinstance(p, str) or not p.strip():
            continue
        if p in seen:
            continue
        seen.add(p)
        merged.append(p)
    _save_pending_ingest(merged)

    _log(f"converted_paths: {len(converted_paths)}")
    _log(f"pending_paths: {len(merged)}")

    if not merged:
        _log("no updated sessions to ingest.")
        _write_last_sync(sync_start_ts)
        _release_lock(lock_name, lock_fd)
        return

    # 2) Ingest converted conversations into memU
    service = build_service()

    ok = 0
    fail = 0

    timeout_s = int(_env("MEMU_MEMORIZE_TIMEOUT_SECONDS", "600") or "600")

    remaining: list[str] = []
    for p in merged:
        # Check if resource already exists to skip re-ingestion
        if resource_exists(p, user_id):
            _log(f"skip existing: {os.path.basename(p)}")
            continue

        try:
            base = os.path.basename(p)
            t0 = time.time()
            _log(f"ingest: {base}")
            await asyncio.wait_for(
                service.memorize(
                    resource_url=p, modality="conversation", user={"user_id": user_id}
                ),
                timeout=timeout_s,
            )
            ok += 1
            _log(f"done: {base} ({time.time() - t0:.1f}s)")
        except asyncio.TimeoutError:
            _log(f"TIMEOUT: {os.path.basename(p)} (>{timeout_s}s)")
            fail += 1
            remaining.append(p)
        except Exception as e:
            _log(f"ERROR: {os.path.basename(p)} - {type(e).__name__}: {e}")
            fail += 1
            remaining.append(p)

    _log(f"sync complete. success={ok}, failed={fail}")

    # Persist remaining queue and advance cursor only when everything is ingested.
    _save_pending_ingest(remaining)

    if fail == 0:
        _write_last_sync(sync_start_ts)
    else:
        _log("sync cursor not advanced due to failures")

    _release_lock(lock_name, lock_fd)


if __name__ == "__main__":
    user_id = _env("MEMU_USER_ID", "default") or "default"
    asyncio.run(sync_once(user_id=user_id))
