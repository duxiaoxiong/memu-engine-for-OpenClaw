# memU Engine for OpenClaw

项目链接：

- OpenClaw: https://github.com/openclaw/openclaw
- MemU（上游）: https://github.com/NevaMind-AI/MemU

语言：

- [English](README_EN.md)

## 简介

`memu-engine` 是一个 OpenClaw 记忆插件，旨在将 MemU 强大的原子化记忆能力带给 OpenClaw。
它监听 OpenClaw 的会话日志和工作区文档，增量提取关键信息（画像、事件、知识、技能等），并存储在本地 SQLite 数据库中，供 Agent 随时检索。

> 核心优势：MemU 的记忆提取算法能将非结构化对话转化为高质量的结构化数据。详见 [MemU 官方文档](https://github.com/NevaMind-AI/MemU)。

## 🤖 让小爪子自己给你装

粘贴下面这一段并告诉openclaw来安装本插件：

```text
Install and configure oh-my-opencode by following the instructions here: https://github.com/duxiaoxiong/memu-engine-for-OpenClaw/blob/main/README.md
```

## 手动安装

### 1. 下载插件

```bash
mkdir -p ~/.openclaw/extensions
cd ~/.openclaw/extensions
git clone https://github.com/duxiaoxiong/memu-engine-for-OpenClaw.git memu-engine
```

### 2. 配置 OpenClaw

编辑 `~/.openclaw/openclaw.json`，在 `plugins` 节点下配置本插件。

### 3. 重启并激活

```bash
openclaw gateway restart
```

重启后，只需对 Agent 说句 "调用 `memory_search`"，后台同步服务就会自动启动并开始首次全量同步。

## 配置详解

以下是完整配置示例及参数说明。建议按此结构顺序进行配置：

```jsonc
{
  "plugins": {
    "slots": { "memory": "memu-engine" },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          // 1. 向量嵌入模型 (用于搜索)
          "embedding": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "model": "text-embedding-3-small"
          },
          // 2. 记忆提取模型 (用于生成摘要)
          "extraction": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "model": "gpt-4o-mini"
          },
          // 3. 输出语言
          "language": "zh",
          // 4. 数据存储目录 (可选)
          "dataDir": "~/.openclaw/memUdata",
          // 5. 文档录入配置
          "ingest": {
            "includeDefaultPaths": true,
            "extraPaths": [
              "/home/you/project/docs",
              "/home/you/project/README.md"
            ]
          },
          // 6. 性能优化参数 (Immutable Parts)
          "flushIdleSeconds": 1800, // 30分钟无对话则固化分片
          "maxMessagesPerPart": 60  // 满60条则固化分片
        }
      }
    }
  }
}
```

### 1. `embedding` (向量模型)
配置用于生成文本向量的模型，直接决定搜索的准确性。
*   **推荐**：`text-embedding-3-small` (OpenAI) 或 `bge-m3` (本地/SiliconFlow)。
*   支持所有 OpenAI 兼容接口。

### 2. `extraction` (提取模型)
配置用于阅读对话日志并提取记忆条目的 LLM。
*   **推荐**：由于需要处理大量分片数据，建议使用**快速且廉价**的模型，如 `gpt-4o-mini` 或 `gemini-1.5-flash`。
*   **注意**：此模型主要负责分类和总结，速度比推理能力更重要。

### 3. `language` (输出语言)
指定记忆摘要的生成语言。
*   **选项**：`zh` (中文), `en` (英文), `ja` (日文)。
*   **建议**：设置为与你日常对话相同的语言，有助于提高记忆识别率。

### 4. `dataDir` (数据目录)
指定 memU 数据库和对话文件的存储位置。
*   **默认**：`~/.openclaw/memUdata`
*   **用途**：聊天记录属于敏感数据，你可以将其存储在加密分区或自定义位置。
*   **目录结构**：
    ```
    {dataDir}/
    ├── memu.db           # SQLite 数据库
    ├── conversations/    # 对话分片
    └── resources/        # 资源文件
    ```

### 5. `ingest` (文档录入)
配置除会话日志外，还需要录入哪些 Markdown 文档。

*   **`includeDefaultPaths`** (bool): 是否包含默认工作区文档（`workspace/*.md` 和 `memory/*.md`）。默认为 `true`。
*   **`extraPaths`** (list): 额外的文档来源列表。
    *   支持文件路径（必须是 `.md`）。
    *   支持目录路径（递归扫描目录下的所有 `*.md` 文件）。
    *   **限制**：目前仅限制 Markdown 格式。

### 6. 性能优化参数 (Immutable Parts)
本插件采用“不可变分片”策略来防止重复消耗 Token。

*   **`flushIdleSeconds`** (int): 默认 `1800` (30分钟)。如果一个会话闲置超过此时间，暂存的聊天尾巴 (`.tail.tmp`) 会被“固化”为永久分片并写入 MemU。
*   **`maxMessagesPerPart`** (int): 默认 `60`。如果聊天积攒满 60 条，也会强制固化。

---

## 本地模型支持

如果你的本地推理服务（vLLM, Ollama, LM Studio 等）暴露了 OpenAI 兼容的 `/v1` 接口：

*   `provider`: `openai`
*   `baseUrl`: `http://127.0.0.1:PORT/v1`
*   `apiKey`: `your-api-key` (不能为空)
*   `model`: `<本地模型名称>`

---

## 技术原理

<details>
<summary>点击展开：插件对话存入逻辑</summary>


1.  **Tail Staging (尾部暂存)**：
    *   你的最新聊天内容首先被写入一个 **临时文件**：`{sessionId}.tail.tmp.json`。
    *   **MemU 会完全忽略这个文件**。因此，无论你聊得多欢，MemU 都不会被触发，消耗为 0。

2.  **Commit & Finalize (提交与固化)**：
    *   只有当满足 **Commit 条件**时（满 60 条消息，或闲置 30 分钟），脚本才会把这个 `.tmp` 文件**重命名**为正式的 `partNNN.json`。

3.  **One-Time Ingestion (一次性消费)**：
    *   memu-engine 发现新出现的 `partNNN.json`。
    *   它读取一次、分析一次、存入数据库。
    *   因为这个分片已经“满”了，它永远不会再被修改。memu-engine 以后再也不用看它了。


</details>

<details>
<summary>点击展开：会话内容清洗</summary>

### 会话清洗 (Sanitization)
在送入 LLM 之前，插件会对原始日志进行深度清洗：

1.  **主会话锁定**：只通过 `sessions.json` 的 ID 锁定主会话，不录取子agents对话。
2.  **去噪**：移除 `NO_REPLY`、`System:` 提示、Tool Calls 等非正常对话内容。
3.  **脱敏**：移除 `message_id`、Telegram ID 等元数据，只保留纯文本内容。

### 隐私安全
所有数据存储在本地 SQLite (`memu.db`) 中。
*   没有数据会被发送到云端（除非你配置了云端 LLM）。
*   你可以随时备份或删除 `~/.openclaw/memUdata` 目录来重置记忆。

</details>

---


## 禁用与回退

### 临时禁用
在 `openclaw.json` 中移除或注释掉 `memu-engine` 配置。

### 完全卸载
1. 删除插件目录：`rm -rf ~/.openclaw/extensions/memu-engine`
2. 删除数据：`rm -rf ~/.openclaw/memUdata`
3. 重启 OpenClaw。

## 许可证
Apache License 2.0
