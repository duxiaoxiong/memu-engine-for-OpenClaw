> ⚠️ **WARNING: Performance Issues**
>
> This project currently has severe performance bugs and is under repair. Please do NOT use it for now.
>
> **警告：暂时有性能 Bug**
>
> 本项目目前存在严重的性能问题，正在紧急修复中。请暂时不要使用。

# memU Engine for OpenClaw

Project Links:

- OpenClaw: https://github.com/openclaw/openclaw
- MemU (upstream): https://github.com/NevaMind-AI/MemU

Language:

- [Chinese (中文)](README.md)

## Introduction

`memu-engine` is an OpenClaw memory plugin designed to bring MemU's powerful atomic memory capabilities to OpenClaw.
It listens to OpenClaw's session logs and workspace documents, incrementally extracts key information (profiles, events, knowledge, skills, etc.), and stores them in a local SQLite database for instant retrieval by the agent.

> Core Advantage: MemU's memory extraction algorithm transforms unstructured conversations into high-quality structured data. See the [MemU official documentation](https://github.com/NevaMind-AI/MemU) for details.

## 🤖 Let OpenClaw Install Itself

Paste the following block and tell OpenClaw to install this plugin:

```text
Install and configure memu-engine by following the instructions here: https://github.com/duxiaoxiong/memu-engine-for-OpenClaw/blob/main/README_EN.md
```

## Manual Installation

### 1. Download Plugin

```bash
mkdir -p ~/.openclaw/extensions
cd ~/.openclaw/extensions
git clone https://github.com/duxiaoxiong/memu-engine-for-OpenClaw.git memu-engine
```

### 2. Configure OpenClaw

Edit `~/.openclaw/openclaw.json` and configure this plugin under the `plugins` section.

### 3. Restart and Activate

```bash
openclaw gateway restart
```

After restarting, just say "Call `memory_search`" to your agent. The background sync service will automatically start and begin the initial full sync.

## Configuration Details

Below is a complete configuration example with parameter explanations. It is recommended to configure in this order:

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
          "language": "zh",
          // 4. Ingest Configuration
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

### 1. `embedding` (Embedding Model)
Configures the model used for generating text vectors, which directly determines search accuracy.
*   **Recommended**: `text-embedding-3-small` (OpenAI) or `bge-m3` (local/SiliconFlow).
*   Supports all OpenAI-compatible interfaces.

### 2. `extraction` (Extraction Model)
Configures the LLM used for reading conversation logs and extracting memory items.
*   **Recommended**: Since it needs to process large amounts of chunked data, use **fast and cheap** models like `gpt-4o-mini` or `gemini-1.5-flash`.
*   **Note**: This model is primarily for classification and summarization; speed is more important than reasoning capability.

### 3. `language` (Output Language)
Specifies the language for generated memory summaries.
*   **Options**: `zh` (Chinese), `en` (English), `ja` (Japanese).
*   **Suggestion**: Set to the same language as your daily conversations to improve memory recognition rates.

### 4. `ingest` (Document Ingest)
Configures which additional Markdown documents to ingest besides session logs.

*   **`includeDefaultPaths`** (bool): Whether to include default workspace docs (`workspace/*.md` and `memory/*.md`). Default is `true`.
*   **`extraPaths`** (list): List of extra document sources.
    *   Supports file paths (must be `.md`).
    *   Supports directory paths (recursively scans all `*.md` files).
    *   **Limitation**: Currently restricted to Markdown format only.

---

## Local Model Support

If your local inference service (vLLM, Ollama, LM Studio, etc.) exposes an OpenAI-compatible `/v1` interface:

*   `provider`: `openai`
*   `baseUrl`: `http://127.0.0.1:PORT/v1`
*   `apiKey`: `your-api-key` (cannot be empty)
*   `model`: `<local-model-name>`

---

## Plugin Session Processing Logic

> This section explains how the plugin ensures chronological consistency of memories. For reference only.

To ensure memory summaries always reflect the latest content, the plugin uses a phased processing strategy:

1.  **History First**: Prioritizes processing `.deleted` files (rotated historical sessions), sorted chronologically by session creation time.
2.  **Active Follow-up**: Subsequently processes active `.jsonl` files, also sorted chronologically.
3.  **Incremental Update**: Uses `offset` and `hash` to detect file changes, reading only new messages to avoid wasted tokens.
4.  **Smart Filtering**: By default, only syncs main sessions (UUID), automatically ignoring sub-task sessions (unless `MEMU_SYNC_SUB_SESSIONS=true` is set).

Sync state is saved in `~/.openclaw/workspace/memU/data/conversations/state.json`.

## Disable and Uninstall

### Temporary Disable

Remove or comment out the `memu-engine` configuration in `openclaw.json`:

```jsonc
{
  "extensions": {
    // "memu-engine": { ... }  // Comment out to disable
  }
}
```

### Full Uninstall

1.  Disable the plugin in `openclaw.json`.
2.  Restart Gateway.
3.  (Optional) Delete data and plugin files:

```bash
rm -rf ~/.openclaw/extensions/memu-engine
rm -rf ~/.openclaw/memUdata  # Or your configured dataDir
```

## State Tracking

Sync state is stored in `{dataDir}/conversations/state.json`.

## License

Apache License 2.0
