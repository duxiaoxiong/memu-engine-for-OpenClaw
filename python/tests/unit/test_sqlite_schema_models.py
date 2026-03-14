from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

from pydantic import BaseModel
from sqlalchemy import MetaData
from sqlalchemy.sql.schema import Table
from sqlmodel import SQLModel


PYTHON_SRC = Path(__file__).resolve().parents[2] / "src"
PYTHON_SRC_STR = str(PYTHON_SRC)
if PYTHON_SRC_STR not in sys.path:
    sys.path.insert(0, PYTHON_SRC_STR)

from memu.database.sqlite.models import (
    SQLiteCategoryItemModel,
    build_sqlite_table_model,
)
from memu.database.sqlite.schema import get_sqlite_sqlalchemy_models


def _index_names(table: Table) -> set[str]:
    return {str(index.name) for index in table.indexes}


def _find_index(table: Table, name: str):
    for index in table.indexes:
        if str(index.name) == name:
            return index
    return None


def test_build_sqlite_category_item_model_recreates_unique_index() -> None:
    class EmptyScopeOne(BaseModel):
        pass

    class EmptyScopeTwo(BaseModel):
        pass

    first_model = build_sqlite_table_model(
        EmptyScopeOne,
        SQLiteCategoryItemModel,
        tablename="memu_category_items",
        metadata=MetaData(),
    )
    second_model = build_sqlite_table_model(
        EmptyScopeTwo,
        SQLiteCategoryItemModel,
        tablename="memu_category_items",
        metadata=MetaData(),
    )

    first_table = cast(Table, getattr(first_model, "__table__"))
    second_table = cast(Table, getattr(second_model, "__table__"))

    first_indexes = _index_names(first_table)
    second_indexes = _index_names(second_table)

    assert "idx_sqlite_category_items_unique" not in first_indexes
    assert "idx_sqlite_category_items_unique" not in second_indexes


def test_get_sqlite_sqlalchemy_models_keeps_category_item_unique_index() -> None:
    class ScopeOne(BaseModel):
        agent_id: str

    class ScopeTwo(BaseModel):
        user_id: str

    first_models = get_sqlite_sqlalchemy_models(scope_model=ScopeOne)
    second_models = get_sqlite_sqlalchemy_models(scope_model=ScopeTwo)

    first_category_item = cast(type[SQLModel], first_models.CategoryItem)
    second_category_item = cast(type[SQLModel], second_models.CategoryItem)
    first_table = cast(Table, getattr(first_category_item, "__table__"))
    second_table = cast(Table, getattr(second_category_item, "__table__"))

    first_indexes = _index_names(first_table)
    second_indexes = _index_names(second_table)
    first_unique_index = _find_index(first_table, "idx_sqlite_category_items_unique")
    second_unique_index = _find_index(second_table, "idx_sqlite_category_items_unique")

    assert "idx_sqlite_category_items_unique" in first_indexes
    assert "idx_sqlite_category_items_unique" in second_indexes
    assert first_unique_index is not None
    assert second_unique_index is not None
    assert first_unique_index.unique is True
    assert second_unique_index.unique is True
    assert [str(column.name) for column in first_unique_index.columns] == [
        "item_id",
        "category_id",
    ]
    assert [str(column.name) for column in second_unique_index.columns] == [
        "item_id",
        "category_id",
    ]
