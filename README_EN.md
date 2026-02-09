# memU Engine for OpenClaw

Links:

- OpenClaw: https://github.com/openclaw/openclaw
- MemU (upstream): https://github.com/NevaMind-AI/MemU

Language:

- [Chinese (中文)](README.md)

## What this is

`memu-engine` is a community OpenClaw memory plugin that wires OpenClaw sessions into the MemU engine.
It provides `memory_search` and `memory_get`, and keeps a SQLite-backed long-term store under the OpenClaw
workspace.

This is not an official MemU/OpenClaw project. It is a pragmatic integration that tries to stay close to
upstream and keep the moving parts simple.

## What it does

- Watches OpenClaw session `.jsonl` files and incrementally ingests new messages.
- Uses MemU to extract atomic memory items (profile/event/knowledge/skill/tool, etc.).
- Stores everything in SQLite at `~/.openclaw/workspace/memU/data/memu.db`.

It can also ingest extra Markdown sources (for example: docs inside your workspace, or extension docs),
so the memory database can answer questions with citations to real files.

## Install

### Ask OpenClaw to install (agent-friendly)

If you are using OpenClaw as an agent that can operate your machine, you can usually just tell it to read
this README and install the extension.

Suggested message to OpenClaw:

```text
Please install the OpenClaw plugin `memu-engine` from https://github.com/duxiaoxiong/memu-engine-for-OpenClaw
```

### Manual install

1) Download from git

```bash
mkdir -p ~/src
cd ~/src
git clone https://github.com/<you>/<repo>.git memu-engine
git clone https://github.com/duxiaoxiong/memu-engine-for-OpenClaw.git memu-engine
```

2) Copy into the OpenClaw extensions directory

```bash
mkdir -p ~/.openclaw/extensions
rm -rf ~/.openclaw/extensions/memu-engine
cp -R ~/src/memu-engine ~/.openclaw/extensions/memu-engine
```

3) Configure OpenClaw first (before restarting)

Edit `~/.openclaw/openclaw.json` and set the memory slot + plugin config (example below).

4) Restart the gateway

```bash
openclaw gateway restart
```

If you restart the gateway before updating `openclaw.json`, OpenClaw may still be using the old memory
slot and you can see confusing errors.

### Initial Sync & Trigger

When you first install the plugin (or reset the database), the memory database will be empty.
The plugin uses a "lazy load" strategy:

1. Restarting the Gateway **does NOT** immediately start the sync process.
2. The first time you (or the AI) interact with the plugin (e.g., send a message, call `memory_search`), the background sync service will start.
3. On startup, it detects if the database is empty or if there are new sessions, and triggers a **full historical sync**.

So after installation, just say "Hello" to your agent to kick off the initial build.

### Real-time Sync

Once running, the background service watches for changes:

- **Sessions**: Syncs new messages from `~/.openclaw/sessions/*.jsonl` in real-time.
- **Docs**: Watches configured markdown paths.
- **Debounce**: Changes are processed with a 5-second debounce to avoid churning on rapid writes.
- **Efficiency**: Only files with modified timestamps are processed. If no files changed, no LLM calls are made.
- **Locking**: Uses a file lock to prevent multiple processes from syncing simultaneously (stale lock expiry: 15 mins).

## Configure

In `~/.openclaw/openclaw.json`, assign the memory slot and provide model settings.

This plugin passes config to MemU via environment variables:

- `embedding.*` -> `MEMU_EMBED_*`
- `extraction.*` -> `MEMU_CHAT_*`

Example (no real keys):

```json
{
  "plugins": {
    "slots": { "memory": "memu-engine" },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "embedding": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "model": "text-embedding-3-small"
          },
          "extraction": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "model": "gpt-4o-mini"
          },
          "language": "zh"
        }
      }
    }
  }
}
```

> ⚠️ **Note**: If the extraction model is too slow, the sync process may timeout and fail silently.

Optional:

- `language`: output language for memory summaries (`zh`, `en`, `ja`). If not set, uses the default behavior (English).
- `ingest.extraPaths`: list of directories/files to ingest Markdown from.
- `MEMU_USER_ID`: override the default user id (default: `default`).

### Output Language

By default, MemU extracts memory summaries in English. For Chinese users, set `language` to `zh`:

```json
{
  "plugins": {
    "entries": {
      "memu-engine": {
        "config": {
          "language": "zh",
          "embedding": { ... },
          "extraction": { ... }
        }
      }
    }
  }
}
```

Supported languages: `zh` (Chinese), `en` (English), `ja` (Japanese).

### Import extra Markdown (docs)

By default, this plugin ingests common OpenClaw Markdown sources:

- `~/.openclaw/workspace/*.md` (for example: `AGENTS.md`, `MEMORY.md`)
- `~/.openclaw/workspace/memory/*.md` (durable memory notes)

You can disable the defaults by setting `ingest.includeDefaultPaths` to `false`.

If you set `ingest.extraPaths`, the background watcher will also:

- scan those directories/files for `*.md`
- ingest them into the MemU SQLite store
- re-ingest on changes (debounced)

This is useful for indexing things like:

- your project docs (`docs/`, `README.md`)
- OpenClaw extension docs (any folder path you provide)

Example:

```json
{
  "plugins": {
    "entries": {
      "memu-engine": {
        "config": {
          "ingest": {
            "includeDefaultPaths": true,
            "extraPaths": [
              "/home/you/project/docs",
              "/home/you/project/README.md"
            ]
          }
        }
      }
    }
  }
}
```

### Read back memory sources

`memory_get` accepts:

- a physical file path (it reads from disk)
- a MemU resource id / URL in the form `memu://<id>`

`memory_search` prints a `Source:` for each hit. You can pass that value into `memory_get` to read the
full text.

## Local model support

MemU supports configuring providers via `provider` + `baseUrl` + `model`.

If your local runtime exposes an OpenAI-compatible `/v1` API (vLLM, LM Studio, llama.cpp server, Ollama in
OpenAI-compatible mode, etc.), you can typically set:

- `provider: openai`
- `baseUrl: http://127.0.0.1:PORT/v1`
- `apiKey: anything` (many local servers ignore it)
- `model: <your-local-model-name>`

Note: advanced MemU provider features (like switching `client_backend`) are supported upstream, but this
plugin currently only maps the basic fields into `LLMConfig`.

## Upstream / updates

The MemU core is vendored into `python/src/memu/`.

- See `python/UPSTREAM.md` for the exact upstream reference and a patch list.
- `update_from_upstream.sh` is a helper for refreshing from upstream (best-effort; review changes after).

If you previously ran an older variant that used different SQLite table names, you may need to delete
`~/.openclaw/workspace/memU/data/memu.db` and let it rebuild.

## Portability note (native extension)

Upstream MemU ships a Rust-backed Python extension (`memu._core`). This repository currently vendors the
package under `python/src/memu/`.

This integration is only tested on Linux (Ubuntu) so far.

## Verify

After installation and configuration:

```bash
openclaw gateway restart
openclaw agent --message "Call the tool memory_search with query=\"xx\"." --thinking off
```

If the models are configured correctly, the first call will also start the background watcher and ingest
workspace docs.

## License

This project is released under the Apache License 2.0.

- See `LICENSE`
- See `NOTICE`

## Session File Processing Logic

This plugin processes OpenClaw session files in a phased, chronologically-ordered manner to ensure memory summaries always reflect the latest content.

### Initial Sync (Historical Data Import)

When you first install the plugin or reset the database:

```
Processing Order:
1. .deleted files (ended historical sessions) → sorted by session creation time, oldest first
2. .jsonl files (active/current sessions) → sorted by session creation time, oldest first
```

**Key Design Decisions**:
- Uses the `timestamp` field from session file headers for sorting (not file mtime)
- `.deleted` files are processed first, ensuring old data doesn't overwrite newer summaries
- Files copied from other machines (with changed mtime) will still sort correctly

**Steps to Migrate Old Conversations**:
1. Copy old `.jsonl` files to `~/.openclaw/agents/main/sessions/`
2. (Optional) Delete `~/.openclaw/workspace/memU/data/memu.db` to rebuild database
3. Restart Gateway and interact with the agent to trigger initial sync

### Real-time Incremental Sync

During normal use, the plugin watches active sessions and updates incrementally:

```
Detection Mechanisms:
- mtime: Quick check if file has changed
- offset: Tracks last read position, only reads new content
- head/tail SHA256: Detects if file was modified (vs appended)
- inode/device: Detects if file was replaced
```

**Incremental Processing Flow**:
```
File change → Check mtime
  ↓
mtime updated → Check offset and hash
  ↓
Append-only → Read from offset (new messages only)
Modified → Re-read entire file from beginning
```

**Filtering Rules**:
- By default, only syncs main sessions (UUID-format session IDs)
- Sub-agent sessions (like `verify-prepush`) are skipped
- Set `MEMU_SYNC_SUB_SESSIONS=true` to enable sub-session sync

### State Tracking

Sync state is saved in `~/.openclaw/workspace/memU/data/conversations/state.json`:

```json
{
  "sessions": {
    "<session-id>": {
      "last_offset": 12345,
      "part_count": 2,
      "head_sha256": "abc...",
      "tail_sha256": "def..."
    }
  },
  "processed_deleted": ["file1.jsonl.deleted.xxx", ...]
}
```
