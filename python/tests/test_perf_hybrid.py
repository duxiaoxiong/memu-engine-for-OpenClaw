from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_factory import HybridDatabaseManager
from tests.shared_user_model import SharedUserModel


pytestmark = pytest.mark.perf


class _DeterministicEmbedClient:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


def _fd_count() -> int:
    return len(list(Path("/proc/self/fd").iterdir()))


def test_50_agents_concurrent() -> None:
    with TemporaryDirectory(prefix="memu_hybrid_perf_") as tmp_root:
        tmp_path = Path(tmp_root)
        previous_memory_root = os.environ.get("MEMU_MEMORY_ROOT")
        os.environ["MEMU_MEMORY_ROOT"] = str(tmp_path / "memory-root")

        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(),
            user_model=SharedUserModel,
        )

        try:
            doc_path = tmp_path / "shared_doc.md"
            secret = "hybrid-shared-doc-token-42"
            doc_path.write_text(f"Performance seed content: {secret}", encoding="utf-8")

            ingest_result = asyncio.run(
                manager.ingest_document(
                    file_path=str(doc_path),
                    agent_id="agent-seed",
                    user_id="user-1",
                    embed_client=_DeterministicEmbedClient(),
                )
            )
            assert ingest_result.chunk_count >= 1, "Seeding should ingest at least one document chunk"

            agent_ids = [f"agent-{index}" for index in range(50)]

            async def _query_for_agent(agent_id: str) -> list[dict[str, object]]:
                return await asyncio.to_thread(
                    manager.search_documents,
                    query=secret,
                    requesting_agent_id=agent_id,
                    allow_cross_agent=True,
                )

            async def _run_concurrent_queries() -> list[list[dict[str, object]]]:
                tasks = [_query_for_agent(agent_id) for agent_id in agent_ids]
                return await asyncio.gather(*tasks)

            start = time.perf_counter()
            all_results = asyncio.run(_run_concurrent_queries())
            runtime_seconds = time.perf_counter() - start

            assert runtime_seconds < 5.0, (
                f"50 concurrent hybrid document queries should complete in under 5s; got {runtime_seconds:.3f}s"
            )

            fd_count = _fd_count()
            assert fd_count < 20, f"Open file descriptor count should stay under 20; got {fd_count}"

            assert len(all_results) == len(agent_ids), (
                f"Expected one response list per querying agent; got {len(all_results)} for {len(agent_ids)} agents"
            )

            non_empty_count = sum(1 for results in all_results if results)
            assert non_empty_count >= 40, (
                "At least 40/50 concurrent query responses should be non-empty to demonstrate stable shared access "
                f"under load; got {non_empty_count}/50"
            )

            seeded_doc_seen = any(
                any(result.get("owner_agent_id") == "agent-seed" for result in results)
                for results in all_results
            )
            assert seeded_doc_seen, (
                "At least one concurrent query response should contain the seeded shared document "
                "owned by agent-seed when allow_cross_agent=True"
            )
        finally:
            manager.close()
            if previous_memory_root is None:
                os.environ.pop("MEMU_MEMORY_ROOT", None)
            else:
                os.environ["MEMU_MEMORY_ROOT"] = previous_memory_root
