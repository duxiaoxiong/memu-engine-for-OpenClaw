import asyncio
import os
import json
from memu.app.service import MemoryService
from memu.app.settings import (
    DatabaseConfig,
    LLMConfig,
    MemorizeConfig,
    MetadataStoreConfig,
)

def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is not None and str(v).strip():
        return v
    return default

def get_db_dsn() -> str:
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        base = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base, "data")
    
    os.makedirs(data_dir, exist_ok=True)
    return f"sqlite:///{os.path.join(data_dir, 'memu.db')}"

def get_extra_paths() -> list[str]:
    raw = os.getenv("MEMU_EXTRA_PATHS", "[]")
    try:
        paths = json.loads(raw)
        if isinstance(paths, list):
            return [p for p in paths if isinstance(p, str)]
    except json.JSONDecodeError:
        pass
    return []

async def main():
    chat_provider = _env("MEMU_CHAT_PROVIDER", "openai")
    chat_config = LLMConfig(
        provider=chat_provider,
        base_url=_env("MEMU_CHAT_BASE_URL", "http://192.168.31.109:8317/v1"),
        api_key=_env("MEMU_CHAT_API_KEY", "your-api-key-1"),
        chat_model=_env("MEMU_CHAT_MODEL", "gemini-3-flash-preview"),
    )
    embed_provider = _env("MEMU_EMBED_PROVIDER", "openai")
    embed_config = LLMConfig(
        provider=embed_provider,
        base_url=_env("MEMU_EMBED_BASE_URL", "https://api.siliconflow.cn/v1"),
        api_key=_env("MEMU_EMBED_API_KEY", ""),
        embed_model=_env("MEMU_EMBED_MODEL", "BAAI/bge-m3"),
    )

    db_config = DatabaseConfig(
        metadata_store=MetadataStoreConfig(
            provider="sqlite",
            dsn=get_db_dsn(),
        )
    )

    memorize_config = MemorizeConfig(
        memory_types=["profile", "event", "knowledge", "behavior", "skill", "tool"],
        enable_item_references=True,
        enable_item_reinforcement=True,
    )

    service = MemoryService(
        llm_profiles={"default": chat_config, "embedding": embed_config},
        database_config=db_config,
        memorize_config=memorize_config,
    )

    extra_paths = get_extra_paths()
    files_to_ingest: list[str] = []

    for path_item in extra_paths:
        if not os.path.exists(path_item):
            continue
        if os.path.isfile(path_item) and path_item.endswith(".md"):
            files_to_ingest.append(path_item)
        elif os.path.isdir(path_item):
            for root, _, filenames in os.walk(path_item):
                for f in filenames:
                    if f.endswith(".md"):
                        files_to_ingest.append(os.path.join(root, f))

    if not files_to_ingest:
        print("[memU docs_ingest] No markdown files found in extraPaths.", flush=True)
        return

    print(f"[memU docs_ingest] Starting ingest of {len(files_to_ingest)} files...", flush=True)
    
    timeout_s = int(_env("MEMU_MEMORIZE_TIMEOUT_SECONDS", "600") or "600")

    ok = 0
    fail = 0

    for file_path in files_to_ingest:
        print(f"[memU docs_ingest] Ingesting: {file_path}", flush=True)
        try:
            await asyncio.wait_for(
                service.memorize(
                    resource_url=file_path,
                    modality="document",
                    user={"user_id": "xiaoxiong"},
                ),
                timeout=timeout_s
            )
            ok += 1
        except Exception as e:
            print(f"[memU docs_ingest] FAILED {file_path}: {e}", flush=True)
            fail += 1

    print(f"[memU docs_ingest] Completed. ok={ok} fail={fail}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
