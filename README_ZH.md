<!-- markdownlint-disable MD013 MD031 MD032 MD033 MD034 MD040 MD004 MD030 MD022 MD007 MD012 MD009 MD025 -->
# memU Engine for OpenClaw

项目链接：

- OpenClaw: https://github.com/openclaw/openclaw
- MemU（上游）: https://github.com/NevaMind-AI/MemU

语言：

- [English](README.md)

## v0.3.1 的变化

v0.3.1 把 `memu-engine` 从单库记忆插件改成了按代理拆分的记忆布局，同时把共享存储和检索规则明确下来。

| 维度 | v0.2.6 | v0.3.1 |
| --- | --- | --- |
| 代理记忆 | 单个 `memu.db` | 每个代理独立 `memory/<agent>/memu.db` |
| 共享文档 | 混在同一个库里 | 独立 `memory/shared/memu.db` |
| 跨代理检索 | 旧的粗粒度开关 | `searchableStores` 精确授权 |
| 运行时路径 | 历史路径较分散 | 统一到 `~/.openclaw/memUdata` |
| 升级方式 | 需要自己理解老布局 | 自动迁移，先备份再写入 |

### 对用户有什么变化

- **按代理隔离**：每个代理写自己的数据库，默认不串库。
- **共享规则明确**：跨代理检索由 `agentSettings.searchableStores` 控制。
- **路径更简单**：会话、资源、数据库、状态文件都放在同一个根目录下。
- **升级自动处理**：`v0.2.6` 的旧数据会自动迁移，并在写入前备份。

相关文档：

- **[MEMU_PARAMETERS.md](MEMU_PARAMETERS.md)**：完整参数、默认值、优先级。

## 这个插件做什么

`memu-engine` 会把 OpenClaw 的会话日志和工作区 Markdown 文档，转成可检索的结构化记忆。

- 从对话里提取画像、事件、知识、技能、行为偏好等信息。
- 用本地 SQLite/向量检索保存代理记忆。
- 把共享文档与代理私有记忆分开管理。
- 通过 OpenClaw 的 memory plugin slot 暴露给 Agent，直接调用 `memory_search` 即可。

底层使用 MemU 的提取链路。上游原理可参考 [MemU 官方项目](https://github.com/NevaMind-AI/MemU)。

## 安装（官方 OpenClaw 流程）

### 前置条件

- `uv` 在 `PATH` 中可用（插件会通过 `uv sync` 自动自举独立 Python 运行时）

> 首次运行自动自举：插件会在 `python/.venv` 下创建/复用独立环境，并按 `python/uv.lock` 安装锁定依赖。npm 安装与 GitHub 源码安装都走同一逻辑。

### 1. 安装插件

已发包版本：

```bash
openclaw plugins install memu-engine
```

本地开发联动（不拷贝）：

```bash
openclaw plugins install -l /你的/memu-engine/绝对路径
```

### 2. 配置 OpenClaw

编辑 `~/.openclaw/openclaw.json`，在 `plugins` 节点下配置本插件。

### 3. 重启并激活

```bash
openclaw gateway restart
```

重启后，只需对 Agent 说句 "调用 `memory_search`"，后台同步服务就会自动启动并开始首次全量同步。

## 路径结构

v0.3.1 把运行时数据统一放到一个根目录下面：

```text
~/.openclaw/memUdata/
├── conversations/          # 转换后的会话分片
├── resources/              # 文档录入后的资源产物
├── memory/
│   ├── shared/memu.db      # 共享文档库
│   ├── main/memu.db        # main 代理记忆库
│   └── <agent>/memu.db     # 其他代理的独立记忆库
└── state/                  # 同步状态与运行时标记
```

相比 `v0.2.6`，现在更容易按目录理解、备份、迁移和清理记忆，因为每一类数据都有固定位置。

如果你是从 v0.2.6 升级，插件会在启动时自动把旧的单库布局迁移到新的多代理布局，并在写入前保留备份。

## 快速上手配置

### 最小单代理配置

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
          "language": "zh"
        }
      }
    }
  }
}
```

### 最小多代理配置

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

上面这个例子的读取规则很简单：

- `main` 可以检索自己的记忆、共享文档库，以及 `research` 的记忆。
- `research` 只能检索自己的记忆和共享文档库。
- 两个代理仍然各自写入独立数据库。

## 配置详解

以下是完整配置示例及参数说明。如果你只是想先跑起来，建议先用上面的最小配置，再回到这里做细调。

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
          // 4. 混合存储根目录 (可选)
          "memoryRoot": "~/.openclaw/memUdata/memory",
          // 5. 文档录入配置
          "ingest": {
            "includeDefaultPaths": true,
            "extraPaths": [
              "/home/you/project/docs",
              "/home/you/project/README.md"
            ]
          },
          // 6. 检索行为配置
          "retrieval": {
            "mode": "fast",               // fast | full
            "contextMessages": 3,          // full 模式下注入最近消息数
            "defaultCategoryQuota": 3,     // 默认 category 条数
            "defaultItemQuota": 7,         // 默认 item 条数
            "outputMode": "compact"       // compact | full
          },
          // 7. 性能优化参数 (Immutable Parts)
          "flushIdleSeconds": 1800, // 30分钟无对话则固化分片
          "maxMessagesPerPart": 60,  // 满60条则固化分片
          // 8. 多代理内存控制
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
          // 9. 文档分片
          "chunkSize": 512,      // 1-2048, default 512
          "chunkOverlap": 50     // >=0, < chunkSize, default 50
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

**API Key 配置（推荐方式）：**

使用环境变量模板语法 - **最安全、最方便**的配置方式：

```jsonc
"embedding": {
  "provider": "openai",
  "baseUrl": "https://api.openai.com/v1",
  "apiKey": "${OPENAI_API_KEY}",  // 引用环境变量
  "model": "text-embedding-3-small"
}
```

**设置步骤（一次性）：**
```bash
# 添加到 shell 配置文件（永久生效）
echo 'export OPENAI_API_KEY="sk-your-actual-key"' >> ~/.bashrc
source ~/.bashrc

# 验证
echo $OPENAI_API_KEY
```

**为什么这样安全：**
- ✅ 配置文件只包含变量名 `"${OPENAI_API_KEY}"` - 可以安全提交到 git
- ✅ 实际的 API key 保存在环境变量中 - 不会暴露在配置文件中
- ✅ 不同环境（开发/生产）可以使用不同的密钥
- ✅ 符合 12-Factor App 最佳实践

**其他配置方式：**

<details>
<summary>点击展开：其他配置选项</summary>

**完整 SecretRef 对象**（等同于 `${VAR}` 语法）：
```jsonc
"apiKey": {
  "source": "env",
  "provider": "default",
  "id": "OPENAI_API_KEY"
}
```

**明文 API key**（不推荐，会显示安全警告）：
```jsonc
"apiKey": "sk-..."  // ⚠️ 不安全 - 提交到 git 可能泄露
```

**环境变量回退**（自动）：
如果未配置 `apiKey`，插件会自动尝试从 `MEMU_EMBED_API_KEY` 环境变量读取。

**注意：** 目前仅支持 `env` source。`file` 和 `exec` source 需要完整的 OpenClaw SDK 集成。

</details>

### 2. `extraction` (提取模型)
配置用于阅读对话日志并提取记忆条目的 LLM。
*   **推荐**：由于需要处理大量分片数据，建议使用**快速且廉价**的模型，如 `gpt-4o-mini` 或 `gemini-1.5-flash`。
*   **注意**：此模型主要负责分类和总结，速度比推理能力更重要。
*   **提示**：建议使用非推理（non-reasoning）模型，并确保仅返回严格 XML（不要输出思考过程或 Markdown）。

### 3. `language` (输出语言)
指定记忆摘要的生成语言。
*   **选项**：`zh` (中文), `en` (英文), `ja` (日文)。
*   **建议**：设置为与你日常对话相同的语言，有助于提高记忆识别率。

### 4. `memoryRoot`（混合存储）
定义插件在磁盘上的混合存储布局，统一管理每个代理的专属记忆与共享文档存储。

*   **默认**：`~/.openclaw/memUdata/memory`
*   **用途**：每个代理在 `{memoryRoot}/<agentName>/memu.db` 里写入自己的记忆，而 `{memoryRoot}/shared/memu.db` 则保存可被任意代理读取的文档与 chunk。
*   **高层结构示例**（名称仅示意）：
    ```
    {memoryRoot}/
    ├── shared/            # 共享文档与 chunk 数据
    │   └── memu.db
    ├── main/              # 默认代理记忆库
    │   └── memu.db
    └── <agentName>/       # 其他代理遵循相同模式
        └── memu.db
    ```
*   **迁移说明**：当遗留的单库布局（例如 `~/.openclaw/memUdata/memu.db`）仍存在时，`watch_sync.py` 或 `auto_sync.py` 会在启动阶段调用迁移逻辑，将数据移动到 `memoryRoot/main/memu.db`（或指定代理），同时保留带时间戳的备份。

### 5. `ingest` (文档录入)
配置除会话日志外，还需要录入哪些 Markdown 文档。

*   **`includeDefaultPaths`** (bool): 是否包含默认工作区文档（`workspace/*.md` 和 `memory/*.md`）。默认为 `true`。
*   **`extraPaths`** (list): 额外的文档来源列表。
    *   支持文件路径（必须是 `.md`）。
    *   支持目录路径（递归扫描目录下的所有 `*.md` 文件）。
    *   **限制**：目前仅限制 Markdown 格式。

#### 可选 TweetClaw 来源记忆工作流

[TweetClaw](https://github.com/Xquik-dev/tweetclaw) 是一个 OpenClaw 插件，适合处理
X/Twitter 自动化任务，例如搜索 tweets、搜索 tweet replies、用户查询、粉丝导出、
媒体下载、监控 tweets，以及需要人工确认的写入动作。

用 TweetClaw 做只读的公开 X/Twitter 研究后，将人工确认过的发现保存为 Markdown，
再通过 memu-engine 的 `ingest.extraPaths` 写入共享文档库。这样可以让 `memory_search`
检索到社交来源上下文，同时避免把这些外部来源混入某个代理的私有对话记忆。

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

完整示例见：[TweetClaw X/Twitter 来源记忆工作流](examples/tweetclaw-x-signal-memory.md)。

### 6. `retrieval`（检索行为）

用于控制 `memory_search` 的行为。

*   **`mode`**：`fast`（更快）或 `full`（启用 MemU 渐进判断链路）。
*   **`contextMessages`**：`full` 模式下注入的最近消息数量。
*   **`defaultCategoryQuota` / `defaultItemQuota`**：调用未显式传 quota 时的默认条数。
*   **`outputMode`**：`compact`（给模型的精简输出）或 `full`（完整 envelope）。

> 详细规则（默认值、优先级、可选项）见：**[MEMU_PARAMETERS.md](MEMU_PARAMETERS.md)**

### 7. 性能优化参数 (Immutable Parts)
本插件采用“不可变分片”策略来防止重复消耗 Token。

*   **`flushIdleSeconds`** (int): 默认 `1800` (30分钟)。如果一个会话闲置超过此时间，暂存的聊天尾巴 (`.tail.tmp`) 会被“固化”为永久分片并写入 MemU。
*   **`maxMessagesPerPart`** (int): 默认 `60`。如果聊天积攒满 60 条，也会强制固化。

### 8. `agentSettings`（多代理内存配置）

`agentSettings` 直接写在 `plugins.entries["memu-engine"].config` 中，用于为每个代理定义记忆写入与检索权限。运行时会为每个配置项应用默认策略，并确保 `main` 始终存在，即使配置里省略了它。

*   **`memoryEnabled`**（布尔，默认 `true`）：控制该代理是否写入/更新结构化记忆。设为 `false` 时，该代理的所有记忆 ingestion 都会被跳过。
*   **`searchEnabled`**（布尔，默认 `true`）：控制该代理是否允许调用 `memory_search`。设为 `false` 后，即使调用发生，运行时也不会执行搜索。
*   **`searchableStores`**（字符串数组，默认 `['self']`）：指定该代理向哪些存储发起检索。有效值包括 `self`（运行时自动替换为发起请求的代理）、`shared`（共享文档/块数据库）以及明确的其他代理名称。填入其他代理名即可按策略进行跨代理检索。

> 兼容说明：旧参数 `allowCrossAgentRetrieval` 已废弃（deprecated），仍可用但启动时会告警。请迁移到 `agentSettings.<agent>.searchableStores`：
> - `allowCrossAgentRetrieval=false` → `searchableStores: ["self"]`
> - `allowCrossAgentRetrieval=true` → `searchableStores: ["self", "shared"]`
> 当新旧配置同时存在时，以 `agentSettings` 为准。

保留 `self` 让同一份配置可以复用到不同代理；加入 `shared` 让该代理访问共享仓库；写入具体代理名可在授权场景下跨代理拉取记忆。每次 `memory_search` 调用都会带上 `agentName`（默认 `main`），且结果中仍会包含 `agentName`，帮助识别数据来源。

示例：开启 `trial` 的记忆能力，并让 `main` 可检索 `trial` + 共享文档，而 `trial` 仅检索自身 + 共享文档：

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

- `main`：可检索自身记忆、`trial` 记忆、`shared` 文档库。
- `trial`：仅可检索自身记忆与 `shared` 文档库。

### 9. 文档分块配置
控制文档如何被拆分为可检索的 parts。

```jsonc
{
  "chunkSize": 512,      // 1-2048, default 512
  "chunkOverlap": 50     // >=0, < chunkSize, default 50
}
```

**参数说明**：
*   `chunkSize`：每个 chunk 的最大字符数（1-2048）。
*   `chunkOverlap`：相邻 chunk 之间的重叠字符数，必须小于 chunkSize。

**建议**：默认值（chunkSize=512, chunkOverlap=50）适用于大多数文档。需要更长上下文可适当增大 chunkSize，需要更明显的分割可减少 chunkOverlap。

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

1.  **按代理会话处理**：运行时基于代理上下文处理会话并按 `agentName` 写入，支持多代理记忆隔离与检索策略。
2.  **去噪**：移除 `NO_REPLY`、`System:` 提示、Tool Calls 等非正常对话内容。
3.  **脱敏**：移除 `message_id`、Telegram ID 等元数据，只保留纯文本内容。

### 隐私安全
所有数据存储在本地 SQLite (`memu.db`) 中。
*   没有数据会被发送到云端（除非你配置了云端 LLM）。
*   你可以随时备份或删除 `~/.openclaw/memUdata` 目录来重置记忆。

</details>

---

## 故障排查

### 官方记忆系统冲突（Official Memory System Conflict）

如果同时启用了 `agents.defaults.memorySearch.enabled=true` 与 `plugins.slots.memory="memu-engine"`，就会出现 OpenClaw 官方记忆与 memu-engine 并行工作的情况。
两个记忆系统同时生效可能导致检索行为混乱。

**推荐修复方式**：保留 memu-engine 作为唯一记忆后端，并关闭官方 memory search。

`openclaw.json` 精确修改如下：

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


## 禁用与回退

### 临时禁用
在 `openclaw.json` 中移除或注释掉 `memu-engine` 配置。

### 完全卸载
1. 删除插件目录：`rm -rf ~/.openclaw/extensions/memu-engine`
2. 删除数据：`rm -rf ~/.openclaw/memUdata`
3. 重启 OpenClaw。

## 许可证
Apache License 2.0
