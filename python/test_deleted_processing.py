#!/usr/bin/env python3
"""
验证 .deleted 文件处理逻辑的测试脚本
"""
import os
import sys
import tempfile
import json
import shutil

# 设置环境变量
TEST_DIR = tempfile.mkdtemp(prefix="memu_test_")
SESSIONS_DIR = os.path.join(TEST_DIR, "sessions")
DATA_DIR = os.path.join(TEST_DIR, "data")
CONV_DIR = os.path.join(DATA_DIR, "conversations")

os.makedirs(SESSIONS_DIR)
os.makedirs(CONV_DIR)

os.environ["OPENCLAW_SESSIONS_DIR"] = SESSIONS_DIR
os.environ["MEMU_DATA_DIR"] = DATA_DIR

# 现在导入模块
from convert_sessions import convert, _extract_session_id, _load_state, _save_state, SESSION_FILENAME_RE


def create_openclaw_jsonl(file_path: str, messages: list[tuple[str, str]]) -> None:
    """创建符合 OpenClaw 格式的 JSONL 文件"""
    with open(file_path, "w") as f:
        # 写入 session header
        f.write(json.dumps({
            "type": "session",
            "version": 3,
            "id": "test-session",
            "timestamp": "2026-02-09T10:00:00.000Z"
        }) + "\n")
        
        # 写入消息
        for i, (role, content) in enumerate(messages):
            msg = {
                "type": "message",
                "id": f"msg-{i}",
                "parentId": None,
                "timestamp": f"2026-02-09T10:0{i}:00.000Z",
                "message": {
                    "role": role,
                    "content": [{"type": "text", "text": content}]
                }
            }
            f.write(json.dumps(msg) + "\n")


def test_regex_matching():
    """测试1: 正则表达式能正确匹配各种文件名格式"""
    print("\n=== 测试1: 正则表达式匹配 ===")
    
    test_cases = [
        ("session-123.jsonl", "session-123"),
        ("abc-def-ghi.jsonl", "abc-def-ghi"),
        ("test.jsonl.deleted.2026-02-07T03-38-42.185Z", "test"),
        ("uuid-1234-5678.jsonl.deleted.2026-02-08T02-40-40.029Z", "uuid-1234-5678"),
        ("my-session.jsonl.deleted.2026-01-01T00-00-00Z", "my-session"),
    ]
    
    all_passed = True
    for filename, expected_id in test_cases:
        result = _extract_session_id(filename)
        status = "✅" if result == expected_id else "❌"
        if result != expected_id:
            all_passed = False
        print(f"  {status} {filename} -> {result} (expected: {expected_id})")
    
    return all_passed


def test_deleted_file_without_state():
    """测试2: 没有 state 记录的 .deleted 文件应该从头读取"""
    print("\n=== 测试2: 无状态的 .deleted 文件处理 ===")
    
    # 创建一个 .deleted 文件 (使用 UUID 格式以通过主会话过滤)
    session_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    deleted_filename = f"{session_id}.jsonl.deleted.2026-02-09T10-00-00.000Z"
    deleted_path = os.path.join(SESSIONS_DIR, deleted_filename)
    
    # 写入测试数据（使用真实 OpenClaw 格式）
    create_openclaw_jsonl(deleted_path, [
        ("user", "Hello"),
        ("assistant", "Hi there!"),
    ])
    
    # 运行 convert
    result = convert(since_ts=None)
    
    # 验证
    expected_part = os.path.join(CONV_DIR, f"{session_id}.part000.json")
    if expected_part in result:
        print(f"  ✅ 生成了分片: {os.path.basename(expected_part)}")
        
        # 检查分片内容
        with open(expected_part) as f:
            content = json.load(f)
        msg_count = len([m for m in content if m.get("role") in ("user", "assistant")])
        print(f"  ✅ 分片包含 {msg_count} 条消息")
        return True
    else:
        print(f"  ❌ 未生成预期分片")
        print(f"     实际结果: {result}")
        return False


def test_deleted_file_with_existing_parts():
    """测试3: 有 state 记录的 .deleted 文件应该从正确的 part 索引开始"""
    print("\n=== 测试3: 有状态的 .deleted 文件（防覆盖测试）===")
    
    # 使用 UUID 格式以通过主会话过滤
    session_id = "b2c3d4e5-f6a7-8901-bcde-f23456789012"
    
    # 模拟已有 3 个分片
    state = _load_state()
    state["sessions"] = state.get("sessions", {})
    state["sessions"][session_id] = {
        "file_path": f"/fake/path/{session_id}.jsonl",
        "last_offset": 50,   # 假装已经读了 50 字节（只读了 header）
        "part_count": 3,     # 已有 3 个分片 (part000, part001, part002)
    }
    _save_state(state)
    
    # 创建对应的 .deleted 文件
    deleted_filename = f"{session_id}.jsonl.deleted.2026-02-09T11-00-00.000Z"
    deleted_path = os.path.join(SESSIONS_DIR, deleted_filename)
    
    # 写入数据（offset=50 之后应该能读到消息）
    create_openclaw_jsonl(deleted_path, [
        ("user", "This message should be read from offset 50"),
        ("assistant", "And this response too"),
        ("user", "One more message"),
    ])
    
    # 运行 convert
    result = convert(since_ts=None)
    
    # 验证: 新分片应该是 part003，而不是 part000
    expected_part = os.path.join(CONV_DIR, f"{session_id}.part003.json")
    wrong_part = os.path.join(CONV_DIR, f"{session_id}.part000.json")
    
    has_expected = expected_part in result
    has_wrong = wrong_part in result
    
    if has_expected and not has_wrong:
        print(f"  ✅ 正确生成了 part003（跳过了 0,1,2）")
        return True
    elif has_wrong:
        print(f"  ❌ 错误！生成了 part000（会覆盖已有分片）")
        return False
    else:
        # 检查是否生成了任何该 session 的分片
        session_parts = [p for p in result if session_id in p]
        if session_parts:
            print(f"  ⚠️  生成了分片但索引不对: {[os.path.basename(p) for p in session_parts]}")
        else:
            print(f"  ⚠️  未生成分片（可能因为 offset 计算问题）")
        print(f"     结果: {result}")
        return False


def test_processed_deleted_tracking():
    """测试4: 已处理的 .deleted 文件不应重复处理"""
    print("\n=== 测试4: 已处理文件跳过逻辑 ===")
    
    # 使用 UUID 格式以通过主会话过滤
    session_id = "c3d4e5f6-a7b8-9012-cdef-345678901234"
    deleted_filename = f"{session_id}.jsonl.deleted.2026-02-09T12-00-00.000Z"
    deleted_path = os.path.join(SESSIONS_DIR, deleted_filename)
    
    # 写入数据
    create_openclaw_jsonl(deleted_path, [("user", "Test message")])
    
    # 第一次 convert
    result1 = convert(since_ts=None)
    part_generated = any(session_id in p for p in result1)
    
    # 第二次 convert（不应该再处理）
    result2 = convert(since_ts=None)
    part_regenerated = any(session_id in p for p in result2)
    
    if part_generated and not part_regenerated:
        print(f"  ✅ 第一次处理: 生成分片")
        print(f"  ✅ 第二次处理: 正确跳过")
        return True
    else:
        print(f"  ❌ 第一次: {part_generated}, 第二次: {part_regenerated}")
        return False


def test_sub_session_filtering():
    """测试5: 子会话（非 UUID 格式）应该被过滤掉"""
    print("\n=== 测试5: 子会话过滤逻辑 ===")
    
    # 创建一个子会话文件（非 UUID 格式）
    sub_session_id = "my-sub-task"
    sub_filename = f"{sub_session_id}.jsonl.deleted.2026-02-09T13-00-00.000Z"
    sub_path = os.path.join(SESSIONS_DIR, sub_filename)
    create_openclaw_jsonl(sub_path, [("user", "Sub task message")])
    
    # 同时创建一个主会话文件（UUID 格式）
    main_session_id = "d4e5f6a7-b8c9-0123-def0-456789012345"
    main_filename = f"{main_session_id}.jsonl.deleted.2026-02-09T13-00-00.000Z"
    main_path = os.path.join(SESSIONS_DIR, main_filename)
    create_openclaw_jsonl(main_path, [("user", "Main session message")])
    
    # 运行 convert
    result = convert(since_ts=None)
    
    # 验证：主会话应该被处理，子会话应该被跳过
    main_processed = any(main_session_id in p for p in result)
    sub_processed = any(sub_session_id in p for p in result)
    
    if main_processed and not sub_processed:
        print(f"  ✅ 主会话已处理: {main_session_id[:20]}...")
        print(f"  ✅ 子会话已跳过: {sub_session_id}")
        return True
    else:
        print(f"  ❌ 主会话处理: {main_processed}, 子会话处理: {sub_processed}")
        return False


def test_chronological_ordering():
    """测试6: .deleted 文件应该按时间从旧到新排序处理"""
    print("\n=== 测试6: 时间顺序处理 ===")
    
    from convert_sessions import _extract_deleted_timestamp
    
    # 创建多个不同时间的 .deleted 文件
    files = [
        ("e5f6a7b8-c9d0-1234-ef01-567890123456", "2026-02-09T15-00-00.000Z"),  # 最新
        ("f6a7b8c9-d0e1-2345-f012-678901234567", "2026-02-09T13-00-00.000Z"),  # 中间
        ("a7b8c9d0-e1f2-3456-0123-789012345678", "2026-02-09T11-00-00.000Z"),  # 最旧
    ]
    
    for sid, ts in files:
        fn = f"{sid}.jsonl.deleted.{ts}"
        path = os.path.join(SESSIONS_DIR, fn)
        create_openclaw_jsonl(path, [("user", f"Message at {ts}")])
    
    # 验证排序
    import glob
    from convert_sessions import DELETED_GLOB, _is_main_session, _extract_session_id
    
    deleted_files = glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl.deleted.*"))
    # 只取这三个测试文件
    test_files = [f for f in deleted_files if any(sid in f for sid, _ in files)]
    test_files.sort(key=lambda p: _extract_deleted_timestamp(os.path.basename(p)))
    
    sorted_timestamps = [_extract_deleted_timestamp(os.path.basename(f)) for f in test_files]
    expected_order = ["2026-02-09T11-00-00.000Z", "2026-02-09T13-00-00.000Z", "2026-02-09T15-00-00.000Z"]
    
    if sorted_timestamps == expected_order:
        print(f"  ✅ 排序正确: 旧→新")
        for ts in sorted_timestamps:
            print(f"     {ts}")
        return True
    else:
        print(f"  ❌ 排序错误: {sorted_timestamps}")
        return False


def cleanup():
    """清理测试目录"""
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def main():
    print("=" * 60)
    print("memU .deleted 文件处理逻辑验证测试")
    print("=" * 60)
    
    results = []
    
    try:
        results.append(("正则匹配", test_regex_matching()))
        results.append(("无状态处理", test_deleted_file_without_state()))
        results.append(("防覆盖逻辑", test_deleted_file_with_existing_parts()))
        results.append(("跳过已处理", test_processed_deleted_tracking()))
        results.append(("子会话过滤", test_sub_session_filtering()))
        results.append(("时间顺序", test_chronological_ordering()))
    finally:
        cleanup()
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        if not passed:
            all_passed = False
        print(f"  {status}: {name}")
    
    print("\n" + ("🎉 所有测试通过！" if all_passed else "⚠️  部分测试失败"))
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
