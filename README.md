# memU Engine for OpenClaw

Links:

- OpenClaw: https://github.com/openclaw/openclaw
- MemU (upstream): https://github.com/NevaMind-AI/MemU

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
Please install the OpenClaw plugin `memu-engine` from https://github.com/duxiaoxiong/memu-engine-for-OpenClaw.
```

### Manual install

1) Download from git

```bash
mkdir -p ~/src
cd ~/src
git clone https://github.com/<you>/<repo>.git memu-engine
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
          }
        }
      }
    }
  }
}
```

Optional:

- `ingest.extraPaths`: list of directories/files to ingest Markdown from.
- `MEMU_USER_ID`: override the default user id (default: `default`).

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
openclaw agent --message "Call the tool memory_search with query=\"test\"." --thinking off
```

If the models are configured correctly, the first call will also start the background watcher and ingest
workspace docs.

## License

This project is released under the Apache License 2.0.

- See `LICENSE`
- See `NOTICE`
