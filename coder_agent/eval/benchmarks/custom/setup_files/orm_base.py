# orm_base.py — agent must add a chainable query builder to this sqlite3 Model base
#
# Current: basic CRUD (save, delete, find_by_id, find_all).
# Missing: a QueryBuilder returned by Model.query() that supports
#   .filter(field=value)
#   .order_by(field, desc=False)
#   .limit(n)
#   .all() -> list[Model]
#   .first() -> Model | None
#   .count() -> int
#
# Each Model subclass maps to a DB table. The agent creates the query builder.

import sqlite3
from typing import Any, Optional, Type, TypeVar

T = TypeVar("T", bound="Model")


class ModelMeta(type):
    """Metaclass that registers subclasses and their column definitions."""
    _registry: dict[str, type] = {}

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        if name != "Model":
            mcs._registry[name] = cls
        return cls


class Model(metaclass=ModelMeta):
    """Base class for ORM models backed by an in-memory or file-based SQLite DB.

    Subclass example:
        class User(Model):
            __tablename__ = "users"
            __columns__ = ["id INTEGER PRIMARY KEY", "name TEXT", "age INTEGER"]
    """

    __tablename__: str = ""
    __columns__: list[str] = []
    _db: Optional[sqlite3.Connection] = None

    @classmethod
    def _conn(cls) -> sqlite3.Connection:
        if cls._db is None:
            cls._db = sqlite3.connect(":memory:")
            cls._db.row_factory = sqlite3.Row
            cols = ", ".join(cls.__columns__)
            cls._db.execute(f"CREATE TABLE IF NOT EXISTS {cls.__tablename__} ({cols})")
            cls._db.commit()
        return cls._db

    @classmethod
    def _reset(cls) -> None:
        """Drop and recreate the table (useful in tests)."""
        cls._db = None

    def _col_names(self) -> list[str]:
        return [c.split()[0] for c in self.__columns__]

    def save(self) -> "Model":
        """Insert or replace this record."""
        conn = self._conn()
        cols = self._col_names()
        values = [getattr(self, c, None) for c in cols]
        placeholders = ", ".join("?" * len(cols))
        col_str = ", ".join(cols)
        conn.execute(
            f"INSERT OR REPLACE INTO {self.__tablename__} ({col_str}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
        return self

    @classmethod
    def find_by_id(cls: Type[T], id_val: Any) -> Optional[T]:
        row = cls._conn().execute(
            f"SELECT * FROM {cls.__tablename__} WHERE id=?", (id_val,)
        ).fetchone()
        return cls._row_to_obj(row) if row else None

    @classmethod
    def find_all(cls: Type[T]) -> list[T]:
        rows = cls._conn().execute(f"SELECT * FROM {cls.__tablename__}").fetchall()
        return [cls._row_to_obj(r) for r in rows]

    def delete(self) -> None:
        self._conn().execute(
            f"DELETE FROM {self.__tablename__} WHERE id=?", (getattr(self, "id"),)
        )
        self._conn().commit()

    @classmethod
    def _row_to_obj(cls: Type[T], row: sqlite3.Row) -> T:
        obj = cls.__new__(cls)
        for key in row.keys():
            setattr(obj, key, row[key])
        return obj

    @classmethod
    def query(cls: Type[T]) -> "QueryBuilder[T]":
        """Return a QueryBuilder for this model. TODO: implement QueryBuilder."""
        raise NotImplementedError("QueryBuilder not implemented yet")


class QueryBuilder:
    """Chainable query builder for Model subclasses. Agent must implement this."""
    pass
