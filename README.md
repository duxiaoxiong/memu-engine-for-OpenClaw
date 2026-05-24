<!-- markdownlint-disable MD013 MD031 MD032 MD033 MD034 MD040 MD004 MD030 MD022 MD007 MD012 MD009 MD025 -->
# memU Engine for OpenClaw

[![Tests](https://github.com/duxiaoxiong/memu-engine-for-OpenClaw/actions/workflows/test.yml/badge.svg)](https://github.com/duxiaoxiong/memu-engine-for-OpenClaw/actions/workflows/test.yml)

Project Links:

- OpenClaw: https://github.com/openclaw/openclaw
- MemU (upstream): https://github.com/NevaMind-AI/MemU

Language:

- [Chinese (中文)](README_ZH.md)

## Changes in v0.3.1

v0.3.1 changes `memu-engine` from a single-store memory plugin into a per-agent memory layout with explicit shared storage and retrieval rules.

| Area | v0.2.6 | v0.3.1 |
| --- | --- | --- |
| Agent memory | Single `memu.db` | One DB per agent: `memory/<agent>/memu.db` |
| Shared docs | Mixed into the same store | Dedicated shared store: `memory/shared/memu.db` |
| Cross-agent retrieval | Legacy coarse switch | Explicit per-agent `searchableStores` policy |
| Runtime layout | Legacy scattered paths | Unified `~/.openclaw/memUdata` |
| Upgrade path | Manual reasoning required | Auto migration with backup-first behavior |

### What changed for users

- **Per-agent memory**: each agent writes to its own DB, so memory is isolated by default.
- **Explicit sharing**: cross-agent retrieval is controlled by `agentSettings.searchableStores`.
- **Simpler paths**: conversations, resources, memory DBs, and state files live under one root.
- **Automatic upgrade**: old `v0.2.6` data is migrated to the new layout with backup-first behavior.

Useful docs:

- **[MEMU_PARAMETERS.md](MEMU_PARAMETERS.md)**: parameter defaults, optional fields, and precedence.

## Overview

`memu-engine` turns OpenClaw session logs and workspace Markdown into structured, retrievable memory.

- It extracts profiles, events, knowledge, skills, and behavior signals from conversations.
- It stores agent memory locally in SQLite/vector-backed MemU databases.
- It keeps shared documents separate from agent-private memory.
- It exposes the result through OpenClaw's memory plugin slot, so agents can call `memory_search` directly.

It is built on top of MemU's extraction pipeline. See the [MemU upstream project](https://github.com/NevaMind-AI/MemU) for the model and method details.

## Install (Official OpenClaw Flow)

### Prerequisites

- `uv` available in `PATH` (the plugin auto-bootstraps an isolated Python runtime via `uv sync`)

> First run auto-bootstrap: the plugin will create/use an isolated environment under `python/.venv` and install locked dependencies from `python/uv.lock`. This applies to both npm install and GitHub source install.

### 1. Install plugin

Published package:

```bash
openclaw plugins install memu-engine
```

Local dev link:

```bash
openclaw plugins install -l /abs/path/to/memu-engine
```

### 2. Configure OpenClaw

Edit `~/.openclaw/openclaw.json` under `plugins`.

### 3. Restart and activate

```bash
openclaw gateway restart
```

After restarting, just say "Call `memory_search`" to your agent. The background sync service will automatically start and begin the initial full sync.

## Storage Layout

v0.3.1 stores runtime data under one root:

```text
~/.openclaw/memUdata/
├── conversations/          # converted session parts
├── resources/              # ingested document artifacts
├── memory/
│   ├── shared/memu.db      # shared document store
│   ├── main/memu.db        # main agent memory
│   └── <agent>/memu.db     # other agent memories
└── state/                  # sync state and bookkeeping
```

Compared with `v0.2.6`, the layout is easier to inspect, back up, migrate, and clean because each part now has a fixed directory.

If you are upgrading from v0.2.6, the plugin automatically migrates the legacy single-DB layout into the new per-agent layout and keeps a backup before writing.

## Quick Start Configuration

### Minimal single-agent setup

```jsonc
{
  "agents": {
    "defaults": {
      "memorySearch": {
        "enabled": false
      }
    }
  },
  "plugins": {
    "slots": { "memory": "memu-engine" },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "embedding": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "${OPENAI_API_KEY}",
            "model": "text-embedding-3-small"
          },
          "extraction": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "${OPENAI_API_KEY}",
            "model": "gpt-4o-mini"
          },
          "language": "en"
        }
      }
    }
  }
}
```

### Minimal multi-agent setup

```jsonc
{
  "plugins": {
    "slots": { "memory": "memu-engine" },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "memoryRoot": "~/.openclaw/memUdata/memory",
          "agentSettings": {
            "main": {
              "memoryEnabled": true,
              "searchEnabled": true,
              "searchableStores": ["self", "shared", "research"]
            },
            "research": {
              "memoryEnabled": true,
              "searchEnabled": true,
              "searchableStores": ["self", "shared"]
            }
          }
        }
      }
    }
  }
}
```

Reading rule for the example above:

- `main` can search its own memory, the shared doc store, and `research` memory.
- `research` can search only its own memory plus shared docs.
- Both agents still write into separate DBs.

## Configuration Details

Below is a full configuration example with parameter explanations. If you only want a working setup, use the quick-start example above first and return here for tuning.

```jsonc
{
  "plugins": {
    "slots": { "memory": "memu-engine" },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          // 1. Embedding Model (for search)
          "embedding": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "model": "text-embedding-3-small"
          },
          // 2. Extraction Model (for summarization)
          "extraction": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "model": "gpt-4o-mini"
          },
          // 3. Output Language
          "language": "en",
          // 4. Memory Root (Optional hybrid storage)
          "memoryRoot": "~/.openclaw/memUdata/memory",
          // 5. Ingest Configuration
          "ingest": {
            "includeDefaultPaths": true,
            "extraPaths": [
              "/home/you/project/docs",
              "/home/you/project/README.md"
            ]
          },
          // 6. Retrieval Behavior
          "retrieval": {
            "mode": "fast",               // fast | full
            "contextMessages": 3,          // used in full mode
            "defaultCategoryQuota": 3,     // default category results
            "defaultItemQuota": 7,         // default item results
            "outputMode": "compact"       // compact | full
          },
          // 7. Performance Optimization (Immutable Parts)
          "flushIdleSeconds": 1800, // Flush part after 30 mins of inactivity
          "maxMessagesPerPart": 60,  // Flush part after 60 messages
          // 8. Multi-agent Memory Policy
          "agentSettings": {
            "main": {
              "memoryEnabled": true,
              "searchEnabled": true,
              "searchableStores": ["self", "shared", "trial"]
            },
            "trial": {
              "memoryEnabled": true,
              "searchEnabled": true,
              "searchableStores": ["self", "shared"]
            }
          },
          // 9. Document Chunking
          "chunkSize": 512,      // 1-2048, default 512
          "chunkOverlap": 50     // >=0, < chunkSize, default 50
        }
      }
    }
  }
}
```

### 1. `embedding` (Embedding Model)
Configures the model used for generating text vectors, which directly determines search accuracy.
*   **Recommended**: `text-embedding-3-small` (OpenAI) or `bge-m3` (local/SiliconFlow).
*   Supports all OpenAI-compatible interfaces.

**API Key Configuration (Recommended):**

Use environment variable template syntax - the **safest and most convenient** way:

```jsonc
"embedding": {
  "provider": "openai",
  "baseUrl": "https://api.openai.com/v1",
  "apiKey": "${OPENAI_API_KEY}",  // References environment variable
  "model": "text-embedding-3-small"
}
```

**Setup (one-time):**
```bash
# Add to your shell config (permanent)
echo 'export OPENAI_API_KEY="sk-your-actual-key"' >> ~/.bashrc
source ~/.bashrc

# Verify
echo $OPENAI_API_KEY
```

**Why this is secure:**
- ✅ Config file only contains variable name `"${OPENAI_API_KEY}"` - safe to commit to git
- ✅ Actual API key stays in environment variable - never exposed in config files
- ✅ Different environments (dev/prod) can use different keys
- ✅ Follows 12-Factor App best practices

**Alternative Configuration Methods:**

<details>
<summary>Click to expand: Other configuration options</summary>

**Full SecretRef Object** (equivalent to `${VAR}` syntax):
```jsonc
"apiKey": {
  "source": "env",
  "provider": "default",
  "id": "OPENAI_API_KEY"
}
```

**Plain Text** (not recommended, shows security warning):
```jsonc
"apiKey": "sk-..."  // ⚠️ Insecure - may leak if committed to git
```

**Environment Variable Fallback** (automatic):
If no `apiKey` is configured, the plugin automatically reads from `MEMU_EMBED_API_KEY` environment variable.

**Note:** Currently only `env` source is supported. `file` and `exec` sources require full OpenClaw SDK integration.

</details>

### 2. `extraction` (Extraction Model)
Configures the LLM used for reading conversation logs and extracting memory items.
*   **Recommended**: Since it needs to process large amounts of chunked data, use **fast and cheap** models like `gpt-4o-mini` or `gemini-1.5-flash`.
*   **Note**: This model is primarily for classification and summarization; speed is more important than reasoning capability.
*   **Tip**: Prefer non-reasoning models that return strict XML-only output (no thinking text or Markdown).

### 3. `language` (Output Language)
Specifies the language for generated memory summaries.
*   **Options**: `zh` (Chinese), `en` (English), `ja` (Japanese).
*   **Suggestion**: Set to the same language as your daily conversations to improve memory recognition rates.

### 4. `memoryRoot` (Hybrid storage layout)
Defines the root directory where the runtime keeps all agent-specific memories alongside a shared document store.
*   **Default**: `~/.openclaw/memUdata/memory`
*   **Usage**: Each configured agent gets its own subdirectory containing `memu.db`, while a `shared/` subdirectory hosts the document database and chunks that all agents can reference.
*   **High-level structure** (names are illustrative, not enforced filenames):
    ```
    {memoryRoot}/
    ├── shared/            # Shared document store (documents + chunks)
    │   └── memu.db
    ├── main/              # Agent-specific memory store
    │   └── memu.db
    └── <agentName>/       # Additional agents use the same pattern
        └── memu.db
    ```
*   **Migration**: On startup the plugin checks for the legacy single-DB layout (e.g., `~/.openclaw/memUdata/memu.db`). If it exists, `watch_sync.py`/`auto_sync.py` move data into `memoryRoot/<agent>/memu.db` (defaulting to `main`) and keep a backup of the legacy file.

### 5. `ingest` (Document Ingest)
Configures which additional Markdown documents to ingest besides session logs.

*   **`includeDefaultPaths`** (bool): Whether to include default workspace docs (`workspace/*.md` and `memory/*.md`). Default is `true`.
*   **`extraPaths`** (list): List of extra document sources.
    *   Supports file paths (must be `.md`).
    *   Supports directory paths (recursively scans all `*.md` files).
    *   **Limitation**: Currently restricted to Markdown format only.

#### Optional TweetClaw source memory workflow

[TweetClaw](https://github.com/Xquik-dev/tweetclaw) is an OpenClaw plugin for
X/Twitter automation tasks such as search tweets, search tweet replies, user lookup,
follower export, media download, monitor tweets, and approval-gated write actions.

Use TweetClaw for read-only public X/Twitter research, then save reviewed findings as
Markdown under a directory that memu-engine ingests into the shared document store. This
keeps social source context available through `memory_search` without mixing it into a
single agent's private chat history.

```bash
openclaw plugins install @xquik/tweetclaw
```

```jsonc
"ingest": {
  "extraPaths": ["~/workspace/memory/x-twitter-signals"]
},
"agentSettings": {
  "main": {
    "memoryEnabled": true,
    "searchEnabled": true,
    "searchableStores": ["self", "shared"]
  },
  "research": {
    "memoryEnabled": true,
    "searchEnabled": true,
    "searchableStores": ["self", "shared"]
  }
}
```

Full recipe: [TweetClaw X/Twitter source memory workflow](examples/tweetclaw-x-signal-memory.md).

### 6. `retrieval` (Search behavior)

Controls how `memory_search` behaves.

*   **`mode`**: `fast` (lower latency) or `full` (MemU progressive decision path).
*   **`contextMessages`**: how many recent chat messages to inject in `full` mode.
*   **`defaultCategoryQuota` / `defaultItemQuota`**: default category/item counts when tool call does not pass quotas.
*   **`outputMode`**: `compact` (model-facing minimal fields) or `full` (full envelope with score/model metadata).

> Full details, defaults, and precedence rules: **[MEMU_PARAMETERS.md](MEMU_PARAMETERS.md)**

### 6.1 Agent settings

`agentSettings` replaces the old `enabledAgents`/`allowCrossAgentRetrieval` knobs and lets you define per-agent policies directly in `openclaw.json`.

*   **`memoryEnabled`** (bool, default `true`): enable writing and updating structured memory for this agent.
*   **`searchEnabled`** (bool, default `true`): allow the agent to issue `memory_search` calls.
*   **`searchableStores`** (array of `self`, `shared`, or explicit agent names, default `['self']`): controls which agent stores are searchable when this agent runs `memory_search`. `self` is automatically replaced with the requesting agent name.

If a caller omits `agentName` when invoking `memory_search`, the runtime assumes `main` and still respects the agent's `agentSettings` entry. Search results always include `agentName`, so you can tell which agent produced each memory record, even if retrieval spans multiple stores.

Set `agentSettings` under `plugins.entries["memu-engine"].config` to match your workspace agents and trust boundaries. The runtime also ensures `main` is always enabled, even if it is not declared.

Example: enable `trial` memory, let `main` search `trial` + shared docs, and keep `trial` restricted to self + shared docs:

```jsonc
"agentSettings": {
  "main": {
    "memoryEnabled": true,
    "searchEnabled": true,
    "searchableStores": ["self", "shared", "trial"]
  },
  "trial": {
    "memoryEnabled": true,
    "searchEnabled": true,
    "searchableStores": ["self", "shared"]
  }
}
```

Behavior for this example:
- `main`: can search own memory + `trial` memory + `shared` document store.
- `trial`: can search own memory + `shared` document store only.

### 7. Performance Optimization (Immutable Parts)
This plugin uses an "Immutable Parts" strategy to prevent repeated token consumption.

*   **`flushIdleSeconds`** (int): Default `1800` (30 mins). If a session is idle for this long, the staged chat tail (`.tail.tmp`) is "frozen" into a permanent part and written to MemU.
*   **`maxMessagesPerPart`** (int): Default `60`. If chat accumulates 60 messages, it forces a freeze.

### 8. Document Chunking
Controls how ingestion divides documents into retrievable parts.

```jsonc
{
  "chunkSize": 512,      // 1-2048, default 512
  "chunkOverlap": 50     // >=0, < chunkSize, default 50
}
```

**Parameters**:
*   `chunkSize`: Maximum characters per chunk (1-2048)
*   `chunkOverlap`: Overlap between consecutive chunks (must be < chunkSize)

**Recommendation**: The defaults (chunkSize=512, chunkOverlap=50) suit most documents. Increase chunkSize for longer context windows or reduce chunkOverlap when you want more distinct splits.

---

## Local Model Support

If your local inference service (vLLM, Ollama, LM Studio, etc.) exposes an OpenAI-compatible `/v1` interface:

*   `provider`: `openai`
*   `baseUrl`: `http://127.0.0.1:PORT/v1`
*   `apiKey`: `your-api-key` (cannot be empty)
*   `model`: `<local-model-name>`

---

## Technical Principles

<details>
<summary>Click to expand: Plugin Conversation Ingestion Logic</summary>

1.  **Tail Staging**:
    *   Your latest chat content is first written to a **temporary file**: `{sessionId}.tail.tmp.json`.
    *   **MemU completely ignores this file**. So no matter how much you chat, MemU is not triggered, costing 0 tokens.

2.  **Commit & Finalize**:
    *   Only when **Commit conditions** are met (60 messages or 30 mins idle), the script **renames** the `.tmp` file to a formal `partNNN.json`.

3.  **One-Time Ingestion**:
    *   memu-engine detects the new `partNNN.json`.
    *   It reads once, analyzes once, and stores in the database.
    *   Since this part is "full", it will never be modified again. memu-engine never needs to read it again.

</details>

<details>
<summary>Click to expand: Session Content Cleaning</summary>

### Session Sanitization
Before sending to LLM, the plugin deeply cleans raw logs:

1.  **Agent-Scoped Session Locking**: Tracks sessions per configured agent policy (`agentSettings`) and writes memory with `agentName` scope.
2.  **De-noising**: Removes `NO_REPLY`, `System:` prompts, Tool Calls, and other non-normal conversation content.
3.  **Anonymization**: Removes `message_id`, Telegram IDs, and other metadata, keeping only plain text.

### Privacy
All data is stored in local SQLite (`memu.db`).
*   No data is sent to the cloud (unless you configure a cloud LLM).
*   You can reset memory at any time by deleting the `~/.openclaw/memUdata` directory.

</details>

---

## Troubleshooting

### Official Memory System Conflict

If `agents.defaults.memorySearch.enabled=true` and `plugins.slots.memory="memu-engine"` are both active, OpenClaw official memory and memu-engine run at the same time.
This dual-memory setup can cause confusing retrieval behavior.

**Recommended fix**: keep memu-engine as the only memory backend and disable official memory search.

Exact `openclaw.json` change:

```jsonc
{
  "agents": {
    "defaults": {
      "memorySearch": {
        "enabled": false
      }
    }
  },
  "plugins": {
    "slots": {
      "memory": "memu-engine"
    }
  }
}
```

## Disable and Uninstall

### Temporary Disable
Remove or comment out the `memu-engine` configuration in `openclaw.json`.

### Full Uninstall
1. Delete plugin directory: `rm -rf ~/.openclaw/extensions/memu-engine`
2. Delete data: `rm -rf ~/.openclaw/memUdata`
3. Restart OpenClaw.

## License
Apache License 2.0
