import asyncio
import os
import sqlite3
import sys
import json
import argparse
import re
import time

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
        base = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        data_dir = os.path.join(base, "data")
    os.makedirs(data_dir, exist_ok=True)
    return f"sqlite:///{os.path.join(data_dir, 'memu.db')}"


async def search(
    query_text: str,
    max_results: int = 10,
    min_score: float = 0.0,
    user_id: str = "default",
    mode: str = "fast",
    category_quota: int | None = None,
    item_quota: int | None = None,
    queries: list[dict] | None = None,
):
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

    retrieval_mode = (mode or "fast").strip().lower()
    if retrieval_mode not in ("fast", "full"):
        retrieval_mode = "fast"

    route_intention = retrieval_mode == "full"
    sufficiency_check = retrieval_mode == "full"

    retr_config = RetrieveConfig(
        route_intention=route_intention,
        sufficiency_check=sufficiency_check,
        item=RetrieveItemConfig(enabled=True, top_k=max_results),
        category=RetrieveCategoryConfig(enabled=True, top_k=min(5, max_results)),
        resource=RetrieveResourceConfig(enabled=True, top_k=min(5, max_results)),
    )

    t0 = time.perf_counter()
    service = MemoryService(
        llm_profiles={"default": chat_config, "embedding": embed_config},
        database_config=db_config,
        retrieve_config=retr_config,
    )
    t1 = time.perf_counter()

    effective_queries = queries or [{"role": "user", "content": query_text}]
    if not effective_queries:
        effective_queries = [{"role": "user", "content": query_text}]

    t2 = time.perf_counter()
    results = await service.retrieve(
        queries=effective_queries,
        where={"user_id": user_id},
    )
    t3 = time.perf_counter()

    if (_env("MEMU_DEBUG_TIMING", "false") or "").lower() == "true":
        timing = {
            "init_ms": round((t1 - t0) * 1000, 2),
            "pre_retrieve_ms": round((t2 - t1) * 1000, 2),
            "retrieve_ms": round((t3 - t2) * 1000, 2),
            "total_ms": round((t3 - t0) * 1000, 2),
            "mode": retrieval_mode,
            "max_results": max_results,
        }
        if isinstance(results, dict):
            results["_timing"] = timing

    return results


def shorten_path(abs_path: str, workspace_dir: str, extra_paths: list[str]) -> str:
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

    m = re.search(r"conversations/([a-f0-9-]+)\.part(\d+)\.json$", abs_path)
    if m:
        return f"conv:{m.group(1)[:8]}:p{int(m.group(2))}"
    m = re.search(r"conversations/([a-f0-9-]+)\.json$", abs_path)
    if m:
        return f"conv:{m.group(1)[:8]}"

    return abs_path


def format_source(url, workspace_dir, extra_paths):
    if not url:
        return None
    short = shorten_path(url, workspace_dir, extra_paths)
    if short != url:
        return f"memu://{short}"
    if url.startswith("/"):
        return f"memu://{short}"
    return f"memu://{url}"


def normalize_snippet(text: str) -> str:
    if not text:
        return ""
    s = text.strip().lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s)
    return s


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="Search query")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--category-quota", type=int, default=None)
    parser.add_argument("--item-quota", type=int, default=None)
    parser.add_argument(
        "--mode",
        type=str,
        default="fast",
        choices=["fast", "full"],
        help="Retrieval mode: fast (vector-focused) or full (memU progressive LLM checks).",
    )
    parser.add_argument(
        "--queries-json",
        type=str,
        default="",
        help="Optional JSON array of chat messages for memU context-aware retrieval.",
    )
    args = parser.parse_args()

    try:
        query_messages = None
        if args.queries_json:
            try:
                parsed = json.loads(args.queries_json)
                if isinstance(parsed, list):
                    query_messages = parsed
            except Exception:
                query_messages = None

        res = asyncio.run(
            search(
                args.query,
                args.max_results,
                args.min_score,
                mode=args.mode,
                category_quota=args.category_quota,
                item_quota=args.item_quota,
                queries=query_messages,
            )
        )

        items = res.get("items", [])
        cats = res.get("categories", [])
        resources = res.get("resources", [])

        resource_url_map = {r.get("id"): r.get("url") for r in resources}
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
        extra_paths_json = _env("MEMU_EXTRA_PATHS", "[]")
        try:
            extra_paths = json.loads(extra_paths_json) if extra_paths_json else []
        except Exception:
            extra_paths = []

        output_results = []
        SNIPPET_BUDGET = 4000
        SNIPPET_MAX = 700

        category_quota = args.category_quota
        item_quota = args.item_quota

        if category_quota is None and item_quota is None:
            if args.max_results >= 10:
                category_quota = 3 if args.max_results <= 10 else 4
            elif args.max_results >= 6:
                category_quota = 2
            else:
                category_quota = 1
            category_quota = min(category_quota, args.max_results)
            item_quota = max(0, args.max_results - category_quota)
        else:
            category_quota = 0 if category_quota is None else max(0, category_quota)
            item_quota = 0 if item_quota is None else max(0, item_quota)
            total_quota = category_quota + item_quota
            if total_quota == 0:
                category_quota = min(1, args.max_results)
                item_quota = max(0, args.max_results - category_quota)
            elif total_quota > args.max_results:
                scale = args.max_results / total_quota
                category_quota = int(category_quota * scale)
                item_quota = int(item_quota * scale)
                while category_quota + item_quota < args.max_results:
                    if category_quota <= item_quota:
                        category_quota += 1
                    else:
                        item_quota += 1

        filtered_cats = [c for c in cats if c.get("score", 0.0) >= args.min_score]
        filtered_items = [i for i in items if i.get("score", 0.0) >= args.min_score]
        filtered_cats.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        filtered_items.sort(key=lambda x: x.get("score", 0.0), reverse=True)

        seen_norm_snippets = set()

        selected_cats = filtered_cats[:category_quota]
        selected_items = filtered_items[:item_quota]

        for c in selected_cats:
            score = c.get("score", 0.0)
            snippet = c.get("summary", "")[:SNIPPET_MAX]
            norm = normalize_snippet(snippet)
            if not norm or norm in seen_norm_snippets:
                continue
            seen_norm_snippets.add(norm)

            cat_id = c.get("id") or c.get("name", "unknown")
            output_results.append(
                {
                    "path": f"memu://category/{cat_id}",
                    "startLine": 1,
                    "endLine": 1,
                    "score": score,
                    "snippet": snippet,
                    "source": "memory",
                }
            )

        for i in selected_items:
            score = i.get("score", 0.0)
            url = resource_url_map.get(i.get("resource_id"))
            item_id = i.get("id") or "unknown"
            path = (
                format_source(url, workspace_dir, extra_paths)
                or f"memu://item/{item_id}"
            )

            snippet = i.get("summary", "")[:SNIPPET_MAX]
            norm = normalize_snippet(snippet)
            if not norm or norm in seen_norm_snippets:
                continue
            seen_norm_snippets.add(norm)

            output_results.append(
                {
                    "path": path,
                    "startLine": 1,
                    "endLine": 1,
                    "score": score,
                    "snippet": snippet,
                    "source": "memory",
                }
            )

        output_results = output_results[: args.max_results]

        trimmed_results = []
        remaining = SNIPPET_BUDGET
        for r in output_results:
            if remaining <= 0:
                break
            snippet = r.get("snippet", "")
            if len(snippet) > remaining:
                snippet = snippet[:remaining]
            if not snippet:
                continue
            r = {**r, "snippet": snippet}
            trimmed_results.append(r)
            remaining -= len(snippet)

        print(
            json.dumps(
                {
                    "results": trimmed_results,
                    "provider": _env("MEMU_CHAT_PROVIDER", "openai") or "openai",
                    "model": _env("MEMU_CHAT_MODEL", "unknown") or "unknown",
                    "fallback": None,
                    "citations": "off",
                },
                ensure_ascii=False,
            )
        )

    except Exception as e:
        print(
            json.dumps(
                {
                    "results": [],
                    "provider": _env("MEMU_CHAT_PROVIDER", "openai") or "openai",
                    "model": _env("MEMU_CHAT_MODEL", "unknown") or "unknown",
                    "fallback": None,
                    "citations": "off",
                    "error": str(e),
                },
                ensure_ascii=False,
            )
        )
