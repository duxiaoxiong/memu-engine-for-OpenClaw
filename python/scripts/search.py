import asyncio
import os
import sqlite3
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


def _db_has_column(conn: sqlite3.Connection, *, table: str, column: str) -> bool:
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cur.fetchall() if len(row) > 1]
        return column in set(cols)
    except Exception:
        return False


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


async def search(query_text: str, user_id: str = "default"):
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
        category=RetrieveCategoryConfig(
            enabled=True, top_k=2
        ),  # Reduced for conciseness
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

        items = res.get("items", [])
        cats = res.get("categories", [])
        resources = res.get("resources", [])

        # Build resource_id -> url lookup
        resource_url_map = {r.get("id"): r.get("url") for r in resources}

        # Ensure we can resolve sources even when `resources` top_k doesn't include the item's resource.
        # (MemU retrieve can return items without returning their resource objects.)
        item_resource_ids = {
            i.get("resource_id")
            for i in items
            if isinstance(i, dict) and i.get("resource_id")
        }
        missing_ids = [rid for rid in item_resource_ids if rid not in resource_url_map]
        if missing_ids:
            try:
                data_dir = os.getenv("MEMU_DATA_DIR")
                if data_dir:
                    db_path = os.path.join(data_dir, "memu.db")
                    conn = sqlite3.connect(db_path)
                    cur = conn.cursor()
                    placeholders = ",".join(["?"] * len(missing_ids))
                    user_id = _env("MEMU_USER_ID", "default") or "default"
                    if _db_has_column(conn, table="memu_resources", column="user_id"):
                        cur.execute(
                            f"SELECT id, url FROM memu_resources WHERE id IN ({placeholders}) AND user_id = ?",
                            [*missing_ids, user_id],
                        )
                    else:
                        cur.execute(
                            f"SELECT id, url FROM memu_resources WHERE id IN ({placeholders})",
                            missing_ids,
                        )
                    for rid, url in cur.fetchall():
                        resource_url_map[rid] = url
                    conn.close()
            except Exception:
                pass

        workspace_dir = _env(
            "MEMU_WORKSPACE_DIR", os.path.expanduser("~/.openclaw/workspace")
        )
        memu_data_dir = _env("MEMU_DATA_DIR", "")

        extra_paths_json = _env("MEMU_EXTRA_PATHS", "[]")
        try:
            import json

            extra_paths: list[str] = (
                json.loads(extra_paths_json) if extra_paths_json else []
            )
        except Exception:
            extra_paths = []

        def shorten_path(abs_path: str) -> str:
            """Shorten absolute paths using prefix aliases.

            Conversion rules:
            - /workspace_dir/... -> ws:...
            - /extra_paths[i]/... -> ext{i}:...
            - memU internal paths handled separately
            """
            import re

            if not abs_path:
                return abs_path

            for i, ep in enumerate(extra_paths):
                if abs_path.startswith(ep + "/"):
                    rel = abs_path[len(ep) + 1 :]
                    return f"ext{i}:{rel}"
                if abs_path == ep:
                    return f"ext{i}:"

            if workspace_dir and abs_path.startswith(workspace_dir + "/"):
                rel = abs_path[len(workspace_dir) + 1 :]
                return f"ws:{rel}"
            if workspace_dir and abs_path == workspace_dir:
                return "ws:"

            # conversations/UUID.partNNN.json -> conv:UUID[:8]:pN
            m = re.search(r"conversations/([a-f0-9-]+)\.part(\d+)\.json$", abs_path)
            if m:
                return f"conv:{m.group(1)[:8]}:p{int(m.group(2))}"
            m = re.search(r"conversations/([a-f0-9-]+)\.json$", abs_path)
            if m:
                return f"conv:{m.group(1)[:8]}"

            return abs_path

        def format_source(url):
            if not url:
                return None
            short = shorten_path(url)
            if short != url:
                return f"memu://{short}"
            if url.startswith("/"):
                return f"memu://{shorten_path(url)}"
            return f"memu://{url}"

        def get_item_source(item):
            resource_id = item.get("resource_id")
            return resource_url_map.get(resource_id) if resource_id else None

        # 1. Print Header
        print("--- [memU Retrieval System] ---")

        # 2. Print Category Summaries (Primary Insight)
        if cats:
            print(f"--- Category Summaries for: {query} ---")
            for c in cats:
                name = c.get("name", "General")
                summary = c.get("summary", "")
                print(f"- Category [{name}]: {summary}")

        # 3. Print Atomic Items (Detailed Evidence)
        if items:
            print(f"\n--- Detailed Memories for: {query} ---")
            for i in items:
                mtype = i.get("memory_type", "fact")
                summary = i.get("summary", "")
                url = get_item_source(i)
                source_part = f" (Source: {format_source(url)})" if url else ""
                print(f"- [{mtype}]: {summary}{source_part}")

        if not items and not cats:
            print("No relevant memories found in database.")

    except Exception as e:
        print(f"Search failed: {e}")
