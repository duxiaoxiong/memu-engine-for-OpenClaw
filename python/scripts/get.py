import asyncio
import argparse
import os
import sys

from memu.app.service import MemoryService
from memu.app.settings import DatabaseConfig, LLMConfig, MetadataStoreConfig


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


def _expand_short_path(short: str) -> str | None:
    """Expand shortened memU paths back to full paths."""
    import json
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

    # ws:relative/path -> /workspace_dir/relative/path
    if short.startswith("ws:"):
        rel = short[3:]
        return os.path.join(workspace_dir, rel) if rel else workspace_dir

    # ext{i}:relative/path -> /extra_paths[i]/relative/path
    m = re.match(r"^ext(\d+):(.*)$", short)
    if m:
        idx, rel = int(m.group(1)), m.group(2)
        if 0 <= idx < len(extra_paths):
            return os.path.join(extra_paths[idx], rel) if rel else extra_paths[idx]

    # conv:UUID_PREFIX:pN -> conversations/UUID.partNNN.json
    m = re.match(r"^conv:([a-f0-9-]+):p(\d+)$", short)
    if m:
        prefix, part = m.group(1), int(m.group(2))
        conv_dir = os.path.join(data_dir, "conversations") if data_dir else ""
        if conv_dir and os.path.isdir(conv_dir):
            for f in os.listdir(conv_dir):
                if f.startswith(prefix) and f.endswith(f".part{part:03d}.json"):
                    return f"conversations/{f}"

    # conv:UUID_PREFIX -> conversations/UUID.json
    m = re.match(r"^conv:([a-f0-9-]+)$", short)
    if m:
        prefix = m.group(1)
        conv_dir = os.path.join(data_dir, "conversations") if data_dir else ""
        if conv_dir and os.path.isdir(conv_dir):
            for f in os.listdir(conv_dir):
                if f.startswith(prefix) and f.endswith(".json") and ".part" not in f:
                    return f"conversations/{f}"

    return None


async def get_resource(path_or_id: str):
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
    # Initialize DB only
    service = MemoryService(
        llm_profiles={"default": dummy_llm, "embedding": dummy_llm},
        database_config=db_config,
    )

    # List scoped resources to find a match
    resources = service.database.resource_repo.list_resources(
        where={"user_id": user_id}
    )
    target = None
    for res in resources.values():
        if res.url == path_or_id or res.id == path_or_id:
            target = res
            break

    if target and target.local_path:
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
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()

    # Fallback: treat input as physical path if not found/empty
    if os.path.exists(path_or_id):
        with open(path_or_id, "r", encoding="utf-8") as f:
            return f.read()

    if is_memu_uri:
        return f"Resource not found in memU: {path_or_id}"
    return f"File not found: {path_or_id}"


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

    # Prevent path traversal / reading outside workspace.
    if os.path.commonpath([workspace_real, candidate]) != workspace_real:
        raise ValueError(f"Path escapes workspace: {path_str}")
    return candidate


def _read_file_range(file_path: str, offset: int, limit: int | None) -> str:
    if offset < 0:
        offset = 0
    if limit is not None and limit < 0:
        limit = 0
    out_lines: list[str] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx < offset:
                continue
            if limit is not None and len(out_lines) >= limit:
                break
            out_lines.append(line)
    return "".join(out_lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Path to file or memu:// resource")
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Start line (0-based). Only for file paths.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Number of lines to read. Only for file paths.",
    )
    args = parser.parse_args()

    path_in = args.path
    if path_in.startswith("memu://"):
        try:
            print(asyncio.run(get_resource(path_in)))
        except Exception as e:
            print(f"Error fetching resource: {type(e).__name__}: {e}")
        sys.exit(0)

    file_path = _resolve_file_path(path_in)
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        sys.exit(0)

    try:
        print(_read_file_range(file_path, args.offset, args.limit))
    except Exception as e:
        print(f"Error reading file: {type(e).__name__}: {e}")
