import argparse
import asyncio
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


async def get_resource(path_or_id: str):
    is_memu_uri = path_or_id.startswith("memu://")
    if is_memu_uri:
        path_or_id = path_or_id.replace("memu://", "", 1)

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

    # List resources to find a match
    resources = service.database.resource_repo.list_resources()
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
    if os.path.isabs(path_str):
        return path_str
    workspace_dir = os.getenv("MEMU_WORKSPACE_DIR")
    if not workspace_dir:
        workspace_dir = os.path.expanduser("~/.openclaw/workspace")
    return os.path.normpath(os.path.join(workspace_dir, path_str))


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
