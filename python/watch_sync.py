import time
import subprocess
import os
import json
import tempfile
import sys
import signal
import traceback
from typing import Optional, Dict, Tuple, Any, TYPE_CHECKING

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

if TYPE_CHECKING:
    pass


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
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as f:
            f.write(str(os.getpid()))
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


def _release_lock(lock_path: str, lock_handle):
    try:
        if isinstance(lock_handle, int):
            try:
                os.close(lock_handle)
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
    return os.path.join(data_dir, "state", "docs_full_scan.marker")


def _enabled_agents_from_settings() -> list[str]:
    raw = os.getenv("MEMU_AGENT_SETTINGS", "")
    if not raw.strip():
        return ["main"]
    try:
        loaded = json.loads(raw)
    except Exception:
        return ["main"]
    if not isinstance(loaded, dict):
        return ["main"]

    out: list[str] = []
    for name, cfg in loaded.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(cfg, dict):
            continue
        if bool(cfg.get("memoryEnabled", True)):
            out.append(name)

    if not out:
        return ["main"]
    return list(dict.fromkeys(out))


def _get_agent_session_files(
    sessions_dir: str,
    enabled_agents: list[str],
) -> dict[str, list[str]]:
    if not sessions_dir:
        return {}

    enabled = set(a for a in enabled_agents if isinstance(a, str) and a.strip())
    if not enabled:
        enabled = {"main"}

    try:
        os.environ.setdefault("MEMU_DATA_DIR", tempfile.gettempdir())
        os.environ.setdefault("OPENCLAW_SESSIONS_DIR", sessions_dir)
        from convert_sessions import discover_all_session_files

        discovered = discover_all_session_files(sessions_dir, list(enabled))
        if not isinstance(discovered, dict):
            return {}
        return {
            str(agent): [str(path) for path in paths if isinstance(path, str) and path]
            for agent, path in discovered.items()
            if isinstance(agent, str)
            and agent in enabled
            and isinstance(path, list)
            and path
        }
    except Exception:
        return {}


def _should_run_idle_flush(
    *,
    session_files: Optional[list[str]],
    agent_name: str,
    flush_idle_seconds: int,
) -> bool:
    """Low-overhead check to avoid needless auto_sync calls.

    We trigger auto_sync only if:
    - at least one tracked session file has been idle for long enough, AND
    - there is a staged tail file present (otherwise we'd spin with converted_paths=0).
    """
    if flush_idle_seconds <= 0:
        return False
    if not session_files:
        return False

    memu_data_dir = os.getenv("MEMU_DATA_DIR")
    if not memu_data_dir:
        return False

    now = time.time()
    for session_file in session_files:
        try:
            mtime = os.path.getmtime(session_file)
        except Exception:
            continue

        # Only consider idle flush when session file itself is idle.
        if (now - mtime) < flush_idle_seconds:
            continue

        session_id = os.path.basename(session_file)
        if session_id.endswith(".jsonl"):
            session_id = session_id[: -len(".jsonl")]

        tail_candidates = [
            os.path.join(
                memu_data_dir,
                "conversations",
                agent_name,
                f"{session_id}.tail.tmp.json",
            ),
            os.path.join(memu_data_dir, "conversations", f"{session_id}.tail.tmp.json"),
        ]
        seen: set[str] = set()
        for tail_path in tail_candidates:
            if tail_path in seen:
                continue
            seen.add(tail_path)
            try:
                st = os.stat(tail_path)
            except FileNotFoundError:
                continue
            except Exception:
                continue
            if st.st_size >= 10:
                return True

    return False


class SyncHandler(FileSystemEventHandler):
    def __init__(self, script_name, extensions, *, should_trigger=None):
        self.script_name = script_name
        self.extensions = extensions
        self.last_run = 0
        self.debounce_seconds = int(
            os.getenv("MEMU_SYNC_DEBOUNCE_SECONDS", "20") or "20"
        )
        self.should_trigger = should_trigger

    @staticmethod
    def _matches_extensions(path: str | None, extensions: list[str]) -> bool:
        if not path:
            return False
        return any(path.endswith(ext) for ext in extensions)

    def _handle_event(self, *, src_path: str, dest_path: str | None = None):
        if not (
            self._matches_extensions(src_path, self.extensions)
            or self._matches_extensions(dest_path, self.extensions)
        ):
            return

        extra_env: dict[str, str] | None = None
        if self.should_trigger:
            decision = self.should_trigger(src_path=src_path, dest_path=dest_path)
            should_run = False
            if isinstance(decision, tuple):
                should_run = bool(decision[0])
                maybe_env = decision[1] if len(decision) > 1 else None
                if isinstance(maybe_env, dict):
                    extra_env = {
                        str(k): str(v)
                        for k, v in maybe_env.items()
                        if v is not None and str(v)
                    }
            else:
                should_run = bool(decision)

            if not should_run:
                return

        changed_path = dest_path or src_path
        self.trigger_sync(changed_path=changed_path, extra_env=extra_env)

    def on_modified(self, event):
        if event.is_directory:
            return
        self._handle_event(src_path=str(event.src_path))

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle_event(src_path=str(event.src_path))

    def on_moved(self, event):
        if event.is_directory:
            return
        self._handle_event(
            src_path=str(event.src_path),
            dest_path=str(getattr(event, "dest_path", "") or ""),
        )

    def on_deleted(self, event):
        if event.is_directory:
            return
        self._handle_event(src_path=str(event.src_path))

    def trigger_sync(
        self,
        *,
        changed_path: str | None = None,
        extra_env: dict[str, str] | None = None,
    ):
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
        lock_handle = _try_acquire_lock(lock_name)
        if lock_handle is None:
            return
        try:
            self.last_run = time.time()
            env = os.environ.copy()
            if changed_path:
                env["MEMU_CHANGED_PATH"] = changed_path
            if extra_env:
                env.update(extra_env)
            script_path = os.path.join(MEMU_DIR, self.script_name)
            subprocess.run([sys.executable, script_path], cwd=MEMU_DIR, env=env)
        except Exception as e:
            print(f"Failed to trigger {self.script_name}: {e}")
        finally:
            _release_lock(lock_name, lock_handle)


def start_watching_agent(
    agent_name: str,
    sessions_dir: str,
    observer: Any,
    flush_idle_seconds: int,
    flush_poll_seconds: int,
) -> Tuple[Optional["SyncHandler"], Dict[str, Any]]:
    """Setup watching for a single agent's sessions directory.
    
    Returns:
        (session_handler, state_dict) where state_dict contains:
        - session_files_box: mutable box for tracking all session files
        - last_poll_tick: last poll timestamp
        - last_idle_trigger_sig: last idle trigger signature
    """
    if not sessions_dir or not os.path.exists(sessions_dir):
        print(f"[{agent_name}] Warning: Session dir {sessions_dir} not found or not set.")
        return None, {}

    print(f"[{agent_name}] Watching sessions: {sessions_dir}")

    sessions_dir_abs = os.path.abspath(sessions_dir)
    enabled_agents = _enabled_agents_from_settings()
    session_files_box: Dict[str, dict[str, list[str]]] = {
        "paths": _get_agent_session_files(sessions_dir, enabled_agents)
    }

    def _tracked_session_files() -> list[str]:
        tracked = session_files_box["paths"].get(agent_name)
        if not isinstance(tracked, list):
            return []
        return tracked

    def _is_sessions_json_path(path: str) -> bool:
        if not path:
            return False
        try:
            p = os.path.abspath(path)
            return os.path.dirname(p) == sessions_dir_abs and os.path.basename(
                p
            ) == "sessions.json"
        except Exception:
            return False

    def _sessions_should_trigger(
        *, src_path: str, dest_path: str | None = None
    ) -> bool | tuple[bool, dict[str, str]]:
        paths = [p for p in (src_path, dest_path) if p]
        abs_paths = []
        for p in paths:
            try:
                abs_paths.append(os.path.abspath(p))
            except Exception:
                continue

        if any(_is_sessions_json_path(p) for p in paths):
            session_files_box["paths"] = _get_agent_session_files(
                sessions_dir, _enabled_agents_from_settings()
            )
            return True, {
                "MEMU_AGENT_NAME": agent_name,
                "OPENCLAW_SESSIONS_DIR": sessions_dir,
            }

        tracked_abs = {
            os.path.abspath(path)
            for path in _tracked_session_files()
            if isinstance(path, str) and path
        }

        if tracked_abs and any(p in tracked_abs for p in abs_paths):
            return True, {
                "MEMU_AGENT_NAME": agent_name,
                "OPENCLAW_SESSIONS_DIR": sessions_dir,
            }

        interested = any(
            p.startswith(sessions_dir_abs + os.sep)
            and (p.endswith(".jsonl") or p.endswith(".json"))
            for p in abs_paths
        )
        if interested:
            prev_tracked = _tracked_session_files()
            session_files_box["paths"] = _get_agent_session_files(
                sessions_dir, _enabled_agents_from_settings()
            )
            refreshed_tracked = _tracked_session_files()

            prev_ids = {
                os.path.basename(path)[: -len(".jsonl")]
                if isinstance(path, str) and path.endswith(".jsonl")
                else os.path.basename(path)
                for path in prev_tracked
                if isinstance(path, str) and path
            }
            refreshed_ids = {
                os.path.basename(path)[: -len(".jsonl")]
                if isinstance(path, str) and path.endswith(".jsonl")
                else os.path.basename(path)
                for path in refreshed_tracked
                if isinstance(path, str) and path
            }
            removed_ids = sorted(prev_ids - refreshed_ids)

            if prev_ids != refreshed_ids:
                extra_env: dict[str, str] = {}
                if removed_ids:
                    # Ask auto_sync to salvage/finalize removed tracked sessions
                    # so resets/rotations do not strand un-ingested tail messages.
                    extra_env["MEMU_PREV_SESSION_IDS"] = ",".join(removed_ids)
                    extra_env["MEMU_PREV_MAIN_SESSION_ID"] = removed_ids[0]
                extra_env["MEMU_AGENT_NAME"] = agent_name
                extra_env["OPENCLAW_SESSIONS_DIR"] = sessions_dir
                return (True, extra_env)

            refreshed_abs = {
                os.path.abspath(path)
                for path in refreshed_tracked
                if isinstance(path, str) and path
            }
            if refreshed_abs and any(p in refreshed_abs for p in abs_paths):
                return True, {
                    "MEMU_AGENT_NAME": agent_name,
                    "OPENCLAW_SESSIONS_DIR": sessions_dir,
                }

        return False

    session_handler = SyncHandler(
        "auto_sync.py", [".jsonl", ".json"], should_trigger=_sessions_should_trigger
    )
    watch = observer.schedule(session_handler, sessions_dir, recursive=False)
    # Trigger initial sync
    session_handler.trigger_sync(
        changed_path=_tracked_session_files()[0] if _tracked_session_files() else None,
        extra_env={
            "MEMU_AGENT_NAME": agent_name,
            "OPENCLAW_SESSIONS_DIR": sessions_dir,
        },
    )

    state = {
        "session_files_box": session_files_box,
        "last_poll_tick": 0,
        "last_idle_trigger_sig": None,
        "watch": watch,
    }
    return session_handler, state


def _reconcile_agent_watchers(
    *,
    agent_dirs: dict[str, str],
    agent_states: dict[str, dict[str, Any]],
    observer: Any,
    flush_idle_seconds: int,
    flush_poll_seconds: int,
) -> dict[str, dict[str, Any]]:
    enabled_agents = set(_enabled_agents_from_settings())

    for agent_name in list(agent_states.keys()):
        if agent_name in enabled_agents:
            continue
        state = agent_states.get(agent_name, {}).get("state", {})
        watch = state.get("watch") if isinstance(state, dict) else None
        if watch is not None:
            try:
                observer.unschedule(watch)
            except Exception:
                pass
        agent_states.pop(agent_name, None)

    for agent_name, sessions_dir in agent_dirs.items():
        if agent_name not in enabled_agents:
            continue
        if agent_name in agent_states:
            continue
        handler, state = start_watching_agent(
            agent_name, sessions_dir, observer, flush_idle_seconds, flush_poll_seconds
        )
        if handler:
            agent_states[agent_name] = {"handler": handler, "state": state}

    return agent_states


def process_agent_idle_flushes(
    *,
    agent_states: dict[str, dict[str, Any]],
    agent_dirs: dict[str, str],
    flush_idle_seconds: int,
    flush_poll_seconds: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {"success": [], "failed": [], "errors": []}

    for agent_name, agent_data in agent_states.items():
        try:
            handler = agent_data["handler"]
            state = agent_data["state"]
            session_files_box = state["session_files_box"]

            if handler is None or flush_poll_seconds <= 0:
                results["success"].append(agent_name)
                continue

            now_i = int(time.time())
            if now_i % flush_poll_seconds != 0 or now_i == state["last_poll_tick"]:
                results["success"].append(agent_name)
                continue

            state["last_poll_tick"] = now_i
            sessions_dir = agent_dirs.get(agent_name)
            if sessions_dir:
                enabled_agents_now = _enabled_agents_from_settings()
                session_files_box["paths"] = _get_agent_session_files(
                    sessions_dir, enabled_agents_now
                )

            tracked_session_files = session_files_box["paths"].get(agent_name)
            if tracked_session_files and _should_run_idle_flush(
                session_files=tracked_session_files,
                agent_name=agent_name,
                flush_idle_seconds=flush_idle_seconds,
            ):
                signature_parts: list[str] = []
                for session_file in tracked_session_files:
                    try:
                        mtime = os.path.getmtime(session_file)
                    except Exception:
                        continue
                    signature_parts.append(f"{session_file}:{mtime}")
                idle_sig = "|".join(signature_parts)
                if idle_sig and idle_sig != state["last_idle_trigger_sig"]:
                    state["last_idle_trigger_sig"] = idle_sig
                    handler.trigger_sync(
                        extra_env={
                            "MEMU_AGENT_NAME": agent_name,
                            "OPENCLAW_SESSIONS_DIR": sessions_dir,
                        }
                    )

            results["success"].append(agent_name)
        except Exception as exc:
            tb = traceback.format_exc()
            err = {
                "agent": agent_name,
                "error": str(exc),
                "traceback": tb,
            }
            print(
                f"[watch_sync] idle flush failed for agent '{agent_name}': {exc}\n{tb}",
                file=sys.stderr,
            )
            results["failed"].append({"agent": agent_name, "error": str(exc)})
            results["errors"].append(err)

    return results


if __name__ == "__main__":
    watcher_lock_name = f"{LOCK_FILE}_watch_sync"
    watcher_lock_handle = _try_acquire_lock(watcher_lock_name)
    if watcher_lock_handle is None:
        print("Another memU watcher is already running. Exiting.")
        raise SystemExit(0)

    try:
        from memu.storage_layout import migrate_legacy_single_db_to_agent_db

        migration = migrate_legacy_single_db_to_agent_db(default_agent="main")
        if migration.migrated:
            print(
                f"Legacy DB auto-migrated: {migration.source_db} -> {migration.target_db} "
                f"(backup={migration.backup_path})"
            )
    except Exception as e:
        print(f"Migration check failed: {e}")
        import traceback
        traceback.print_exc()

    observer = Observer()

    flush_idle_seconds = int(os.getenv("MEMU_FLUSH_IDLE_SECONDS", "1800") or "1800")
    flush_poll_seconds = int(os.getenv("MEMU_FLUSH_POLL_SECONDS", "60") or "60")

    # Parse agent directories from environment
    agent_dirs_json = os.getenv("MEMU_AGENT_DIRS")
    if agent_dirs_json:
        try:
            agent_dirs = json.loads(agent_dirs_json)
            print(f"Monitoring {len(agent_dirs)} agent(s): {', '.join(agent_dirs.keys())}")
        except json.JSONDecodeError as e:
            print(f"Error parsing MEMU_AGENT_DIRS: {e}")
            agent_dirs = {}
    else:
        # Backward compatibility: use SESSIONS_DIR for "main" agent
        if SESSIONS_DIR:
            agent_dirs = {"main": SESSIONS_DIR}
            print("Using backward compatibility mode: single agent 'main'")
        else:
            agent_dirs = {}
            print("Warning: No agent directories configured")

    agent_states: dict[str, dict[str, Any]] = {}
    agent_states = _reconcile_agent_watchers(
        agent_dirs=agent_dirs,
        agent_states=agent_states,
        observer=observer,
        flush_idle_seconds=flush_idle_seconds,
        flush_poll_seconds=flush_poll_seconds,
    )

    def _shutdown_handler(signum, frame):
        try:
            observer.stop()
        finally:
            _release_lock(watcher_lock_name, watcher_lock_handle)
        raise SystemExit(0)

    # Ensure SIGTERM/SIGINT releases the singleton lock.
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

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
            agent_states = _reconcile_agent_watchers(
                agent_dirs=agent_dirs,
                agent_states=agent_states,
                observer=observer,
                flush_idle_seconds=flush_idle_seconds,
                flush_poll_seconds=flush_poll_seconds,
            )
            flush_results = process_agent_idle_flushes(
                agent_states=agent_states,
                agent_dirs=agent_dirs,
                flush_idle_seconds=flush_idle_seconds,
                flush_poll_seconds=flush_poll_seconds,
            )
            if flush_results["errors"]:
                print(
                    f"[watch_sync] idle flush completed with failures: "
                    f"{len(flush_results['failed'])}/{len(agent_states)} agents",
                    file=sys.stderr,
                )
    except KeyboardInterrupt:
        observer.stop()
    finally:
        _release_lock(watcher_lock_name, watcher_lock_handle)
    observer.join()
