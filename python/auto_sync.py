import asyncio
import os
import time
from typing import Any

from memu.app.service import MemoryService
from memu.app.settings import (
    DatabaseConfig,
    LLMConfig,
    MemorizeConfig,
    MetadataStoreConfig,
)

from convert_sessions import convert

SYNC_MARKER = os.path.join(os.getenv("MEMU_DATA_DIR", "/home/xiaoxiong/.openclaw/workspace/memU/data"), "last_sync_ts")

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


def build_service() -> MemoryService:
    # Chat LLM (Extraction)
    chat_provider = _env("MEMU_CHAT_PROVIDER", "openai")
    chat_config = LLMConfig(
        provider=chat_provider,
        base_url=_env("MEMU_CHAT_BASE_URL", "http://192.168.31.109:8317/v1"),
        api_key=_env("MEMU_CHAT_API_KEY", "your-api-key-1"),
        chat_model=_env("MEMU_CHAT_MODEL", "gemini-3-flash-preview"),
    )

    # Embedding (Generic OpenAI Compatible)
    embed_provider = _env("MEMU_EMBED_PROVIDER", "openai")
    embed_config = LLMConfig(
        provider=embed_provider,
        base_url=_env("MEMU_EMBED_BASE_URL", "https://api.siliconflow.cn/v1"),
        api_key=_env("MEMU_EMBED_API_KEY", ""),
        embed_model=_env("MEMU_EMBED_MODEL", "BAAI/bge-m3"),
    )

    db_config = DatabaseConfig(
        metadata_store=MetadataStoreConfig(provider="sqlite", dsn=get_db_dsn())
    )

    # Key change: include knowledge/skill/tool so technical discussions become retrievable.
    memorize_config = MemorizeConfig(
        memory_types=["profile", "event", "knowledge", "behavior", "skill", "tool"],
        enable_item_references=True,
        enable_item_reinforcement=True,
    )

    return MemoryService(
        llm_profiles={"default": chat_config, "embedding": embed_config},
        database_config=db_config,
        memorize_config=memorize_config,
    )


def _read_last_sync() -> float:
    try:
        with open(SYNC_MARKER, "r", encoding="utf-8") as f:
            return float(f.read().strip() or "0")
    except Exception:
        return 0.0


def _write_last_sync(ts: float) -> None:
    os.makedirs(os.path.dirname(SYNC_MARKER), exist_ok=True)
    with open(SYNC_MARKER, "w", encoding="utf-8") as f:
        f.write(str(ts))


async def sync_once(user_id: str = "xiaoxiong") -> None:
    last_sync = _read_last_sync()
    now_ts = time.time()

    # 1) Convert updated OpenClaw session jsonl -> memU JSON resources
    converted_paths = convert(since_ts=last_sync)

    if not converted_paths:
        print("[memU auto_sync] no updated sessions to ingest.")
        _write_last_sync(now_ts)
        return

    # 2) Ingest converted conversations into memU
    service = build_service()

    ok = 0
    fail = 0

    timeout_s = int(_env("MEMU_MEMORIZE_TIMEOUT_SECONDS", "600") or "600")

    for p in converted_paths:
        try:
            print(f"[memU auto_sync] ingest: {p}", flush=True)
            await asyncio.wait_for(
                service.memorize(resource_url=p, modality="conversation", user={"user_id": user_id}),
                timeout=timeout_s,
            )
            ok += 1
        except asyncio.TimeoutError:
            print(f"[memU auto_sync] TIMEOUT ingest {p} after {timeout_s}s", flush=True)
            fail += 1
        except Exception as e:
            print(f"[memU auto_sync] FAILED ingest {p}: {e}", flush=True)
            fail += 1

    print(f"[memU auto_sync] done. ok={ok} fail={fail}")
    _write_last_sync(now_ts)


if __name__ == "__main__":
    asyncio.run(sync_once())
