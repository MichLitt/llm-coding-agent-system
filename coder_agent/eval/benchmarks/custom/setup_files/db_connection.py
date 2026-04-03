# db_connection.py — DBConnection is missing context manager support; agent must add it

import sqlite3


class DBConnection:
    """Wraps a sqlite3 connection.

    Currently missing __enter__ and __exit__ so it cannot be used with 'with'.
    The agent must add context manager support so the connection is properly
    committed on success and rolled back on exception.
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def open(self):
        self.conn = sqlite3.connect(self.db_path)
        return self.conn

    def close(self, commit: bool = True):
        if self.conn:
            if commit:
                self.conn.commit()
            else:
                self.conn.rollback()
            self.conn.close()
            self.conn = None

    def execute(self, sql: str, params: tuple = ()):
        if self.conn is None:
            raise RuntimeError("Connection is not open")
        return self.conn.execute(sql, params)

    # TODO: implement __enter__ and __exit__
