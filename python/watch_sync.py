import time
import subprocess
import os
import json
import tempfile
import sys
import signal
from typing import Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# Configuration paths
SESSIONS_DIR = os.getenv("OPENCLAW_SESSIONS_DIR")
MEMU_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(tempfile.gettempdir(), "memu_sync.lock")


def _run_lock_name(script_name: str) -> str:
    """Lock used by the worker script itself (auto_sync/docs_ingest)."""
    if script_name == "auto_sync.py":
        return os.path.join(tempfile.gettempdir(), "memu_sync.lock_auto_sync")
    if script_name == "docs_ingest.py":
        return os.path.join(tempfile.gettempdir(), "memu_sync.lock_docs_ingest")
    safe = script_name.replace(os.sep, "_")
    return os.path.join(tempfile.gettempdir(), f"memu_sync.lock_{safe}")


def _trigger_lock_name(script_name: str) -> str:
    """Lock used by the watcher to avoid redundant spawns."""
    safe = script_name.replace(os.sep, "_")
    return os.path.join(tempfile.gettempdir(), f"memu_sync.trigger.lock_{safe}")


def _is_lock_held(lock_path: str) -> bool:
    """PID-aware check whether a lock file is held by a live process."""
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            pid_str = f.read().strip()
        pid = int(pid_str)
        if pid > 1:
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                # Cannot signal; assume alive.
                return True
    except FileNotFoundError:
        return False
    except Exception:
        # If we cannot parse, be conservative and treat as held.
        return True

    return False


def _try_acquire_lock(lock_path: str, stale_seconds: int = 15 * 60):
    """Best-effort cross-platform lock using O_EXCL.

    - Non-blocking: if another process holds the lock, skip this sync trigger.
    - Stale lock recovery: if the lock file is older than stale_seconds, remove it.
    """

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(lock_path, flags)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        return fd
    except FileExistsError:
        try:
            # PID-aware check: if the process in the lock file is alive,
            # treat it as held forever (mtime-based stale checks are unreliable
            # for long-running daemons because the lock file is not touched).
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    pid_str = f.read().strip()
                pid = int(pid_str)
                if pid > 1:
                    try:
                        os.kill(pid, 0)
                        return None
                    except ProcessLookupError:
                        # PID not alive; recover immediately.
                        try:
                            os.remove(lock_path)
                        except FileNotFoundError:
                            pass
                        return _try_acquire_lock(lock_path, stale_seconds=stale_seconds)
                    except PermissionError:
                        # Cannot signal it; assume it's alive.
                        return None
            except Exception:
                # Fall back to mtime-based stale check.
                pass

            age = time.time() - os.path.getmtime(lock_path)
            if age > stale_seconds:
                os.remove(lock_path)
                return _try_acquire_lock(lock_path, stale_seconds=stale_seconds)
        except FileNotFoundError:
            return _try_acquire_lock(lock_path, stale_seconds=stale_seconds)
        except Exception:
            pass
        return None


def _release_lock(lock_path: str, fd):
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


def get_extra_paths():
    try:
        return json.loads(os.getenv("MEMU_EXTRA_PATHS", "[]"))
    except:
        return []


def _docs_full_scan_marker_path() -> Optional[str]:
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        return None
    return os.path.join(data_dir, "docs_full_scan.marker")


def _get_main_session_file() -> Optional[str]:
    """Best-effort: resolve main session file path from sessions.json."""
    if not SESSIONS_DIR:
        return None
    sessions_json = os.path.join(SESSIONS_DIR, "sessions.json")
    try:
        with open(sessions_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        main_id = (data.get("agent:main:main") or {}).get("sessionId")
        if not main_id:
            return None
        p = os.path.join(SESSIONS_DIR, f"{main_id}.jsonl")
        return p if os.path.exists(p) else None
    except Exception:
        return None


def _should_run_idle_flush(
    *,
    main_session_file: Optional[str],
    flush_idle_seconds: int,
) -> bool:
    """Low-overhead check to avoid needless auto_sync calls.

    We trigger auto_sync only if:
    - the main session file has been idle for long enough, AND
    - there is a staged tail file present (otherwise we'd spin with converted_paths=0).
    """
    if flush_idle_seconds <= 0:
        return False
    if not main_session_file:
        return False

    memu_data_dir = os.getenv("MEMU_DATA_DIR")
    if not memu_data_dir:
        return False

    try:
        mtime = os.path.getmtime(main_session_file)
    except Exception:
        return False
    now = time.time()

    # Only consider idle flush when session file itself is idle.
    if (now - mtime) < flush_idle_seconds:
        return False

    # Only consider idle flush if there's a staged tail present.
    session_id = os.path.basename(main_session_file)
    if session_id.endswith(".jsonl"):
        session_id = session_id[: -len(".jsonl")]

    tail_path = os.path.join(
        memu_data_dir, "conversations", f"{session_id}.tail.tmp.json"
    )
    try:
        st = os.stat(tail_path)
    except FileNotFoundError:
        return False
    except Exception:
        return False

    # If the staged file is empty-ish, skip triggering.
    if st.st_size < 10:
        return False

    return True


class SyncHandler(FileSystemEventHandler):
    def __init__(self, script_name, extensions, *, should_trigger=None):
        self.script_name = script_name
        self.extensions = extensions
        self.last_run = 0
        self.debounce_seconds = 5
        self.should_trigger = should_trigger

    def on_modified(self, event):
        if event.is_directory:
            return
        if not any(event.src_path.endswith(ext) for ext in self.extensions):
            return
        src_path = str(event.src_path)
        if self.should_trigger and not self.should_trigger(src_path):
            return
        self.trigger_sync(changed_path=src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        if not any(event.src_path.endswith(ext) for ext in self.extensions):
            return
        src_path = str(event.src_path)
        if self.should_trigger and not self.should_trigger(src_path):
            return
        self.trigger_sync(changed_path=src_path)

    def trigger_sync(self, *, changed_path: str | None = None):
        now = time.time()
        if now - self.last_run < self.debounce_seconds:
            return

        print(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Change detected, triggering {self.script_name}..."
        )

        # If the worker is already running, don't even spawn it.
        run_lock = _run_lock_name(self.script_name)
        if _is_lock_held(run_lock):
            return

        lock_name = _trigger_lock_name(self.script_name)
        lock_fd = _try_acquire_lock(lock_name)
        if lock_fd is None:
            return
        try:
            self.last_run = time.time()
            env = os.environ.copy()
            if changed_path:
                env["MEMU_CHANGED_PATH"] = changed_path
            script_path = os.path.join(MEMU_DIR, self.script_name)
            subprocess.run([sys.executable, script_path], cwd=MEMU_DIR, env=env)
        except Exception as e:
            print(f"Failed to trigger {self.script_name}: {e}")
        finally:
            _release_lock(lock_name, lock_fd)


if __name__ == "__main__":
    # Ensure only one watcher instance runs at a time.
    watcher_lock_name = f"{LOCK_FILE}_watch_sync"
    watcher_lock_fd = _try_acquire_lock(watcher_lock_name)
    if watcher_lock_fd is None:
        print("Another memU watcher is already running. Exiting.")
        raise SystemExit(0)

    observer = Observer()

    flush_idle_seconds = int(os.getenv("MEMU_FLUSH_IDLE_SECONDS", "1800") or "1800")
    flush_poll_seconds = int(os.getenv("MEMU_FLUSH_POLL_SECONDS", "60") or "60")
    last_poll_tick = 0
    last_idle_trigger_mtime: float | None = None
    # Use a mutable box so nested functions can refresh the path.
    main_session_file_box: dict[str, Optional[str]] = {"path": _get_main_session_file()}
    sessions_json_path = (
        os.path.join(SESSIONS_DIR, "sessions.json") if SESSIONS_DIR else None
    )

    session_handler: SyncHandler | None = None

    def _shutdown_handler(signum, frame):
        try:
            observer.stop()
        finally:
            _release_lock(watcher_lock_name, watcher_lock_fd)
        raise SystemExit(0)

    # Ensure SIGTERM/SIGINT releases the singleton lock.
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # 1. Watch Sessions
    if SESSIONS_DIR and os.path.exists(SESSIONS_DIR):
        print(f"Watching sessions: {SESSIONS_DIR}")

        def _sessions_should_trigger(src_path: str) -> bool:
            # Refresh main session file when sessions.json changes.
            if sessions_json_path and os.path.abspath(src_path) == os.path.abspath(
                sessions_json_path
            ):
                main_session_file_box["path"] = _get_main_session_file()
                return True

            # Only trigger on changes to the current main session file.
            main_session_file = main_session_file_box.get("path")
            if main_session_file and os.path.abspath(src_path) == os.path.abspath(
                main_session_file
            ):
                return True

            return False

        session_handler = SyncHandler(
            "auto_sync.py", [".jsonl", ".json"], should_trigger=_sessions_should_trigger
        )
        observer.schedule(session_handler, SESSIONS_DIR, recursive=False)
        # Trigger initial sync
        session_handler.trigger_sync(changed_path=main_session_file_box.get("path"))
    else:
        print(f"Warning: Session dir {SESSIONS_DIR} not found or not set.")

    # 2. Watch Docs (Extra Paths)
    extra_paths = get_extra_paths()
    if extra_paths:
        docs_handler = SyncHandler("docs_ingest.py", [".md"])
        # Watch directories recursively; if a file path is provided, watch its parent directory.
        watched_dirs: set[tuple[str, bool]] = set()
        for path_item in extra_paths:
            if not os.path.exists(path_item):
                print(f"Warning: Extra path {path_item} not found.")
                continue

            if os.path.isdir(path_item):
                watch_dir = path_item
                recursive = True
            else:
                watch_dir = os.path.dirname(path_item) or "."
                recursive = False

            key = (watch_dir, recursive)
            if key in watched_dirs:
                continue
            watched_dirs.add(key)

            print(f"Watching docs: {watch_dir}")
            observer.schedule(docs_handler, watch_dir, recursive=recursive)
        # Trigger initial docs sync ONCE per data dir.
        # Full-scan is expensive/noisy; we rely on incremental runs for ongoing updates.
        marker = _docs_full_scan_marker_path()
        if marker and os.path.exists(marker):
            print("Docs full-scan marker exists; skip initial docs sync")
        else:
            docs_handler.trigger_sync()

    observer.start()
    try:
        while True:
            time.sleep(1)
            # Periodic idle-flush trigger with minimal overhead:
            # - does NOT call auto_sync unless the main session file has been idle >= flush_idle_seconds
            # - re-resolves main session file occasionally in case sessions.json changes
            if session_handler is not None and flush_poll_seconds > 0:
                now_i = int(time.time())
                if now_i % flush_poll_seconds == 0 and now_i != last_poll_tick:
                    last_poll_tick = now_i
                    if now_i % (flush_poll_seconds * 10) == 0:
                        main_session_file_box["path"] = _get_main_session_file()
                    # Avoid needless auto_sync calls:
                    # - only trigger if the session is idle AND a staged tail exists
                    # - only trigger once per unique session mtime (otherwise we'd spin)
                    main_session_file = main_session_file_box.get("path")
                    if main_session_file and _should_run_idle_flush(
                        main_session_file=main_session_file,
                        flush_idle_seconds=flush_idle_seconds,
                    ):
                        try:
                            mtime = os.path.getmtime(main_session_file)
                        except Exception:
                            mtime = None
                        if mtime is not None and mtime != last_idle_trigger_mtime:
                            last_idle_trigger_mtime = mtime
                            session_handler.trigger_sync()
    except KeyboardInterrupt:
        observer.stop()
    finally:
        _release_lock(watcher_lock_name, watcher_lock_fd)
    observer.join()
