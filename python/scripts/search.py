import asyncio
import os
import sys

from memu.app.service import MemoryService
from memu.app.settings import (
    DatabaseConfig,
    LLMConfig,
    MetadataStoreConfig,
    RetrieveConfig,
    RetrieveCategoryConfig,
    RetrieveItemConfig,
    RetrieveResourceConfig,
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
        base = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        data_dir = os.path.join(base, "data")

    os.makedirs(data_dir, exist_ok=True)
    return f"sqlite:///{os.path.join(data_dir, 'memu.db')}"


async def search(query_text: str, user_id: str = "xiaoxiong"):
    user_id = _env("MEMU_USER_ID", user_id) or user_id
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

    # Always query DB; keep results small to avoid context explosion.
    retr_config = RetrieveConfig(
        route_intention=False,
        item=RetrieveItemConfig(enabled=True, top_k=8),
        category=RetrieveCategoryConfig(enabled=True, top_k=5),
        resource=RetrieveResourceConfig(enabled=True, top_k=3),
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
        
        # 1. Collect all unique sources from items, categories, and resources
        sources = set()
        items = res.get("items", [])
        cats = res.get("categories", [])
        resources = res.get("resources", [])

        def format_source(url):
            if not url: return None
            if not url.startswith("/") and not url.startswith("."):
                return f"memu://{url}"
            return url

        for i in items:
            if url := i.get("resource_url"):
                if s := format_source(url): sources.add(s)
        
        for r in resources:
            if url := r.get("url"):
                if s := format_source(url): sources.add(s)

        # 2. Print Header
        print(f"--- [memU Retrieval System] ---")
        
        # 3. Print Category Summaries (Primary Insight)
        if cats:
            print(f"--- Category Summaries for: {query} ---")
            for c in cats:
                name = c.get('name', 'General')
                summary = c.get('summary', '')
                print(f"- Category [{name}]: {summary}")
        
        # 4. Print Atomic Items (Detailed Evidence)
        if items:
            print(f"\n--- Detailed Memories for: {query} ---")
            for i in items:
                mtype = i.get("memory_type", "fact")
                summary = i.get("summary", "")
                url = i.get("resource_url")
                source_part = f" (Source: {format_source(url)})" if url else ""
                print(f"- [{mtype}]: {summary}{source_part}")

        # 5. Print Global Sources Footer (Critical for traceability)
        if sources:
            print(f"\n--- Sources ---")
            for s in sorted(list(sources)):
                print(f"- {s}")
        
        if not items and not cats:
            print("No relevant memories found in database.")

    except Exception as e:
        print(f"Search failed: {e}")
