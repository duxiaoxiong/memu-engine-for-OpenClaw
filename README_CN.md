# memU Engine for OpenClaw (中文版)

> **链接导航**：
> - **OpenClaw (Official)**: [https://github.com/openclaw/openclaw](https://github.com/openclaw/openclaw)
> - **MemU (Core Engine)**: [https://github.com/NevaMind-AI/MemU](https://github.com/NevaMind-AI/MemU)

---

## 💡 项目简介

**memU Engine** 是一个为 OpenClaw 设计的**增强型记忆后端插件**。

它不仅仅是简单的“文件搜索”，而是将 OpenClaw 的对话流实时接入 **memU 智能记忆引擎**，通过大模型自动提取对话中的**原子化知识**（如技能、事件、用户偏好、技术结论），并存入结构化的向量数据库。

该插件旨在**无缝替换** OpenClaw 默认的 `memory-core`（Markdown 检索），让你的 AI 助手拥有更精准、更具上下文关联能力的长期记忆。

---

## ✨ 核心优势

1.  **原子化知识提取 (Atomic Knowledge)**
    *   告别粗糙的“文本块切分”。memU 会自动分析对话，将其拆解为 `Profile`（画像）、`Event`（事件）、`Knowledge`（知识）、`Skill`（技能）等独立条目。
    *   **效果**：搜索“Docker配置”时，不再返回一堆闲聊废话，而是精准返回配置指令和相关决策。

2.  **实时事件驱动同步 (Real-time & Zero-Idle)**
    *   **传统方案**：定时轮询（费资源、有延迟）。
    *   **本方案**：采用 `watchdog` 监听文件系统。**你不说话，它不动（零 Token 消耗）；你一发消息，它秒级录入。**

3.  **Python 3.13 兼容性增强**
    *   内置了针对 SQLModel 在 Python 3.13 下 `list[float]` 映射问题的修复补丁，确保在最新环境下稳定运行。

4.  **无缝原生体验**
    *   **接口对齐**：完美实现 OpenClaw 官方定义的 `memory_search` 和 `memory_get` 接口。系统感觉不到底层的变化，但回忆质量大幅提升。
    *   **跨平台**：内置 Node.js 进程守护，支持 Linux/macOS/Windows，无需手动配置 systemd。

---

## 📦 安装与配置

### 1. 安装插件
进入你的 OpenClaw 扩展目录：
```bash
cd ~/.openclaw/extensions
git clone https://github.com/<你的用户名>/openclaw-memu-engine memu-engine
```

### 2. 配置 (`openclaw.json`)
在你的配置文件中启用插件，并填入 Embedding 服务商（如 SiliconFlow 或 OpenAI）的 Key。

```json
{
  "plugins": {
    // 关键：将记忆槽位指定为 memu-engine
    "slots": {
      "memory": "memu-engine"
    },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "embedding": {
            "provider": "openai", // 兼容 OpenAI 格式
            "apiKey": "sk-...",   // 你的 SiliconFlow 或 OpenAI Key
            "baseUrl": "https://api.siliconflow.cn/v1",
            "model": "BAAI/bge-m3"
          }
        }
      }
    }
  }
}
```

### 3. 重启生效
重启 OpenClaw Gateway，插件会自动拉起后台同步服务。

---

## 🛠️ 架构说明

*   **Plugin Layer (Node.js)**: 负责与 OpenClaw 交互，管理 Python 子进程生命周期。
*   **Sync Layer (Python)**:
    *   `watch_sync.py`: 文件监听哨兵。
    *   `auto_sync.py`: 增量同步核心，负责调用 LLM 提取记忆。
*   **Storage Layer**: 数据存储于用户工作区 `workspace/memU/data/memu.db` (SQLite)，确保数据主权。

---

## 🤝 贡献与开源
本项目遵循 MIT 协议。欢迎提交 PR 或 Issue。
如果你是 OpenClaw 官方开发者，欢迎评估将此架构合入官方 `extensions/` 仓库。
