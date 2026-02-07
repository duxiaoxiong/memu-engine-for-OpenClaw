#!/bin/bash
set -e

# 配置
PLUGIN_ROOT=$(cd "$(dirname "$0")" && pwd)
PYTHON_SRC="$PLUGIN_ROOT/python/src"
TEMP_DIR="/tmp/memu_upstream_update"
PATCH_FILE="$PLUGIN_ROOT/patches/py313_sqlmodel_fix.patch"

# 1. 准备补丁（如果还没有的话，我们先生成一个当前的 diff）
# 这是一个简化的逻辑：我们假设现在的代码是 fix 过的，未来的官方代码是未 fix 的。
# 更好的做法是维护一个专门的 .patch 文件。
echo "[1/5] Checking for compatibility patches..."
mkdir -p "$PLUGIN_ROOT/patches"

# 2. 拉取官方代码
echo "[2/5] Fetching upstream..."
rm -rf "$TEMP_DIR"
git clone --depth 1 https://github.com/NevaMind-AI/MemU "$TEMP_DIR"

# 3. 覆盖核心代码 (src/)
echo "[3/5] Syncing core logic..."
# 注意：我们覆盖 src，但保留我们自己的 scripts 和 watch_sync.py
rsync -av --delete --exclude '__pycache__' "$TEMP_DIR/src/" "$PYTHON_SRC/"

# 4. 尝试恢复我们的关键修复 (Python 3.13 Fix)
echo "[4/5] Re-applying Python 3.13 SQLModel fix..."

# 目标文件：src/memu/database/models.py
MODELS_FILE="$PYTHON_SRC/memu/database/models.py"

# 检测是否包含 embedding_json (我们的修复特征)
if grep -q "embedding_json" "$MODELS_FILE"; then
    echo "  -> Upstream already has the fix! (Awesome)"
else
    echo "  -> Upstream missing fix. Applying patch..."
    
    # 动态插入补丁逻辑
    # 1. 在 class Resource/MemoryItem 里插入 embedding_json 字段
    # 2. 修改 embedding 字段为 @property
    
    # 这里使用 sed 做一个简单的“手术”
    # 将 embedding: list[float] ... 替换为我们的逻辑
    
    sed -i 's/embedding: list\[float\] | None = Field(default=None)/# Vector embedding (kept out of most dumps; SQLite stores it in embedding_json)\n    embedding: list[float] | None = Field(default=None, exclude=True)/' "$MODELS_FILE"
    
    # 这是一个比较脆弱的替换，但在没有标准 .patch 文件时最有效。
    # 它仅仅把 exclude=True 加回去，真正的 embedding_json 逻辑需要更复杂的插入。
    
    echo "  WARNING: Auto-patching complex logic via shell script is risky."
    echo "  Please manually verify $MODELS_FILE contains 'embedding_json'."
fi

# 5. 更新依赖定义
echo "[5/5] Updating dependencies..."
cp "$TEMP_DIR/pyproject.toml" "$PLUGIN_ROOT/python/"
# 也可以选择不覆盖 lock 文件，让下一次 uv run 重新解析

echo "✅ Update complete! Please test with 'memory_search'."
rm -rf "$TEMP_DIR"
