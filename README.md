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
4.  **尾部分片（Tail）落盘策略**：为了避免“最后一个 part 未满时反复重写 → memU 反复全量记忆化”的问题，插件采用 **staging + finalize** 的策略：
    - 写入 `{dataDir}/conversations/{sessionId}.tail.tmp.json` 作为暂存尾巴（会持续更新，但不会触发记忆化）。
    - 只有满足 flush 条件时，才会把 tail 固化为不可变 part 文件并触发 memU 记忆化。
    - flush 条件（默认）：
      - tail 累积消息数达到 `MEMU_MAX_MESSAGES_PER_SESSION`（默认 60）
      - 或者会话在 `MEMU_FLUSH_IDLE_SECONDS`（默认 1800s=30min）内无新增消息
    - 这会显著降低功耗和无用重算。
4.  **路径优化**：搜索结果中的路径会自动缩短（如 `ws:docs/guide.md`、`conv:75fcef11:p0`），减少 AI 上下文占用。

同步状态保存在 `{dataDir}/conversations/state.json`。

### 与功耗相关的参数（建议保持默认）

- `MEMU_MAX_MESSAGES_PER_SESSION`：每个 finalized part 的消息数上限。
  - 默认：`60`
- `MEMU_FLUSH_IDLE_SECONDS`：无新增对话多长时间后，将 tail 强制 finalize。
  - 默认：`1800`（30 分钟）
- `MEMU_FLUSH_POLL_SECONDS`：watch 进程的低频 idle-check 周期。
  - 默认：`60`
  - 说明：只有当主会话 `.jsonl` 的 mtime 已经 idle ≥ `MEMU_FLUSH_IDLE_SECONDS` 时，才会触发一次 `auto_sync`，避免无谓唤醒。

### 手动归档 / Freeze（推荐在 compact / 长对话结束后调用）

插件提供工具：
- `memory_flush`
- `memu_flush`（别名）

用途：
- 将当前暂存的 tail（`{dataDir}/conversations/{sessionId}.tail.tmp.json`）强制固化为不可变 part 文件
- 并立即触发 `auto_sync` 进入 memU 记忆录入

你可以直接在对话里要求助手：
> “请在归档/compact 后调用 memory_flush，把尾巴固化并写入记忆。”

#### 关于 watcher 常驻与并发（重要）

- `watch_sync.py` 是**常驻进程**：负责监听会话/文档变更，并在需要时触发一次性同步。
- `auto_sync.py` 是**一次性进程**：只在 watcher 或工具触发时运行，运行完成后退出。

为了避免并发导致重复 ingestion / 额外功耗：
- `auto_sync.py` 内部带有一个轻量锁（位于系统临时目录，例如 `/tmp/memu_sync.lock_auto_sync`）。
- 当 watcher 已经触发并正在运行 `auto_sync.py` 时，如果你此刻再手动调用 `memory_flush`，新的 `auto_sync` 会检测到锁并**直接跳过**（打印 `auto_sync already running; skip`）。

这不会影响数据一致性：
- 你可以在当前同步结束后，再次调用 `memory_flush`。
- 或者等待下一次 watcher 触发（例如会话有新消息/到达 idle flush 条件）。

### 可选：Compaction 后自动触发 Flush

OpenClaw 本身在接近自动 compaction 时会触发官方的 “memory flush” 提示回合。

如果你希望在 **compaction 完成后** 自动把当前 tail 固化并写入 memU，可以在插件配置中启用：

```json5
{
  plugins: {
    entries: {
      "memu-engine": {
        config: {
          flushOnCompaction: true
        }
      }
    }
  }
}
```

说明：
- 该功能通过 OpenClaw 的 `after_compaction` hook 实现。
- 默认关闭（避免意外增加写入频率）。

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

---

## 2026-02-10 变更总结（今晚做了什么）

下面这部分是为了方便你复查/再编辑。

### 1) 会话解析与过滤（OpenClaw sessions → memU conversations）

- **主会话识别修正**：不再用“UUID 文件名”判断主会话；改为读取 `sessions.json["agent:main:main"].sessionId`。
- **跳过无关会话**：默认跳过 `.deleted.*` 归档文件、cron 会话、以及其他子 agent 会话。
- **过滤噪音内容**：忽略 toolCall/toolResult/thinking/image 等块；过滤系统注入/指令回执类消息（如 `Model set to...`、`Thinking level...`、`System: ...`、`NO_REPLY` 等）。
- **文本清洗**：移除 `message_id` 等元信息；清理 Telegram 前缀（保留时间）。

### 2) 分片策略重做（降低反复记忆化的功耗）

- **每个 part 默认 60 条**：`MEMU_MAX_MESSAGES_PER_SESSION` 默认从 120 调整为 60。
- **Tail staging + Finalize**：新增 `{sessionId}.tail.tmp.json` 作为暂存尾巴，尾巴在未 flush 前不会触发 memU ingest。
- **Idle flush**：默认 `MEMU_FLUSH_IDLE_SECONDS=1800`（30 分钟）无新对话则强制 finalize tail。
- **低功耗轮询**：`watch_sync.py` 增加 `MEMU_FLUSH_POLL_SECONDS=60` 的轻量 idle-check，仅当主会话确实 idle 到阈值才触发一次 `auto_sync`。

### 3) 手动归档工具（AI 可直接触发）

- 新增工具：`memory_flush` / `memu_flush`。
- 用途：**强制 finalize 当前 tail 并立刻触发 auto_sync → memU memorize**。

### 4) 可选：compaction 后自动 flush

- 新增配置 `flushOnCompaction: true`（默认 false）。
- 若 OpenClaw 运行时暴露 `after_compaction` hook，则 compaction 结束后自动调用一次 flush。

### 5) 数据目录可配置（隐私/敏感数据）

- 新增 `dataDir` 配置项，默认改为 `~/.openclaw/memUdata`。
- 优先级：`pluginConfig.dataDir` > `MEMU_DATA_DIR` 环境变量 > 默认路径。

### 6) 路径显示压缩（减少上下文占用）

- `search.py` 输出中对路径做缩短（`ws:` / `extN:` / `conv:`），同时 `get.py` 支持反向展开，确保 `memory_get` 可用。

### 7) 自动启动同步

- Gateway 启动时自动启动 `watch_sync`，避免“意外关闭后不再同步，必须手动触发工具才恢复”。
