#!/usr/bin/env python3
"""
PoX DB接続抽象化レイヤー (方針A: 生SQL最小書き換え)。

DATABASE_URL 環境変数がある → PostgreSQL（本番 / Render）
DATABASE_URL がない         → SQLite（ローカル開発）

各モジュールは get_connection(db_path) を呼ぶだけでよい。
SQL は %s プレースホルダで書く（SQLite 向けは内部で ? に自動変換）。
"""
import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL")
_USE_POSTGRES = bool(DATABASE_URL)


def is_postgres() -> bool:
    """現在の接続先が PostgreSQL かどうか。"""
    return _USE_POSTGRES


# ── Postgres 用ラッパー ─────────────────────────────────────

class _PgResult:
    """psycopg2 カーソルを sqlite3 execute() 戻り値 API に合わせるラッパー。"""
    __slots__ = ("_cur",)

    def __init__(self, cur):
        self._cur = cur

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _PgConn:
    """
    psycopg2 Connection を sqlite3.Connection の .execute() API に合わせるラッパー。
    context manager で commit / rollback / close を実行する。
    """
    def __init__(self, pg_conn):
        self._conn = pg_conn
        self._cur = pg_conn.cursor()

    def execute(self, sql: str, params=None) -> _PgResult:
        self._cur.execute(sql, params or ())
        return _PgResult(self._cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
        return False


# ── SQLite 用ラッパー ───────────────────────────────────────

class _SqResult:
    """sqlite3 Cursor を _PgResult と同一 API で包むラッパー。"""
    __slots__ = ("_cur",)

    def __init__(self, cur):
        self._cur = cur

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _SqConn:
    """
    sqlite3.Connection を %s プレースホルダに対応させるラッパー。
    context manager で commit / rollback を行う（close は呼ばない = sqlite3 本来の挙動）。
    """
    def __init__(self, con):
        self._con = con

    def execute(self, sql: str, params=None) -> _SqResult:
        # psycopg2 の %s プレースホルダを SQLite の ? に変換（文字列リテラル内には %s が無い前提）
        cur = self._con.execute(sql.replace("%s", "?"), params or ())
        return _SqResult(cur)

    def commit(self):
        self._con.commit()

    def rollback(self):
        self._con.rollback()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._con.commit()
        else:
            self._con.rollback()
        return False


# ── 公開 API ───────────────────────────────────────────────

def get_connection(db_path: str = "pox.db"):
    """
    Postgres または SQLite の接続を返す。
    どちらも `with get_connection(db_path) as con: con.execute(sql, params)` で使える。
    SQL は必ず %s プレースホルダで記述すること。
    """
    if _USE_POSTGRES:
        import psycopg2
        # Render は "postgres://" を返すことがある。psycopg2 は "postgresql://" を要求。
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return _PgConn(psycopg2.connect(url))
    else:
        return _SqConn(sqlite3.connect(db_path))
