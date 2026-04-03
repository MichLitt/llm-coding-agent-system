# test_db_connection.py — do NOT modify this file
import pytest
from db_connection import DBConnection


def test_context_manager_basic_usage():
    with DBConnection() as db:
        db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        db.execute("INSERT INTO t VALUES (1, 'hello')")
        cur = db.execute("SELECT val FROM t WHERE id=1")
        row = cur.fetchone()
    assert row[0] == "hello"


def test_context_manager_returns_self():
    db = DBConnection()
    with db as ctx:
        assert ctx is db


def test_context_manager_rollback_on_exception():
    db = DBConnection()
    db.open()
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    db.close()

    try:
        with DBConnection(db.db_path) as conn:
            conn.execute("INSERT INTO items VALUES (1, 'a')")
            raise ValueError("simulated error")
    except ValueError:
        pass

    # After rollback the row should NOT be committed
    with DBConnection(db.db_path) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM items")
        count = cur.fetchone()[0]
    assert count == 0, "Row should have been rolled back"


def test_context_manager_closes_connection_after_exit():
    db = DBConnection()
    with db:
        assert db.conn is not None
    assert db.conn is None


def test_plain_open_close_still_works():
    db = DBConnection()
    db.open()
    db.execute("CREATE TABLE x (v INTEGER)")
    db.execute("INSERT INTO x VALUES (42)")
    db.close()
