# memU Engine for OpenClaw

Project Links:

- OpenClaw: https://github.com/openclaw/openclaw
- MemU (upstream): https://github.com/NevaMind-AI/MemU

Language:

- [Chinese (中文)](README_ZH.md)

## Introduction

`memu-engine` is an OpenClaw memory plugin designed to bring MemU's powerful atomic memory capabilities to OpenClaw.
It listens to OpenClaw's session logs and workspace documents, incrementally extracts key information (profiles, events, knowledge, skills, etc.), and stores them in a local SQLite database for instant retrieval by the agent.

> Core Advantage: MemU's memory extraction algorithm transforms unstructured conversations into high-quality structured data. See the [MemU official documentation](https://github.com/NevaMind-AI/MemU) for details.

## 🤖 Let OpenClaw Install Itself

Paste the following block and tell OpenClaw to install this plugin:

```text
Install and configure memu-engine by following the instructions here: https://github.com/duxiaoxiong/memu-engine-for-OpenClaw/blob/main/README.md
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
          "language": "en",
          // 4. Data Directory (Optional)
          "dataDir": "~/.openclaw/memUdata",
          // 5. Ingest Configuration
          "ingest": {
            "includeDefaultPaths": true,
            "extraPaths": [
              "/home/you/project/docs",
              "/home/you/project/README.md"
            ]
          },
          // 6. Performance Optimization (Immutable Parts)
          "flushIdleSeconds": 1800, // Flush part after 30 mins of inactivity
          "maxMessagesPerPart": 60  // Flush part after 60 messages
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

### 4. `dataDir` (Data Directory)
Specifies where memU database and conversation files are stored.
*   **Default**: `~/.openclaw/memUdata`
*   **Usage**: Chat logs are sensitive data; you can store them in an encrypted partition or custom location.
*   **Structure**:
    ```
    {dataDir}/
    ├── memu.db           # SQLite database
    ├── conversations/    # Conversation parts
    └── resources/        # Resource files
    ```

### 5. `ingest` (Document Ingest)
Configures which additional Markdown documents to ingest besides session logs.

*   **`includeDefaultPaths`** (bool): Whether to include default workspace docs (`workspace/*.md` and `memory/*.md`). Default is `true`.
*   **`extraPaths`** (list): List of extra document sources.
    *   Supports file paths (must be `.md`).
    *   Supports directory paths (recursively scans all `*.md` files).
    *   **Limitation**: Currently restricted to Markdown format only.

### 6. Performance Optimization (Immutable Parts)
This plugin uses an "Immutable Parts" strategy to prevent repeated token consumption.

*   **`flushIdleSeconds`** (int): Default `1800` (30 mins). If a session is idle for this long, the staged chat tail (`.tail.tmp`) is "frozen" into a permanent part and written to MemU.
*   **`maxMessagesPerPart`** (int): Default `60`. If chat accumulates 60 messages, it forces a freeze.

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

1.  **Main Session Locking**: Only locks main sessions via `sessions.json` ID; does not record sub-agent conversations.
2.  **De-noising**: Removes `NO_REPLY`, `System:` prompts, Tool Calls, and other non-normal conversation content.
3.  **Anonymization**: Removes `message_id`, Telegram IDs, and other metadata, keeping only plain text.

### Privacy
All data is stored in local SQLite (`memu.db`).
*   No data is sent to the cloud (unless you configure a cloud LLM).
*   You can reset memory at any time by deleting the `~/.openclaw/memUdata` directory.

</details>

---

## Disable and Uninstall

### Temporary Disable
Remove or comment out the `memu-engine` configuration in `openclaw.json`.

### Full Uninstall
1. Delete plugin directory: `rm -rf ~/.openclaw/extensions/memu-engine`
2. Delete data: `rm -rf ~/.openclaw/memUdata`
3. Restart OpenClaw.

## License
Apache License 2.0
