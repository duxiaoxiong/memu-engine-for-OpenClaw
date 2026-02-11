# memu-engine Parameters & Defaults

This document summarizes all configurable parameters currently supported by the `memu-engine` plugin, including:

- `openclaw.json` plugin config fields
- tool call parameters (`memory_search`, `memory_get`)
- default values
- whether each field is optional
- precedence/override rules

> After editing `~/.openclaw/openclaw.json`, run:
>
> `openclaw gateway restart`

---

## 1) `openclaw.json` configuration (plugin-level)

Location:

```json
{
  "plugins": {
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "...": "..."
        }
      }
    }
  }
}
```

---

### 1.1 `config.language`

- **Type**: `string`
- **Optional**: Yes
- **Default**: `"auto"`
- **Meaning**: output language for memory summaries (e.g. `"zh"`, `"en"`, or `"auto"`).

---

### 1.2 `config.embedding`

- **Type**: `object`
- **Optional**: Yes (but required in practice for working retrieval)
- **Default behavior** if fields missing:
  - `provider`: `"openai"`
  - `baseUrl`: `"https://api.openai.com/v1"`
  - `model`: `"text-embedding-3-small"`
  - `apiKey`: `""` (empty; usually causes failure if not set elsewhere)

Fields:

- `embedding.provider`
- `embedding.apiKey`
- `embedding.baseUrl`
- `embedding.model`

Used for vector embedding/retrieval.

---

### 1.3 `config.extraction`

- **Type**: `object`
- **Optional**: Yes (but required in practice for LLM-based extraction/full mode)
- **Default behavior** if fields missing:
  - `provider`: `"openai"`
  - `baseUrl`: `"https://api.openai.com/v1"`
  - `model`: `"gpt-4o-mini"`
  - `apiKey`: `""` (empty; usually causes failure if not set elsewhere)

Fields:

- `extraction.provider`
- `extraction.apiKey`
- `extraction.baseUrl`
- `extraction.model`

Used by memory extraction and full-mode decision checks.

---

### 1.4 `config.ingest`

- **Type**: `object`
- **Optional**: Yes

#### `ingest.includeDefaultPaths`

- **Type**: `boolean`
- **Optional**: Yes
- **Default**: `true`
- **Meaning**: include default workspace markdown paths for ingestion.

#### `ingest.extraPaths`

- **Type**: `string[]`
- **Optional**: Yes
- **Default**: `[]`
- **Meaning**: additional directories/files to ingest.

---

### 1.5 `config.retrieval`

- **Type**: `object`
- **Optional**: Yes

#### `retrieval.mode`

- **Type**: `"fast" | "full"`
- **Optional**: Yes
- **Default**: `"fast"`
- **Meaning**:
  - `fast`: vector-focused retrieval (no route-intention/sufficiency checks)
  - `full`: memU progressive retrieval with route-intention and sufficiency checks

#### `retrieval.contextMessages`

- **Type**: `integer`
- **Optional**: Yes
- **Default**: `3`
- **Valid range**: clamped to `0..20`
- **Meaning**: in `full` mode, number of recent session messages injected as retrieval context.

#### `retrieval.defaultCategoryQuota`

- **Type**: `integer`
- **Optional**: Yes
- **Default**: not set (`null` internally)
- **Meaning**: default number of category results for `memory_search` when call does not pass `categoryQuota`.

#### `retrieval.defaultItemQuota`

- **Type**: `integer`
- **Optional**: Yes
- **Default**: not set (`null` internally)
- **Meaning**: default number of item results for `memory_search` when call does not pass `itemQuota`.

#### `retrieval.outputMode`

- **Type**: `"compact" | "full"`
- **Optional**: Yes
- **Default**: `"compact"`
- **Meaning**:
  - `compact`: tool `content` only returns minimal `results[{path,snippet}]` for lower model token usage.
  - `full`: tool `content` returns full JSON envelope with `score/provider/model/fallback/citations`.

Note: debug metadata is still available in tool `details`.

---

### 1.6 Additional supported config keys (runtime-supported)

These are supported by plugin runtime logic, even if not strictly listed in `openclaw.plugin.json` schema.

#### `config.userId`

- **Type**: `string`
- **Optional**: Yes
- **Default**: `"default"` (or env fallback)
- **Meaning**: user namespace for memory isolation.

#### `config.dataDir`

- **Type**: `string`
- **Optional**: Yes
- **Default**: `~/.openclaw/memUdata`
- **Meaning**: memU data directory (`memu.db`, resources, pid, etc.).

#### `config.flushOnCompaction`

- **Type**: `boolean`
- **Optional**: Yes
- **Default**: `false`
- **Meaning**: if true, registers compaction hook to run `memory_flush` behavior.

---

## 2) Tool parameters (call-level)

## 2.1 `memory_search` / `memu_search`

Input:

- `query` (**required**, `string`)
- `maxResults` (optional, `integer`, default: `10`)
- `minScore` (optional, `number`, default: `0.0`)
- `categoryQuota` (optional, `integer`)
- `itemQuota` (optional, `integer`)

Output: JSON envelope

```json
{
  "results": [
    {
      "path": "memu://...",
      "startLine": 1,
      "endLine": 1,
      "score": 0.73,
      "snippet": "...",
      "source": "memory"
    }
  ],
  "provider": "...",
  "model": "...",
  "fallback": null,
  "citations": "off"
}
```

Execution details also include debug fields (`mode`, `contextCount`, etc.).

`retrieval.outputMode` controls what is placed in tool `content`:

- `compact` => `{ "results": [{"path","snippet"}, ...] }`
- `full` => full envelope with score/model/provider metadata

---

## 2.2 `memory_get` / `memu_get`

Input:

- `path` (**required**, `string`)
- `from` (optional, `integer`, **1-based**, default: `1`)
- `lines` (optional, `integer`, default: all remaining lines)

Output:

```json
{
  "path": "...",
  "text": "..."
}
```

Back-compat in script still accepts `--offset/--limit` internally.

---

## 3) Quota precedence rules

For category/item counts in `memory_search`, precedence is:

1. **Call-level args**: `categoryQuota`, `itemQuota`
2. **Plugin defaults**: `retrieval.defaultCategoryQuota`, `retrieval.defaultItemQuota`
3. **Auto strategy** (built-in fallback):
   - `maxResults >= 10`: category ~3 (or 4 when very large), rest item
   - smaller result sets: proportionally fewer categories

If explicit quotas exceed `maxResults`, quotas are scaled down to fit.

---

## 4) Fast vs Full mode behavior

- `fast`: lower latency, retrieval-focused.
- `full`: richer reasoning path (more LLM decision steps), usually slower.

`categoryQuota/itemQuota` apply in **both** modes, because they are output-assembly controls after retrieval.

---

## 5) Recommended starter config

```json
{
  "plugins": {
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "language": "zh",
          "embedding": {
            "provider": "openai",
            "apiKey": "YOUR_EMBED_KEY",
            "baseUrl": "https://api.siliconflow.cn/v1",
            "model": "BAAI/bge-m3"
          },
          "extraction": {
            "provider": "openai",
            "apiKey": "YOUR_CHAT_KEY",
            "baseUrl": "https://your-chat-endpoint/v1",
            "model": "your-chat-model"
          },
          "ingest": {
            "includeDefaultPaths": true,
            "extraPaths": []
          },
          "retrieval": {
            "mode": "fast",
            "contextMessages": 3,
            "defaultCategoryQuota": 3,
            "defaultItemQuota": 7
          }
        }
      }
    }
  }
}
```
