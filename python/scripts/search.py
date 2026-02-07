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
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        # Fallback for standalone dev: use local 'data' dir
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        data_dir = os.path.join(base, "data")
    
    os.makedirs(data_dir, exist_ok=True)
    return f"sqlite:///{os.path.join(data_dir, 'memu.db')}"

async def search(query_text: str, user_id: str = "xiaoxiong"):
    chat_kwargs = {}
    if p := _env("MEMU_CHAT_PROVIDER"): chat_kwargs["provider"] = p
    if u := _env("MEMU_CHAT_BASE_URL"): chat_kwargs["base_url"] = u
    if k := _env("MEMU_CHAT_API_KEY"): chat_kwargs["api_key"] = k
    if m := _env("MEMU_CHAT_MODEL"): chat_kwargs["chat_model"] = m
    chat_config = LLMConfig(**chat_kwargs)

    embed_kwargs = {}
    if p := _env("MEMU_EMBED_PROVIDER"): embed_kwargs["provider"] = p
    if u := _env("MEMU_EMBED_BASE_URL"): embed_kwargs["base_url"] = u
    if k := _env("MEMU_EMBED_API_KEY"): embed_kwargs["api_key"] = k
    if m := _env("MEMU_EMBED_MODEL"): embed_kwargs["embed_model"] = m
    embed_config = LLMConfig(**embed_kwargs)
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
                # Construct a valid path for memory_get
                # Keep as-is if file path; add prefix if ID
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
