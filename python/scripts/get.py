import asyncio
import argparse
import os
import sys
import json

from memu.app.service import MemoryService
from memu.app.settings import DatabaseConfig, LLMConfig, MetadataStoreConfig


def get_db_dsn() -> str:
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        base = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        data_dir = os.path.join(base, "data")
    os.makedirs(data_dir, exist_ok=True)
    return f"sqlite:///{os.path.join(data_dir, 'memu.db')}"


def _expand_short_path(short: str) -> str | None:
    import re

    data_dir = os.getenv("MEMU_DATA_DIR", "")
    workspace_dir = os.getenv(
        "MEMU_WORKSPACE_DIR", os.path.expanduser("~/.openclaw/workspace")
    )
    extra_paths_json = os.getenv("MEMU_EXTRA_PATHS", "[]")
    try:
        extra_paths: list[str] = (
            json.loads(extra_paths_json) if extra_paths_json else []
        )
    except Exception:
        extra_paths = []

    if short.startswith("ws:"):
        rel = short[3:]
        return os.path.join(workspace_dir, rel) if rel else workspace_dir

    m = re.match(r"^ext(\d+):(.*)$", short)
    if m:
        idx, rel = int(m.group(1)), m.group(2)
        if 0 <= idx < len(extra_paths):
            return os.path.join(extra_paths[idx], rel) if rel else extra_paths[idx]

    m = re.match(r"^conv:([a-f0-9-]+):p(\d+)$", short)
    if m:
        prefix, part = m.group(1), int(m.group(2))
        conv_dir = os.path.join(data_dir, "conversations") if data_dir else ""
        if conv_dir and os.path.isdir(conv_dir):
            for f in os.listdir(conv_dir):
                if f.startswith(prefix) and f.endswith(f".part{part:03d}.json"):
                    return os.path.join(conv_dir, f)

    m = re.match(r"^conv:([a-f0-9-]+)$", short)
    if m:
        prefix = m.group(1)
        conv_dir = os.path.join(data_dir, "conversations") if data_dir else ""
        if conv_dir and os.path.isdir(conv_dir):
            for f in os.listdir(conv_dir):
                if f.startswith(prefix) and f.endswith(".json") and ".part" not in f:
                    return os.path.join(conv_dir, f)

    return None


async def get_resource_content(path_or_id: str) -> str:
    is_memu_uri = path_or_id.startswith("memu://")
    if is_memu_uri:
        path_or_id = path_or_id.replace("memu://", "", 1)

    if path_or_id.startswith(("conv:", "ws:", "ext")):
        expanded = _expand_short_path(path_or_id)
        if expanded:
            path_or_id = expanded

    user_id = os.getenv("MEMU_USER_ID") or "default"

    dummy_llm = LLMConfig(
        provider="openai",
        base_url="http://localhost",
        api_key="none",
        chat_model="none",
    )
    db_config = DatabaseConfig(
        metadata_store=MetadataStoreConfig(provider="sqlite", dsn=get_db_dsn())
    )
    service = MemoryService(
        llm_profiles={"default": dummy_llm, "embedding": dummy_llm},
        database_config=db_config,
    )

    if path_or_id.startswith("category/"):
        category_key = path_or_id.split("/", 1)[1]
        categories = service.database.memory_category_repo.list_categories(
            where={"user_id": user_id}
        )
        target = categories.get(category_key)
        if target is None:
            for cat in categories.values():
                if cat.name == category_key:
                    target = cat
                    break
        if target is not None:
            return (
                f"# Category\n\n"
                f"- id: {target.id}\n"
                f"- name: {target.name}\n"
                f"- description: {target.description or ''}\n\n"
                f"## Summary\n\n{target.summary or ''}\n"
            )

    if path_or_id.startswith("item/"):
        item_key = path_or_id.split("/", 1)[1]
        item = service.database.memory_item_repo.get_item(item_key)
        if item is not None:
            return (
                f"# Memory Item\n\n"
                f"- id: {item.id}\n"
                f"- memory_type: {item.memory_type}\n"
                f"- resource_id: {item.resource_id}\n"
                f"- created_at: {item.created_at}\n"
                f"- updated_at: {item.updated_at}\n\n"
                f"## Summary\n\n{item.summary or ''}\n"
            )

    if path_or_id.startswith("resource/"):
        resource_key = path_or_id.split("/", 1)[1]
        resources = service.database.resource_repo.list_resources(
            where={"user_id": user_id}
        )
        target = resources.get(resource_key)
        if target is not None:
            return (
                f"# Resource\n\n"
                f"- id: {target.id}\n"
                f"- url: {target.url}\n"
                f"- modality: {target.modality}\n"
                f"- local_path: {target.local_path}\n\n"
                f"## Caption\n\n{target.caption or ''}\n"
            )

    resources = service.database.resource_repo.list_resources(
        where={"user_id": user_id}
    )
    target = None
    for res in resources.values():
        if res.url == path_or_id or res.id == path_or_id:
            target = res
            break

    if target:
        if target.local_path:
            local_path = target.local_path
            candidates: list[str] = []
            if os.path.isabs(local_path):
                candidates.append(local_path)
            else:
                data_dir = os.getenv("MEMU_DATA_DIR")
                if data_dir:
                    candidates.append(os.path.join(data_dir, local_path))
                candidates.append(local_path)

            for p in candidates:
                if p and os.path.exists(p):
                    if p.endswith(".json"):
                        # Convert MemU JSON conversation to Virtual Markdown
                        try:
                            with open(p, "r", encoding="utf-8") as f:
                                data = json.load(f)
                                lines = []
                                messages = data.get("messages", [])
                                for msg in messages:
                                    role = msg.get("role", "unknown")
                                    content = msg.get("content", "")
                                    lines.append(
                                        f"### {role.capitalize()}\n\n{content}\n"
                                    )
                                return "\n".join(lines)
                        except:
                            pass
                    with open(p, "r", encoding="utf-8") as f:
                        return f.read()

    if os.path.exists(path_or_id):
        with open(path_or_id, "r", encoding="utf-8") as f:
            return f.read()

    raise FileNotFoundError(f"Resource not found: {path_or_id}")


def _resolve_file_path(path_str: str) -> str:
    workspace_dir = os.getenv("MEMU_WORKSPACE_DIR")
    if not workspace_dir:
        workspace_dir = os.path.expanduser("~/.openclaw/workspace")

    workspace_real = os.path.realpath(workspace_dir)

    if os.path.isabs(path_str):
        candidate = os.path.realpath(path_str)
    else:
        candidate = os.path.realpath(
            os.path.normpath(os.path.join(workspace_dir, path_str))
        )

    if os.path.commonpath([workspace_real, candidate]) != workspace_real:
        data_dir = os.getenv("MEMU_DATA_DIR")
        if data_dir:
            data_real = os.path.realpath(data_dir)
            if os.path.commonpath([data_real, candidate]) == data_real:
                return candidate

        raise ValueError(f"Path escapes workspace: {path_str}")
    return candidate


def _slice_lines(text: str, from_line: int, lines_count: int | None) -> str:
    all_lines = text.splitlines(keepends=True)
    if from_line < 0:
        from_line = 0

    end = None
    if lines_count is not None:
        end = from_line + lines_count

    return "".join(all_lines[from_line:end])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Path to file or memu:// resource")
    parser.add_argument("--from", dest="from_line", type=int, default=1)
    parser.add_argument("--lines", type=int, default=None)
    parser.add_argument("--offset", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    from_val = max(1, args.from_line)
    if args.offset is not None:
        from_idx = max(0, args.offset)
    else:
        from_idx = from_val - 1

    lines_val = args.lines
    if args.limit is not None:
        lines_val = args.limit

    if lines_val is not None and lines_val < 0:
        lines_val = 0

    try:
        content = asyncio.run(get_resource_content(args.path))
        sliced = _slice_lines(content, from_idx, lines_val)
        print(json.dumps({"path": args.path, "text": sliced}, ensure_ascii=False))
    except Exception as e:
        print(
            json.dumps(
                {"path": args.path, "text": "", "error": str(e)}, ensure_ascii=False
            )
        )
