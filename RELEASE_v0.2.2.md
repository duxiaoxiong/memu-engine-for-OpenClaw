v0.2.2 这次更新聚焦检索性能，核心目标是让 `memu_search` 更快且结果不变。

## 本次更新
- 优化 `recall_items`：移除重复全量扫描，`list_items` 调用从 2 次降到 1 次。
- 优化 `route_category`：新增 category summary embedding 缓存（按 summary 内容自动失效）。
- 优化 SQLite 向量解析：为 `embedding_json` 反序列化增加缓存，降低重复 `json.loads` 开销。

## 验证结果（同数据集对比）
- `retrieve` 中位耗时：约下降 42.9%
- `route_category` 中位耗时：约下降 87.8%
- `recall_items` 中位耗时：约下降 38.9%
- 检索结果签名一致（结果不变）
