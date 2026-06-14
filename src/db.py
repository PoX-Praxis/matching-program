#!/usr/bin/env python3
"""
PoX DB層（最小）— seeker の保存と読み出し。
SQLite に seeker を JSON のまま 1 カラムで保持する。
承認台帳は別フェーズ（connection_ledger.schema.json で定義済み）。
"""
import json, sqlite3


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS seekers "
        "(id TEXT PRIMARY KEY, seeker_json TEXT NOT NULL)"
    )
    con.commit()
    return con


def save_seeker(seeker_id: str, seeker: dict, db_path: str = "pox.db") -> None:
    """seeker を1件保存（同じ id なら上書き）。"""
    with _connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO seekers (id, seeker_json) VALUES (?, ?)",
            (seeker_id, json.dumps(seeker, ensure_ascii=False)),
        )


def load_all_seekers(db_path: str = "pox.db") -> list[dict]:
    """全 seeker を [{"id": ..., "seeker": {...}}] のリストで返す。"""
    with _connect(db_path) as con:
        rows = con.execute("SELECT id, seeker_json FROM seekers").fetchall()
    return [{"id": row[0], "seeker": json.loads(row[1])} for row in rows]
