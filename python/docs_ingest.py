import asyncio
import os
import json
import sqlite3
import tempfile
from datetime import datetime
from memu.app.service import MemoryService
from memu.app.settings import (
    CustomPrompt,
    DatabaseConfig,
    LLMConfig,
    MemorizeConfig,
    MetadataStoreConfig,
    PromptBlock,
)


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is not None and str(v).strip():
        return v
    return default


def _log(msg: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        return
    try:
        with open(os.path.join(data_dir, "sync.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _try_acquire_lock(lock_path: str):
    """Best-effort non-blocking lock using O_EXCL (PID-aware)."""

    def _pid_alive(pid: int) -> bool:
        if pid <= 1:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(lock_path, flags)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        return fd
    except FileExistsError:
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                pid_str = f.read().strip()
            pid = int(pid_str)
            if not _pid_alive(pid):
                try:
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass
                try:
                    fd = os.open(lock_path, flags)
                    os.write(fd, str(os.getpid()).encode("utf-8"))
                    return fd
                except FileExistsError:
                    return None
        except Exception:
            return None
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


def _is_under_prefix(path: str, prefix: str) -> bool:
    try:
        path_abs = os.path.abspath(path)
        prefix_abs = os.path.abspath(prefix)
        # Ensure prefix ends with separator for strict containment check.
        if os.path.isdir(prefix_abs):
            prefix_abs = os.path.join(prefix_abs, "")
        return path_abs == prefix_abs.rstrip(os.sep) or path_abs.startswith(prefix_abs)
    except Exception:
        return False


def _collect_markdown_files(
    *, extra_paths: list[str], changed_path: str | None
) -> list[str]:
    """Collect markdown files to ingest.

    - If changed_path is provided, only ingest that file (or md files under that dir),
      and only if it is within extra_paths.
    - Otherwise, do a full scan of extra_paths.
    """
    files: set[str] = set()

    def _add_file(p: str) -> None:
        if p.endswith(".md") and os.path.isfile(p):
            files.add(os.path.abspath(p))

    def _scan_dir(d: str) -> None:
        for root, _, filenames in os.walk(d):
            for f in filenames:
                if f.endswith(".md"):
                    files.add(os.path.abspath(os.path.join(root, f)))

    if changed_path:
        cp = os.path.abspath(changed_path)
        # Only ingest changes that are within configured extra paths.
        allowed = any(_is_under_prefix(cp, p) for p in extra_paths)
        if not allowed:
            return []

        if os.path.isfile(cp):
            _add_file(cp)
        elif os.path.isdir(cp):
            _scan_dir(cp)
        return sorted(files)

    for path_item in extra_paths:
        if not os.path.exists(path_item):
            continue
        if os.path.isfile(path_item):
            _add_file(path_item)
        elif os.path.isdir(path_item):
            _scan_dir(path_item)

    return sorted(files)


LANGUAGE_PROMPTS = {
    "zh": """
## Language Override (CRITICAL - MUST FOLLOW)
- ALL output MUST be in Chinese (中文), regardless of example language.
- Use \"用户\" instead of \"the user\" or \"User\".
- The examples in this prompt are in English for reference only.
- You MUST write all memory content in Chinese.
""",
    "en": """
## Language Override
- ALL output MUST be in English.
- Use \"the user\" to refer to the user.
""",
    "ja": """
## Language Override (重要)
- ALL output MUST be in Japanese (日本語).
- Use \"ユーザー\" instead of \"the user\".
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

    lang_block = PromptBlock(ordinal=35, prompt=LANGUAGE_PROMPTS[lang])
    type_prompts: dict[str, str | CustomPrompt] = {}
    for mt in memory_types:
        type_prompts[mt] = CustomPrompt(root={"language": lang_block})

    return MemorizeConfig(
        **base_config,
        memory_type_prompts=type_prompts,
    )


def get_db_dsn() -> str:
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        base = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base, "data")

    os.makedirs(data_dir, exist_ok=True)
    return f"sqlite:///{os.path.join(data_dir, 'memu.db')}"


def _db_has_column(conn: sqlite3.Connection, *, table: str, column: str) -> bool:
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cur.fetchall() if len(row) > 1]
        return column in set(cols)
    except Exception:
        return False


def _resource_exists(resource_url: str, *, user_id: str) -> bool:
    try:
        data_dir = os.getenv("MEMU_DATA_DIR")
        if not data_dir:
            return False
        db_path = os.path.join(data_dir, "memu.db")
        if not os.path.exists(db_path):
            return False

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memu_resources'"
        )
        if cur.fetchone() is None:
            conn.close()
            return False

        if _db_has_column(conn, table="memu_resources", column="user_id"):
            cur.execute(
                "SELECT 1 FROM memu_resources WHERE url = ? AND user_id = ? LIMIT 1",
                (resource_url, user_id),
            )
        else:
            cur.execute(
                "SELECT 1 FROM memu_resources WHERE url = ? LIMIT 1",
                (resource_url,),
            )
        exists = cur.fetchone() is not None
        conn.close()
        return exists
    except Exception:
        return False


def get_extra_paths() -> list[str]:
    raw = os.getenv("MEMU_EXTRA_PATHS", "[]")
    try:
        paths = json.loads(raw)
        if isinstance(paths, list):
            return [p for p in paths if isinstance(p, str)]
    except json.JSONDecodeError:
        pass
    return []


def _full_scan_marker_path() -> str | None:
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        return None
    return os.path.join(data_dir, "docs_full_scan.marker")


async def main():
    lock_name = os.path.join(tempfile.gettempdir(), "memu_sync.lock_docs_ingest")
    lock_fd = _try_acquire_lock(lock_name)
    if lock_fd is None:
        _log("docs_ingest already running; skip")
        return

    try:
        user_id = _env("MEMU_USER_ID", "default") or "default"

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
            metadata_store=MetadataStoreConfig(
                provider="sqlite",
                dsn=get_db_dsn(),
            )
        )

        output_lang = _env("MEMU_OUTPUT_LANG", "")
        memorize_config = _build_language_aware_memorize_config(output_lang)

        service = MemoryService(
            llm_profiles={"default": chat_config, "embedding": embed_config},
            database_config=db_config,
            memorize_config=memorize_config,
        )

        extra_paths = get_extra_paths()
        changed_path = _env("MEMU_CHANGED_PATH", None)
        files_to_ingest = _collect_markdown_files(
            extra_paths=extra_paths, changed_path=changed_path
        )

        if not files_to_ingest:
            if changed_path:
                _log(
                    f"docs_ingest: no markdown files to ingest for change: {changed_path}"
                )
            else:
                _log("docs_ingest: no markdown files found in extraPaths")
            return

        mode = "incremental" if changed_path else "full-scan"
        _log(f"docs_ingest start. mode={mode} files={len(files_to_ingest)}")

        timeout_s = int(_env("MEMU_MEMORIZE_TIMEOUT_SECONDS", "600") or "600")

        ok = 0
        fail = 0
        skipped = 0

        for file_path in files_to_ingest:
            try:
                if _resource_exists(file_path, user_id=user_id):
                    skipped += 1
                    continue

                _log(f"docs_ingest ingest: {file_path}")
                await asyncio.wait_for(
                    service.memorize(
                        resource_url=file_path,
                        modality="document",
                        user={"user_id": user_id},
                    ),
                    timeout=timeout_s,
                )
                ok += 1
            except Exception as e:
                _log(f"docs_ingest failed: {file_path}: {type(e).__name__}: {e}")
                fail += 1

        _log(
            f"docs_ingest complete. ok={ok} skipped={skipped} fail={fail} files={len(files_to_ingest)}"
        )

        # Persist marker so watcher can skip initial full-scans on restart.
        if mode == "full-scan":
            marker = _full_scan_marker_path()
            if marker:
                try:
                    with open(marker, "w", encoding="utf-8") as f:
                        f.write(datetime.now().isoformat())
                except Exception:
                    pass
    finally:
        _release_lock(lock_name, lock_fd)


if __name__ == "__main__":
    asyncio.run(main())
