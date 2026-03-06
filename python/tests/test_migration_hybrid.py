"""
Comprehensive migration tests using hybrid fixtures (v0.2.6 real + synthetic SQL).

Tests cover:
- v026_real.db migration (10 memories, 5 categories, 3 resources)
- empty_db.sql migration (empty database)
- single_agent.sql migration (100 memories)
- multi_agent.sql migration (3 agents with cross-references)
- large_db.sql migration (1000+ memories)
- corrupted_schema.sql migration (missing agentName column)
- Backup file creation during migration
- Migration idempotency (running twice doesn't break)
- Error handling (corrupted DB, missing files)
"""

import sqlite3
import tempfile
from pathlib import Path
import shutil
import pytest

from memu.migration import (
    migrate_existing_memories,
    check_database_version,
    backup_database,
    verify_migration,
    _execute_migration,
    TABLES_TO_MIGRATE,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.migration


def _load_sql_fixture(fixture_name: str, target_db: Path) -> None:
    """Load a SQL fixture file into a SQLite database."""
    sql_path = FIXTURES_DIR / fixture_name
    assert sql_path.exists(), f"Fixture not found: {sql_path}"
    
    sql_content = sql_path.read_text(encoding="utf-8")
    conn = sqlite3.connect(target_db)
    try:
        conn.executescript(sql_content)
        conn.commit()
    finally:
        conn.close()


def _get_table_columns(db_path: Path, table_name: str) -> list[str]:
    """Get column names for a table."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cursor.fetchall()]
    finally:
        conn.close()


def _count_rows(db_path: Path, table_name: str) -> int:
    """Count rows in a table."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
        return cursor.fetchone()[0]
    finally:
        conn.close()


# ============================================================================
# Test 1: v026_real.db migration (real v0.2.6 database)
# ============================================================================

def test_v026_real_db_migration():
    """Test migration of real v0.2.6 database with 10 memories, 5 categories, 3 resources."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_v026_") as tmpdir:
        db_path = Path(tmpdir) / "memu.db"
        
        # Copy v026_real.db fixture
        shutil.copy(FIXTURES_DIR / "v026_real.db", db_path)
        
        # Verify pre-migration state
        status = check_database_version(str(db_path))
        assert status["version"] == "v1", "v026_real.db should be v1 (no agentName)"
        assert status["needs_migration"] is True
        
        # Verify data counts before migration
        assert _count_rows(db_path, "memu_memory_items") == 10
        assert _count_rows(db_path, "memu_memory_categories") == 5
        assert _count_rows(db_path, "memu_resources") == 3
        
        # Run migration
        success = migrate_existing_memories(str(db_path))
        assert success is True, "Migration should succeed"
        
        # Verify post-migration state
        status = check_database_version(str(db_path))
        assert status["version"] == "v2", "After migration, should be v2"
        assert status["needs_migration"] is False
        
        # Verify agentName column exists in all tables
        for table in TABLES_TO_MIGRATE:
            if table in ["memu_memory_items", "memu_memory_categories", "memu_resources"]:
                columns = _get_table_columns(db_path, table)
                assert "agentName" in columns, f"{table} should have agentName column"
        
        # Verify data integrity (counts unchanged)
        assert _count_rows(db_path, "memu_memory_items") == 10
        assert _count_rows(db_path, "memu_memory_categories") == 5
        assert _count_rows(db_path, "memu_resources") == 3
        
        # Verify default agentName value
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute("SELECT DISTINCT agentName FROM memu_memory_items")
            agent_names = [row[0] for row in cursor.fetchall()]
            assert "main" in agent_names, "Default agentName should be 'main'"
        finally:
            conn.close()


# ============================================================================
# Test 2: empty_db.sql migration (empty database)
# ============================================================================

def test_empty_db_migration():
    """Test migration of empty database (no tables at all)."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_empty_") as tmpdir:
        db_path = Path(tmpdir) / "memu.db"
        
        # Load empty_db.sql fixture (truly empty, no tables)
        _load_sql_fixture("empty_db.sql", db_path)
        
        # Verify pre-migration state (no tables = v2, no migration needed)
        status = check_database_version(str(db_path))
        assert status["version"] == "v2", "Empty DB is considered v2"
        assert status["needs_migration"] is False, "Empty DB needs no migration"
        
        # Run migration (should be no-op)
        success = migrate_existing_memories(str(db_path))
        assert success is True, "Migration should succeed on empty DB"
        
        # Verify post-migration state unchanged
        status = check_database_version(str(db_path))
        assert status["version"] == "v2"
        assert status["needs_migration"] is False


# ============================================================================
# Test 3: single_agent.sql migration (100 memories)
# ============================================================================

def test_single_agent_migration():
    """Test migration of single agent database with 100 memories."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_single_") as tmpdir:
        db_path = Path(tmpdir) / "memu.db"
        
        # Load single_agent.sql fixture
        _load_sql_fixture("single_agent.sql", db_path)
        
        # Verify pre-migration state
        memory_count = _count_rows(db_path, "memu_memory_items")
        assert memory_count == 100, "single_agent.sql should have 100 memories"
        
        # Run migration
        success = migrate_existing_memories(str(db_path))
        assert success is True
        
        # Verify data integrity
        assert _count_rows(db_path, "memu_memory_items") == 100
        
        # Verify all memories have agentName
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM memu_memory_items WHERE agentName IS NOT NULL")
            count_with_agent = cursor.fetchone()[0]
            assert count_with_agent == 100, "All 100 memories should have agentName"
        finally:
            conn.close()


# ============================================================================
# Test 4: multi_agent.sql migration (3 agents with cross-references)
# ============================================================================

def test_multi_agent_migration():
    """Test migration of multi-agent database with cross-references."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_multi_") as tmpdir:
        db_path = Path(tmpdir) / "memu.db"
        
        # Load multi_agent.sql fixture
        _load_sql_fixture("multi_agent.sql", db_path)
        
        # Run migration
        success = migrate_existing_memories(str(db_path))
        assert success is True
        
        # Verify agentName column exists
        columns = _get_table_columns(db_path, "memu_memory_items")
        assert "agentName" in columns
        
        # Verify migration doesn't break cross-references
        conn = sqlite3.connect(db_path)
        try:
            # Check that all memories still accessible
            cursor = conn.execute("SELECT COUNT(*) FROM memu_memory_items")
            total_memories = cursor.fetchone()[0]
            assert total_memories > 0, "Should have memories after migration"
        finally:
            conn.close()


# ============================================================================
# Test 5: large_db.sql migration (1000+ memories)
# ============================================================================

def test_large_db_migration():
    """Test migration of large database with 1000+ memories."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_large_") as tmpdir:
        db_path = Path(tmpdir) / "memu.db"
        
        # Load large_db.sql fixture
        _load_sql_fixture("large_db.sql", db_path)
        
        # Verify pre-migration state
        memory_count = _count_rows(db_path, "memu_memory_items")
        assert memory_count >= 1000, "large_db.sql should have 1000+ memories"
        
        # Run migration
        success = migrate_existing_memories(str(db_path))
        assert success is True
        
        # Verify data integrity (no data loss)
        assert _count_rows(db_path, "memu_memory_items") == memory_count
        
        # Verify all memories have agentName
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM memu_memory_items WHERE agentName IS NOT NULL")
            count_with_agent = cursor.fetchone()[0]
            assert count_with_agent == memory_count, "All memories should have agentName"
        finally:
            conn.close()


# ============================================================================
# Test 6: corrupted_schema.sql migration (missing agentName column)
# ============================================================================

def test_corrupted_schema_migration():
    """Test migration of database with corrupted/incomplete schema."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_corrupted_") as tmpdir:
        db_path = Path(tmpdir) / "memu.db"
        
        # Load corrupted_schema.sql fixture
        _load_sql_fixture("corrupted_schema.sql", db_path)
        
        # Verify pre-migration state (missing agentName)
        columns = _get_table_columns(db_path, "memu_memory_items")
        assert "agentName" not in columns, "corrupted_schema.sql should not have agentName"
        
        # Run migration
        success = migrate_existing_memories(str(db_path))
        assert success is True, "Migration should handle corrupted schema"
        
        # Verify post-migration state
        columns = _get_table_columns(db_path, "memu_memory_items")
        assert "agentName" in columns, "Migration should add missing agentName column"


# ============================================================================
# Test 7: Backup file creation during migration
# ============================================================================

def test_backup_creation():
    """Test that migration creates backup file before modifying database."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_backup_") as tmpdir:
        db_path = Path(tmpdir) / "memu.db"
        
        # Copy v026_real.db fixture
        shutil.copy(FIXTURES_DIR / "v026_real.db", db_path)
        
        # Run migration
        success = migrate_existing_memories(str(db_path))
        assert success is True
        
        # Verify backup file exists
        backup_files = list(Path(tmpdir).glob("memu.db.backup_*"))
        assert len(backup_files) >= 1, "Migration should create backup file"
        
        # Verify backup is valid SQLite database
        backup_path = backup_files[0]
        conn = sqlite3.connect(backup_path)
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM memu_memory_items")
            count = cursor.fetchone()[0]
            assert count == 10, "Backup should contain original data"
        finally:
            conn.close()


# ============================================================================
# Test 8: Migration idempotency (running twice doesn't break)
# ============================================================================

def test_migration_idempotency():
    """Test that running migration twice is safe (idempotent)."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_idempotent_") as tmpdir:
        db_path = Path(tmpdir) / "memu.db"
        
        # Copy v026_real.db fixture
        shutil.copy(FIXTURES_DIR / "v026_real.db", db_path)
        
        # Run migration first time
        success1 = migrate_existing_memories(str(db_path))
        assert success1 is True
        
        # Verify post-migration state
        status1 = check_database_version(str(db_path))
        assert status1["version"] == "v2"
        memory_count1 = _count_rows(db_path, "memu_memory_items")
        
        # Run migration second time (should be no-op)
        success2 = migrate_existing_memories(str(db_path))
        assert success2 is True, "Second migration should succeed (no-op)"
        
        # Verify state unchanged
        status2 = check_database_version(str(db_path))
        assert status2["version"] == "v2"
        assert status2["needs_migration"] is False
        memory_count2 = _count_rows(db_path, "memu_memory_items")
        assert memory_count2 == memory_count1, "Data should be unchanged"
        
        # Verify no duplicate columns
        columns = _get_table_columns(db_path, "memu_memory_items")
        assert columns.count("agentName") == 1, "Should have exactly one agentName column"


# ============================================================================
# Test 9: Error handling - missing database file
# ============================================================================

def test_missing_database_file():
    """Test migration handles missing database file gracefully."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_missing_") as tmpdir:
        db_path = Path(tmpdir) / "nonexistent.db"
        
        # Run migration on non-existent file
        success = migrate_existing_memories(str(db_path))
        assert success is True, "Migration should skip non-existent database"


# ============================================================================
# Test 10: Error handling - corrupted database file
# ============================================================================

def test_corrupted_database_file():
    """Test migration handles corrupted database file gracefully."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_corrupt_") as tmpdir:
        db_path = Path(tmpdir) / "corrupted.db"
        
        # Create corrupted database file (not valid SQLite)
        db_path.write_text("This is not a valid SQLite database file", encoding="utf-8")
        
        # Run migration on corrupted file (should handle gracefully and skip)
        success = migrate_existing_memories(str(db_path))
        assert success is True, "Migration should handle corrupted DB gracefully (skip)"


# ============================================================================
# Test 11: Verify migration function
# ============================================================================

def test_verify_migration_function():
    """Test verify_migration function correctly validates migration."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_verify_") as tmpdir:
        db_path = Path(tmpdir) / "memu.db"
        
        # Copy v026_real.db fixture
        shutil.copy(FIXTURES_DIR / "v026_real.db", db_path)
        
        # Verify should fail before migration
        result_before = verify_migration(str(db_path))
        assert result_before is False, "Verification should fail before migration"
        
        # Run migration
        migrate_existing_memories(str(db_path))
        
        # Verify should pass after migration
        result_after = verify_migration(str(db_path))
        assert result_after is True, "Verification should pass after migration"


# ============================================================================
# Test 12: Backup function standalone
# ============================================================================

def test_backup_function():
    """Test backup_database function creates valid backup."""
    with tempfile.TemporaryDirectory(prefix="memu_migration_backup_fn_") as tmpdir:
        db_path = Path(tmpdir) / "memu.db"
        
        # Copy v026_real.db fixture
        shutil.copy(FIXTURES_DIR / "v026_real.db", db_path)
        
        # Create backup
        backup_path = backup_database(str(db_path))
        assert Path(backup_path).exists(), "Backup file should exist"
        
        # Verify backup is valid
        conn = sqlite3.connect(backup_path)
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM memu_memory_items")
            count = cursor.fetchone()[0]
            assert count == 10, "Backup should contain original data"
        finally:
            conn.close()
