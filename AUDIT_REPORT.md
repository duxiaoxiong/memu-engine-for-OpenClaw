# memU Engine 技术审计报告

> 生成时间：2026-02-09
> 目标受众：代码审核人员、测试工程师
> 版本：v1.0 (Post-Refactor)

## 1. 架构概览

`memu-engine` 是一个通过监听 OpenClaw 会话文件并将数据同步到 MemU 长期记忆库的插件。

核心组件：
1.  **Session Converter (`convert_sessions.py`)**: 负责读取 `.jsonl` 会话文件，处理增量更新，识别历史会话(`.deleted`)，并将其转换为 MemU 可识别的格式。
2.  **Sync Service (`auto_sync.py`)**: 主服务进程，负责调用 Converter，并将转换后的数据推送到 MemU Core 进行记忆提取。
3.  **Watcher (`watch_sync.py`)**: 文件系统监听器，监控会话目录和文档目录的变化，触发同步任务。

---

## 2. 核心逻辑审计

### 2.1 会话文件处理流程 (CRITICAL)

**逻辑文件**: `python/convert_sessions.py`

为确保记忆摘要的时序正确性，插件采用了**分阶段处理**策略。

#### Phase 1: 历史数据处理 (`.deleted` 文件)
*   **目标**: 处理 OpenClaw 轮转或删除的历史会话文件。
*   **排序规则**: 按照 **Session Start Time** (从文件头部的 `timestamp` 字段提取) 进行升序排序 (Oldest First)。**注意：不使用文件名中的删除时间，也不使用文件 mtime。**
*   **处理逻辑**:
    *   检查 `state.json` 中的 `processed_deleted` 列表。
    *   如果已处理，跳过。
    *   如果未处理，读取内容，生成临时分片文件。
    *   处理完成后，将文件名加入 `processed_deleted`，防止重复处理。
    *   **关键点**: 此阶段优先执行，确保旧的记忆先被录入，建立基础上下文。

#### Phase 2: 活跃数据处理 (`.jsonl` 文件)
*   **目标**: 处理当前正在进行的活跃会话。
*   **排序规则**: 同样按照 **Session Start Time** 升序排序。
*   **增量更新机制**:
    *   **State Tracking**: 在 `state.json` 中记录每个 Session 的 `last_offset`, `part_count`, `head_sha256`, `tail_sha256`, `inode`。
    *   **Fast Path**: 检查文件 `mtime`。如果 `mtime <= last_sync_ts` 且文件大小未变，跳过。
    *   **Append Detection**:
        *   检查文件大小：如果变小，视为截断 (Truncated)，触发全量重读。
        *   检查 Head/Tail Hash：如果哈希不匹配，视为被修改 (Modified)，触发全量重读。
        *   检查 Inode：如果 Inode 变化，视为文件被替换 (Replaced)，触发全量重读。
    *   **Incremental Read**: 如果确认是 Append-only，仅从 `last_offset` 开始读取新字节。

### 2.2 状态管理

**状态文件**: `~/.openclaw/workspace/memU/data/conversations/state.json`

```json
{
  "version": 1,
  "sessions": {
    "uuid-session-id": {
      "file_path": "/path/to/session.jsonl",
      "device": 2050,
      "inode": 123456,
      "last_offset": 5000,      // 上次读取的字节位置
      "last_size": 5000,
      "part_count": 3,          // 已生成的记忆分片数量
      "head_sha256": "sha...",  // 文件头 hash (用于检测修改)
      "tail_sha256": "sha..."   // 上次读取末尾 hash (用于检测追加)
    }
  },
  "processed_deleted": [
    "session-id.jsonl.deleted.2026-02-01..."
  ]
}
```

### 2.3 实时同步与锁机制

**逻辑文件**: `python/watch_sync.py`

*   **Debounce (防抖)**: 文件变化触发后，等待 5 秒。如果 5 秒内有新变化，重置计时器。防止频繁触发 LLM 调用。
*   **Process Lock (进程锁)**: 使用文件锁 (`/tmp/memu_sync.lock`) 确保同一时间只有一个同步进程在运行。
*   **Stale Lock Recovery**: 如果锁文件存在超过 15 分钟，会被自动清除 (假设进程崩溃)。

### 2.4 子会话过滤

*   **默认行为**: 仅同步 UUID 格式的主会话 ID (Regex: `^[0-9a-f]{8}-...$`)。
*   **例外**: 如果设置环境变量 `MEMU_SYNC_SUB_SESSIONS=true`，则同步所有 `.jsonl` 文件（包括 `verify-prepush` 等子任务会话）。

---

## 3. 配置项清单

以下环境变量可用于调整插件行为 (通常在 `openclaw.json` 的 `env` 字段或系统环境变量中设置)：

| 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `MEMU_DATA_DIR` | `~/.openclaw/workspace/memU/data` | MemU 数据存储目录 (SQLite DB, Logs) |
| `OPENCLAW_SESSIONS_DIR` | `~/.openclaw/agents/main/sessions` | OpenClaw 会话文件目录 |
| `MEMU_SYNC_SUB_SESSIONS` | `false` | 是否同步非主会话 (如子任务 session) |
| `MEMU_MAX_MESSAGES_PER_SESSION` | `120` | 每个记忆分片的最大消息数 |
| `MEMU_LANGUAGE` | `en` | 记忆提取的目标语言 (`zh`, `en`, `ja`) |
| `MEMU_EMBED_PROVIDER` | `openai` | Embedding 模型提供商 |
| `MEMU_CHAT_PROVIDER` | `openai` | 记忆提取模型提供商 |

---

## 4. 验证与测试指南

### 4.1 自动化测试

项目包含一个针对 `.deleted` 文件处理逻辑的自动化测试脚本。

**运行方法**:
```bash
cd ~/.openclaw/extensions/memu-engine/python
# 确保已安装依赖或激活 venv
python3 test_deleted_processing.py
```

**测试覆盖点**:
*   [x] 正则表达式匹配 (UUID, Deleted Timestamp)
*   [x] 无状态的初始导入
*   [x] 防止覆盖已存在的分片 (`part_count` 检查)
*   [x] 跳过已处理的 `.deleted` 文件
*   [x] 子会话过滤逻辑
*   [x] 时间顺序排序逻辑

### 4.2 手动验证流程 (For QA)

**场景 A: 历史数据导入**
1.  停止 OpenClaw Gateway。
2.  清空 `MEMU_DATA_DIR` 下的 `memu.db` 和 `conversations/state.json`。
3.  将一批旧的 `.jsonl` 和 `.deleted` 文件复制到 Session 目录。
4.  启动 Gateway。
5.  **验证**: 查看 `sync.log`，确认 `.deleted` 文件先于 `.jsonl` 文件处理，且顺序符合 Session 创建时间。

**场景 B: 实时对话同步**
1.  与 Agent 进行一段多轮对话。
2.  **验证**: 观察 `sync.log`，确认触发了增量同步，且 `state.json` 中的 `last_offset` 增加。
3.  使用 `memory_search` 查询刚才对话中的信息，验证记忆已生成。

**场景 C: 子会话过滤**
1.  手动创建一个名为 `verify-test.jsonl` 的文件。
2.  **验证**: 确认 `sync.log` 中**没有**处理该文件的记录。
3.  设置 `MEMU_SYNC_SUB_SESSIONS=true` 并重启 Gateway。
4.  **验证**: 确认该文件现在被处理。

---

## 5. 待解决/已知限制

1.  **文件重命名**: 如果活跃 Session 文件被重命名（非 OpenClaw 标准轮转），可能会被视为新 Session 重新导入。
2.  **并发写入**: 虽然有进程锁，但如果多个 OpenClaw 实例指向同一个 MemU 数据目录，可能会有竞争风险（SQLite 自身有锁，但业务逻辑可能冲突）。建议每个 Agent 使用独立的 MemU 实例或通过 User ID 隔离。
