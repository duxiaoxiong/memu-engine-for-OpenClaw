import time
import subprocess
import os
import json
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Configuration paths
SESSIONS_DIR = os.getenv("OPENCLAW_SESSIONS_DIR", "/home/xiaoxiong/.openclaw/agents/main/sessions")
MEMU_DIR = os.path.dirname(os.path.abspath(__file__)) 
LOCK_FILE = "/tmp/memu_sync.lock"

def get_extra_paths():
    try:
        return json.loads(os.getenv("MEMU_EXTRA_PATHS", "[]"))
    except:
        return []

class SyncHandler(FileSystemEventHandler):
    def __init__(self, script_name, extensions):
        self.script_name = script_name
        self.extensions = extensions
        self.last_run = 0
        self.debounce_seconds = 5 

    def on_modified(self, event):
        if event.is_directory: return
        if not any(event.src_path.endswith(ext) for ext in self.extensions): return
        self.trigger_sync()

    def on_created(self, event):
        if event.is_directory: return
        if not any(event.src_path.endswith(ext) for ext in self.extensions): return
        self.trigger_sync()

    def trigger_sync(self):
        now = time.time()
        if now - self.last_run < self.debounce_seconds:
            return
        
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Change detected, triggering {self.script_name}...")
        try:
            env = os.environ.copy()
            # Use flock to prevent parallel runs of the SAME script type
            # Note: We use different lock files for sessions vs docs to allow parallel ingest
            lock_name = f"{LOCK_FILE}_{self.script_name}"
            subprocess.run([
                "flock", "-n", lock_name, 
                "uv", "run", self.script_name
            ], cwd=MEMU_DIR, env=env)
            self.last_run = time.time()
        except Exception as e:
            print(f"Failed to trigger {self.script_name}: {e}")

if __name__ == "__main__":
    observer = Observer()
    
    # 1. Watch Sessions
    if os.path.exists(SESSIONS_DIR):
        print(f"Watching sessions: {SESSIONS_DIR}")
        session_handler = SyncHandler("auto_sync.py", [".jsonl"])
        observer.schedule(session_handler, SESSIONS_DIR, recursive=False)
        # Trigger initial sync
        session_handler.trigger_sync()
    else:
        print(f"Warning: Session dir {SESSIONS_DIR} not found.")

    # 2. Watch Docs (Extra Paths)
    extra_paths = get_extra_paths()
    if extra_paths:
        docs_handler = SyncHandler("docs_ingest.py", [".md"])
        for path_item in extra_paths:
            if os.path.exists(path_item):
                print(f"Watching docs: {path_item}")
                # Watch recursively for docs
                observer.schedule(docs_handler, path_item, recursive=True)
            else:
                print(f"Warning: Extra path {path_item} not found.")
        # Trigger initial docs sync
        docs_handler.trigger_sync()

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
