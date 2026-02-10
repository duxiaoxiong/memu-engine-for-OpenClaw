"""Force-finalize staged tail and trigger memU ingestion.

This script is intended to be invoked via the OpenClaw tool layer.

Behavior:
- Sets MEMU_FORCE_FLUSH=1 for this run.
- Runs a single auto_sync cycle (convert + memorize for newly finalized parts).

Why:
- Allows users/agents to explicitly archive/freeze the current tail chunk and
  push it into memU without waiting for the idle window.
"""

import asyncio
import os
import sys

# Ensure python/ is on import path so we can import auto_sync.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.dirname(THIS_DIR)
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

os.environ["MEMU_FORCE_FLUSH"] = "1"

import auto_sync  # noqa: E402


async def _main() -> None:
    user_id = os.getenv("MEMU_USER_ID", "default")
    await auto_sync.sync_once(user_id=user_id)


if __name__ == "__main__":
    asyncio.run(_main())
