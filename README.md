# memU Engine for OpenClaw

> **Links**:
> - **OpenClaw (Official)**: [https://github.com/openclaw/openclaw](https://github.com/openclaw/openclaw)
> - **MemU (Core Engine)**: [https://github.com/NevaMind-AI/MemU](https://github.com/NevaMind-AI/MemU)

---

## 💡 Introduction

**memU Engine** is an enhanced **memory backend plugin** for OpenClaw.

Instead of simple file search, it connects OpenClaw's conversation stream to the **memU intelligent memory engine**. It uses LLMs to automatically extract **atomic knowledge** (skills, events, user preferences, technical conclusions) from conversations and stores them in a structured vector database.

This plugin is designed to **seamlessly replace** OpenClaw's default `memory-core` (Markdown search), giving your AI assistant more precise and context-aware long-term memory.

---

## ✨ Key Features

1.  **Atomic Knowledge Extraction**
    *   Say goodbye to rough text chunking. memU automatically analyzes conversations and breaks them down into independent items like `Profile`, `Event`, `Knowledge`, `Skill`, etc.
    *   **Effect**: Searching for "Docker config" returns precise configuration commands and decisions, not just chat noise.

2.  **Real-time Event-Driven Sync (Zero-Idle)**
    *   **Traditional**: Polling (resource-intensive, high latency).
    *   **This Solution**: Uses `watchdog` to monitor the filesystem. **It does nothing when you're silent (zero token usage); it ingests instantly when you send a message.**

3.  **Python 3.13 Compatibility**
    *   Includes built-in patches for SQLModel `list[float]` mapping issues on Python 3.13+, ensuring stability in modern environments.

4.  **Seamless Native Experience**
    *   **Interface Aligned**: Perfectly implements the official `memory_search` and `memory_get` interfaces. The system feels unchanged, but recall quality is drastically improved.
    *   **Cross-Platform**: Built-in Node.js process management supports Linux/macOS/Windows without manual systemd configuration.

---

## 📦 Installation & Configuration

### 1. Install Plugin
Navigate to your OpenClaw extensions directory:
```bash
cd ~/.openclaw/extensions
git clone https://github.com/<your-username>/openclaw-memu-engine memu-engine
```

### 2. Configure (`openclaw.json`)
Enable the plugin in your config file and provide an Embedding Provider Key (e.g., SiliconFlow or OpenAI).

```json
{
  "plugins": {
    // Key: Assign the memory slot to memu-engine
    "slots": {
      "memory": "memu-engine"
    },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "embedding": {
            "provider": "openai", // OpenAI compatible
            "apiKey": "sk-...",   // Your SiliconFlow or OpenAI Key
            "baseUrl": "https://api.siliconflow.cn/v1",
            "model": "BAAI/bge-m3"
          }
        }
      }
    }
  }
}
```

### 3. Restart
Restart the OpenClaw Gateway. The plugin will automatically launch the background sync service.

---

## 🛠️ Architecture

*   **Plugin Layer (Node.js)**: Handles interaction with OpenClaw and manages the Python subprocess lifecycle.
*   **Sync Layer (Python)**:
    *   `watch_sync.py`: Filesystem watchdog sentinel.
    *   `auto_sync.py`: Incremental sync core, handles LLM memory extraction.
*   **Storage Layer**: Data is stored in the user workspace at `workspace/memU/data/memu.db` (SQLite) to ensure data sovereignty.

---

## 🤝 Contribution
This project is MIT licensed. PRs and Issues are welcome.
If you are an OpenClaw official developer, feel free to evaluate merging this architecture into the official `extensions/` repo.
