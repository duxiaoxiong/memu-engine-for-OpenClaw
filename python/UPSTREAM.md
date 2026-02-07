# Upstream Tracking (MemU)

This directory vendors (copies) the upstream MemU Python package into the OpenClaw plugin.
The goal is to keep the plugin self-contained and easy to review, while still being able to
sync from upstream with minimal friction.

## Upstream

- Upstream repository: https://github.com/NevaMind-AI/MemU
- Upstream version/tag: `v1.4.0`
- Upstream commit (best-effort snapshot): `777f1eda1c5a4a3252ffe94f0b98c9c75c6d4539`
- Date imported: 2026-02-07

## What We Vendor

We keep:

- `python/src/memu/` (the MemU Python package)

We do NOT vendor most upstream repo content (docs/examples/tests/assets) because the OpenClaw
extension only needs runtime code.

## OpenClaw Integration Files (Added)

These files are specific to OpenClaw and do not exist upstream:

- `python/watch_sync.py`: watches OpenClaw session logs and triggers ingestion.
- `python/auto_sync.py`: incremental ingestion for OpenClaw sessions (uses `OPENCLAW_SESSIONS_DIR`).
- `python/convert_sessions.py`: converts OpenClaw `.jsonl` sessions into MemU conversation resources.
- `python/docs_ingest.py`: ingests Markdown files from `MEMU_EXTRA_PATHS` (optional).
- `python/scripts/search.py`: CLI entrypoint used by the Node plugin for retrieval.
- `python/scripts/get.py`: CLI entrypoint used by the Node plugin for reading a resource.

## Upstream Patches (Modified Files)

These are changes made to the vendored MemU core to better fit OpenClaw and long-running ingestion.

1) Avoid timeouts during conversation segmentation

- File: `python/src/memu/app/memorize.py`
- Change:
  - Filter out empty prompts and keep memory_type alignment correct.
  - Avoid per-segment LLM summarization in `_split_conversation_into_resources` (keep captions only if
    the preprocessor already provided them).
- Reason: per-segment summarization can be very slow and can cause ingest timeouts.

2) Scope model merging (Pydantic)

- File: `python/src/memu/database/models.py`
- Change: build scoped models with `pydantic.create_model(__base__=...)` instead of multiple inheritance.
- Reason: multiple inheritance between two Pydantic models can trigger MRO inconsistencies (and can confuse
  static checkers). The behavior (a model that contains both scope fields and core fields) stays the same.

3) SQLite compatibility + runtime stability

- File: `python/src/memu/database/sqlite/session.py`
- Change:
  - Preflight DB path creation/touch.
  - Use `NullPool`, set SQLite pragmas (WAL, busy_timeout, foreign_keys).
- Reason: reduces intermittent "unable to open database file" and lock-related failures.

Note:

- Upstream uses table names like `sqlite_*`. On SQLite, names starting with `sqlite_` are reserved for
  internal use and cannot be created. This plugin uses `memu_*` table names instead.
- If you previously ran a build that used a different table naming scheme, delete the existing
  `memu.db` and let it rebuild.

4) SQLModel mapping workaround (embedding fields)

- File: `python/src/memu/database/sqlite/models.py`
- Change: SQLite SQLModel table models do not inherit from the domain Pydantic models.
- Reason: the domain models include `embedding: list[float] | None`, which SQLModel may try to map to a
  column and fail with "no matching SQLAlchemy type". The SQLite models store embeddings as
  `embedding_json` and expose `embedding` as a property.

4) Default OpenAI client timeout

- File: `python/src/memu/llm/openai_sdk.py`
- Change: set a default request timeout (`timeout=120`).
- Reason: avoid hangs during ingestion when a provider stalls.

## How to Sync From Upstream

There is a helper script at the plugin root:

- `update_from_upstream.sh`

Notes:

- It copies upstream `src/` into `python/src/`.
- It keeps OpenClaw-specific scripts.
- It applies a best-effort patch for one of the SQLite/Pydantic issues.
- It may overwrite `python/pyproject.toml` with upstream's maturin-based config. If you rely on the
  simplified packaging in this plugin, re-check `python/pyproject.toml` after syncing.

After syncing:

1) Re-run `openclaw gateway restart`
2) Call `memory_search` once to confirm the plugin still executes
