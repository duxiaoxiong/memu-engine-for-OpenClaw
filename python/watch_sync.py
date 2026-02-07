import time
import subprocess
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 配置路径 (从环境变量读取，支持动态配置)
WATCH_DIR = os.getenv("OPENCLAW_SESSIONS_DIR", "/home/xiaoxiong/.openclaw/agents/main/sessions")
MEMU_DIR = os.path.dirname(os.path.abspath(__file__)) # Assume script is in python root
SYNC_SCRIPT = "auto_sync.py"
LOCK_FILE = "/tmp/memu_sync.lock"

class SessionHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_run = 0
        self.debounce_seconds = 5 # 防止连珠炮式的消息导致频繁触发

    def on_modified(self, event):
        if event.is_directory or not event.src_path.endswith(".jsonl"):
            return
        self.trigger_sync()

    def on_created(self, event):
        if event.src_path.endswith(".jsonl"):
            self.trigger_sync()

    def trigger_sync(self):
        now = time.time()
        if now - self.last_run < self.debounce_seconds:
            return
        
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Change detected, triggering sync...")
        try:
            # 传递当前的环境变量给子进程，确保 auto_sync.py 能读到配置
            env = os.environ.copy()
            # 使用 flock 确保不会并行运行
            subprocess.run([
                "flock", "-n", LOCK_FILE, 
                "uv", "run", SYNC_SCRIPT
            ], cwd=MEMU_DIR, env=env)
            self.last_run = time.time()
        except Exception as e:
            print(f"Failed to trigger sync: {e}")

if __name__ == "__main__":
    # 确保目录存在
    if not os.path.exists(WATCH_DIR):
        print(f"Error: Watch directory {WATCH_DIR} does not exist.")
        exit(1)

    print(f"Starting memU watch-sync on {WATCH_DIR}...")
    event_handler = SessionHandler()
    observer = Observer()
    observer.schedule(event_handler, WATCH_DIR, recursive=False)
    observer.start()
    
    # 启动时先跑一次全量增量同步，确保不漏掉启动前的更新
    event_handler.trigger_sync()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
