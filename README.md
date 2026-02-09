# memU Engine for OpenClaw (中文版)

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
          }
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

---

## 本地模型支持

如果你的本地推理服务（vLLM, Ollama, LM Studio 等）暴露了 OpenAI 兼容的 `/v1` 接口：

*   `provider`: `openai`
*   `baseUrl`: `http://127.0.0.1:PORT/v1`
*   `apiKey`: `your-api-key` (不能为空)
*   `model`: `<本地模型名称>`

---

## 本插件的会话处理逻辑

> 本节介绍插件内部如何确保记忆的时序一致性，仅供参考。

为了确保记忆摘要始终反映最新内容，插件采用分阶段处理策略：

1.  **主会话识别**：通过 `sessions.json` 中的 `agent:main:main` 条目识别真正的用户主对话，忽略所有子代理会话和 `.deleted` 归档文件。
2.  **智能过滤**：自动过滤系统注入消息（NO_REPLY、工具调用、Model 切换通知等），只保留真正的用户对话。
3.  **增量更新**：利用 `offset` 和 `hash` 检测文件变化，只读取新增消息，避免重复消耗 Token。
4.  **路径优化**：搜索结果中的路径会自动缩短（如 `ws:docs/guide.md`、`conv:75fcef11:p0`），减少 AI 上下文占用。

同步状态保存在 `{dataDir}/conversations/state.json`。

## 禁用与回退

### 临时禁用

在 `openclaw.json` 中移除或注释掉 `memu-engine` 配置：

```jsonc
{
  "extensions": {
    // "memu-engine": { ... }  // 注释掉即可禁用
  }
}
```

### 完全卸载

1. 删除插件目录：
   ```bash
   rm -rf ~/.openclaw/extensions/memu-engine
   ```

2. （可选）删除记忆数据：
   ```bash
   rm -rf ~/.openclaw/memUdata
   ```

3. 重启 OpenClaw

### 回退到原生记忆

OpenClaw 原生的记忆功能会自动恢复，无需额外配置。禁用本插件后，原生的 `memory_search` 和 `memory_get` 工具将恢复使用。

## 许可证

Apache License 2.0
