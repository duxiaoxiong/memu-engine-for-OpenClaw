# memU Memory Engine for OpenClaw

An agentic memory backend for OpenClaw that replaces the default Markdown-based memory with a structured, vector-native database (memU).

## Features

- **Atomic Knowledge Extraction**: Extracts `profile`, `event`, `knowledge`, `skill`, `behavior` from conversations instead of raw text chunks.
- **Real-time Ingestion**: Watches OpenClaw session logs (`.jsonl`) and ingests new messages instantly (event-driven).
- **SQLModel + Vector**: Stores structured metadata in SQLite and semantic vectors via SiliconFlow/OpenAI.
- **Python 3.13 Compatible**: Includes fixes for SQLModel array mapping issues.

## Installation

1. Clone this repository into your extensions directory:
   ```bash
   cd ~/.openclaw/extensions
   git clone <your-repo-url> memu-memory
   ```

2. Configure via `~/.openclaw/openclaw.json` or the Web UI:
   ```json
   {
     "plugins": {
       "slots": {
         "memory": "memu-engine"
       },
       "entries": {
         "memu-engine": {
           "enabled": true,
           "config": {
             "embedding": {
               "provider": "siliconflow",
               "apiKey": "sk-...",
               "model": "BAAI/bge-m3"
             }
           }
         }
       }
     }
   }
   ```

## Requirements

- Python 3.11+
- `uv` package manager (the plugin tries to auto-use it)

## Architecture

- **Plugin (Node.js)**: Manages lifecycle, registers tools (`memory_search`, `memory_get`), and spawns the background watcher.
- **Backend (Python)**:
  - `watch_sync.py`: File watcher using `watchdog`.
  - `auto_sync.py`: Incremental ingestion logic.
  - `memu/`: Core logic library.

## License

MIT
