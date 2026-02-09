# memU Engine for OpenClaw (中文版)

项目链接：

- OpenClaw: https://github.com/openclaw/openclaw
- MemU（上游）: https://github.com/NevaMind-AI/MemU

语言：

- [English](README_EN.md)

## 这是什么

`memu-engine` 是一个社区维护的 OpenClaw 记忆插件，用来把 OpenClaw 的会话日志接入 MemU。
它提供 `memory_search` 和 `memory_get`，并在 OpenClaw 工作区中保存一个 SQLite 长期记忆库。

这不是 MemU 或 OpenClaw 官方项目，只是一个尽量贴近上游、偏工程化的集成尝试。

## 它做什么

- 监听 OpenClaw 的会话 `.jsonl` 文件，增量录入新消息。
- 用 MemU 抽取“原子化”的记忆条目（profile/event/knowledge/skill/tool 等）。
- 数据存储在 `~/.openclaw/workspace/memU/data/memu.db`（SQLite）。

另外它也支持录入额外的 Markdown 资料（例如：项目文档、扩展文档等），让记忆库可以引用真实文件内容。

## 安装

### 让 OpenClaw 自己安装（对 Agent 友好）

如果你使用的 OpenClaw Agent 具备操作本机的能力，通常直接让它阅读本 README 并按步骤安装即可。

可以发给 OpenClaw 的提示词：

```text
请从 https://github.com/duxiaoxiong/memu-engine-for-OpenClaw 安装 OpenClaw 插件 `memu-engine`。
```

### 手动安装

1) 从 git 下载

```bash
mkdir -p ~/src
cd ~/src
git clone https://github.com/duxiaoxiong/memu-engine-for-OpenClaw.git memu-engine
```

2) 复制到 OpenClaw 扩展目录

```bash
mkdir -p ~/.openclaw/extensions
rm -rf ~/.openclaw/extensions/memu-engine
cp -R ~/src/memu-engine ~/.openclaw/extensions/memu-engine
```

3) 先修改配置（再重启）

编辑 `~/.openclaw/openclaw.json`，设置 memory slot 和插件参数（示例见下方）。

4) 重启网关

```bash
openclaw gateway restart
```

如果你在修改 `openclaw.json` 之前就重启，OpenClaw 可能仍在使用旧的 memory slot，从而出现一些看起来
不相关的报错。

### 首次同步触发机制

当你初次安装（或重置数据库）后，记忆库是空的。插件采用"懒加载"策略：

1. 重启 Gateway **不会** 立即启动同步进程。
2. 当你（或 AI）第一次与插件交互（例如发送消息、调用 `memory_search`）时，后台同步服务才会启动。
3. 服务启动时，如果发现数据库为空或有新会话，会立即触发 **全量历史同步**。

因此，安装完成后，只需对你的 Agent 说句 "你好"，即可触发首次构建。

### 实时同步机制

后台服务启动后，会持续监听变化：

- **会话同步**：实时监听 `~/.openclaw/sessions/*.jsonl` 的新消息。
- **文档同步**：监听配置的 Markdown 文档路径。
- **防抖 (Debounce)**：变更处理有 5 秒的防抖时间，避免频繁触发。
- **高效处理**：仅处理时间戳发生变化的文件。如果文件未修改，不会调用 LLM。
- **进程锁**：使用文件锁防止多个进程同时同步（锁过期时间：15分钟）。

## 配置

在 `~/.openclaw/openclaw.json` 中绑定 memory slot，并填写模型参数。

插件会通过环境变量把配置传给 MemU：

- `embedding.*` -> `MEMU_EMBED_*`
- `extraction.*` -> `MEMU_CHAT_*`

示例（不要填真实 key）：

```json
{
  "plugins": {
    "slots": { "memory": "memu-engine" },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "embedding": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "model": "text-embedding-3-small"
          },
          "extraction": {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "model": "gpt-4o-mini"
          },
          "language": "zh"
        }
      }
    }
  }
}
```

> ⚠️ **注意**：如果 extraction 模型响应太慢，同步过程可能会超时并静默失败。

可选：

- `language`: 记忆摘要的输出语言（`zh`、`en`、`ja`）。不设置则使用默认行为（英文）。
- `ingest.extraPaths`: 额外录入的 Markdown 目录/文件列表
- `MEMU_USER_ID`: 覆盖默认 user id（默认：`default`）

### 输出语言

默认情况下，MemU 会用英文提取记忆摘要。对于中文用户，可以设置 `language` 为 `zh`：

```json
{
  "plugins": {
    "entries": {
      "memu-engine": {
        "config": {
          "language": "zh",
          "embedding": { ... },
          "extraction": { ... }
        }
      }
    }
  }
}
```

支持的语言：`zh`（中文）、`en`（英文）、`ja`（日文）。

### 录入额外 Markdown（文档）

默认情况下，本插件会录入 OpenClaw 常见的 Markdown 来源：

- `~/.openclaw/workspace/*.md`（例如：`AGENTS.md`、`MEMORY.md`）
- `~/.openclaw/workspace/memory/*.md`（长期记忆笔记）

你可以通过 `ingest.includeDefaultPaths=false` 关闭默认录入。

如果你配置了 `ingest.extraPaths`，后台 watcher 还会：

- 扫描这些目录/文件下的 `*.md`
- 录入到 MemU 的 SQLite 记忆库
- 文件变更时自动增量更新（带 debounce）

适用场景：

- 项目文档（`docs/`、`README.md` 等）
- OpenClaw 扩展文档（你提供任意目录路径即可）

示例：

```json
{
  "plugins": {
    "entries": {
      "memu-engine": {
        "config": {
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

### 读取记忆来源文件

`memory_get` 支持两种输入：

- 物理文件路径（直接从磁盘读取）
- MemU 的资源 id/URL（形如 `memu://<id>`）

`memory_search` 的输出里会带 `Source:`，把这个值原样传给 `memory_get` 就能读到完整内容。

## 本地模型支持

MemU 支持通过 `provider` + `baseUrl` + `model` 配置不同模型服务。

如果你的本地推理服务暴露了 OpenAI 兼容的 `/v1` 接口（例如 vLLM、LM Studio、llama.cpp server、
或者启用 OpenAI 兼容模式的 Ollama），通常可以这样配置：

- `provider: openai`
- `baseUrl: http://127.0.0.1:PORT/v1`
- `apiKey: 随便填`（很多本地服务不会校验）
- `model: <本地模型名称>`

提示：MemU 上游还支持更高级的 provider/client backend 选项，但本插件目前只映射最常用的基础字段。

## 上游 / 更新

MemU 核心代码被 vendoring 到 `python/src/memu/`。

- `python/UPSTREAM.md` 记录了上游版本与本项目的补丁列表。
- `update_from_upstream.sh` 是一个同步脚本（尽力而为；同步后建议手动 review）。


## 可移植性说明（原生扩展）

MemU 上游包含一个 Rust 编写的 Python 原生扩展（`memu._core`）。本仓库目前将 MemU 代码 vendoring
到 `python/src/memu/`。作者仅在linux（Ubuntu）进行了测试。


## 验证

安装并配置后：

```bash
openclaw gateway restart
openclaw agent --message "Call the tool memory_search with query=\"xx\"." --thinking off
```

如果模型参数配置正确，第一次调用也会拉起后台 watcher 并开始录入工作区文档。

## 许可证

本项目采用 Apache License 2.0 发布。

- 见 `LICENSE`
- 见 `NOTICE`

## 会话文件处理逻辑

本插件采用分阶段、按时间排序的方式处理 OpenClaw 会话文件，确保记忆摘要始终反映最新内容。

### 首次同步（历史数据导入）

当你首次安装插件或清空数据库重建时：

```
处理顺序：
1. .deleted 文件（已结束的历史会话）→ 按 session 创建时间，从旧到新
2. .jsonl 文件（活跃/当前会话）→ 按 session 创建时间，从旧到新
```

**关键设计**：
- 使用 session 文件头部的 `timestamp` 字段排序（不是文件的 mtime）
- `.deleted` 文件先处理，确保旧数据不会覆盖新数据的摘要
- 即使从其他机器复制过来的文件（mtime 会变），也能正确排序

**迁移旧对话的步骤**：
1. 将旧的 `.jsonl` 文件复制到 `~/.openclaw/agents/main/sessions/`
2. （可选）删除 `~/.openclaw/workspace/memU/data/memu.db` 重建数据库
3. 重启 Gateway，与 Agent 交互触发首次同步

### 实时增量同步

日常使用中，插件会监听活跃会话的变化并增量更新：

```
检测机制：
- mtime：快速判断文件是否有变化
- offset：记录上次读取的位置，只读取新增内容
- head/tail SHA256：检测文件是否被修改（而非追加）
- inode/device：检测文件是否被替换
```

**增量处理流程**：
```
文件变化 → 检查 mtime
  ↓
mtime 更新 → 检查 offset 和 hash
  ↓
仅追加 → 从 offset 继续读取新消息
被修改 → 从头重新读取整个文件
```

**过滤规则**：
- 默认只同步主会话（UUID 格式的 session ID）
- 子任务会话（如 `verify-prepush`）会被跳过
- 设置 `MEMU_SYNC_SUB_SESSIONS=true` 可开启子会话同步

### 状态跟踪

同步状态保存在 `~/.openclaw/workspace/memU/data/conversations/state.json`：

```json
{
  "sessions": {
    "<session-id>": {
      "last_offset": 12345,
      "part_count": 2,
      "head_sha256": "abc...",
      "tail_sha256": "def..."
    }
  },
  "processed_deleted": ["file1.jsonl.deleted.xxx", ...]
}
```
