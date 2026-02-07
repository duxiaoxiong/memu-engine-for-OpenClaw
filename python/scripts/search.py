import asyncio
import os
import sys

from memu.app.service import MemoryService
from memu.app.settings import (
    DatabaseConfig,
    LLMConfig,
    MetadataStoreConfig,
    RetrieveConfig,
)


def _env(name: str, default: str | None = None) -> str | None:
    # Try actual environment first
    v = os.getenv(name)
    if v is not None and str(v).strip():
        return v
    return default

def get_db_dsn() -> str:
    # 优先使用环境变量传入的 Data Dir
    data_dir = os.getenv("MEMU_DATA_DIR")
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return f"sqlite:///{os.path.join(data_dir, 'memu.db')}"
    # Fallback to hardcoded dev path
    return "sqlite:////home/xiaoxiong/.openclaw/workspace/memU/data/memu.db"

async def search(query_text: str, user_id: str = "xiaoxiong"):
    chat_config = LLMConfig(
        provider="openai",
        base_url=_env("MEMU_CHAT_BASE_URL", "http://192.168.31.109:8317/v1"),
        api_key=_env("MEMU_CHAT_API_KEY", "your-api-key-1"),
        chat_model=_env("MEMU_CHAT_MODEL", "gemini-3-flash-preview"),
    )
    embed_config = LLMConfig(
        provider="openai",
        base_url=_env("MEMU_EMBED_BASE_URL", "https://api.siliconflow.cn/v1"),
        api_key=_env("SILICONFLOW_API_KEY", ""),
        embed_model=_env("MEMU_EMBED_MODEL", "BAAI/bge-m3"),
    )
    db_config = DatabaseConfig(
        metadata_store=MetadataStoreConfig(
            provider="sqlite",
            dsn=get_db_dsn(),
        )
    )

    # Always query DB; keep results small to avoid context explosion.
    retr_config = RetrieveConfig(
        route_intention=False,
        item={"enabled": True, "top_k": 8},
        category={"enabled": True, "top_k": 5},
        resource={"enabled": True, "top_k": 3},
    )

    service = MemoryService(
        llm_profiles={"default": chat_config, "embedding": embed_config},
        database_config=db_config,
        retrieve_config=retr_config,
    )

    results = await service.retrieve(
        queries=[{"role": "user", "content": query_text}],
        where={"user_id": user_id},
    )
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: search.py <query>")
        sys.exit(1)

    query = sys.argv[1]
    try:
        res = asyncio.run(search(query))
        items = res.get("items", [])
        if items:
            print(f"--- Results for: {query} ---")
            for i in items:
                mtype = i.get("memory_type")
                summary = i.get("summary")
                res_url = i.get("resource_url")
                # 构造一个 memory_get 可用的路径标识
                # 如果是文件路径，保持原样；如果是 ID，加上前缀
                source_path = res_url
                if res_url and not res_url.startswith("/") and not res_url.startswith("."):
                    source_path = f"memu://{res_url}"
                
                source_part = f" [Source: {source_path}]" if source_path else ""
                print(f"- [{mtype}]: {summary}{source_part}")
        else:
            cats = res.get("categories", [])
            if cats:
                print(f"--- Category Summaries for: {query} ---")
                for c in cats:
                    print(f"- Category [{c.get('name')}]: {c.get('summary')}")
            else:
                print("No relevant memories found in database.")
    except Exception as e:
        print(f"Search failed: {e}")
