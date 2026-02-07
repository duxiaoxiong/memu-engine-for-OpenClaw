import asyncio
import os
import sys

from memu.app.service import MemoryService
from memu.app.settings import DatabaseConfig, LLMConfig, MetadataStoreConfig

def get_db_dsn() -> str:
    # Prefer Data Dir from environment variables
    data_dir = os.getenv("MEMU_DATA_DIR")
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return f"sqlite:///{os.path.join(data_dir, 'memu.db')}"
    # Fallback to hardcoded dev path
    return "sqlite:////home/xiaoxiong/.openclaw/workspace/memU/data/memu.db"

async def get_resource(path_or_id: str):
    # Handle memu:// prefix
    if path_or_id.startswith("memu://"):
        path_or_id = path_or_id.replace("memu://", "")

    dummy_llm = LLMConfig(provider="openai", base_url="http://localhost", api_key="none", chat_model="none")
    db_config = DatabaseConfig(metadata_store=MetadataStoreConfig(
        provider="sqlite", dsn=get_db_dsn()
    ))
    # Initialize DB only
    service = MemoryService(llm_profiles={"default": dummy_llm, "embedding": dummy_llm}, database_config=db_config)
    
    # List resources to find a match
    resources = service.database.resource_repo.list_resources()
    target = None
    for res in resources.values():
        if res.url == path_or_id or res.id == path_or_id:
            target = res
            break
    
    if target and target.local_path and os.path.exists(target.local_path):
        with open(target.local_path, "r", encoding="utf-8") as f:
            return f.read()
    
    # Fallback: treat input as physical path if not found/empty
    if os.path.exists(path_or_id):
        with open(path_or_id, "r", encoding="utf-8") as f:
            return f.read()
    
    return f"Resource not found in memU or disk: {path_or_id}"

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    try:
        print(asyncio.run(get_resource(sys.argv[1])))
    except Exception as e:
        print(f"Error fetching resource: {e}")
