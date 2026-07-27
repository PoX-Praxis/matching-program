#!/usr/bin/env python3
"""
PoX 軌跡層 — user_snapshots（時点スナップショット）。指示書12（改訂版）。

再構造化（新JSON登録）のたびに1点、その時点の 意志・現状・supporting・必要像を
不変レコードとして保存する（全保存）。編集・自由記述では作らない（呼び出し側で制御）。

改訂版で追加:
  - schema_version: 記録時のスキーマ版（黙示のスキーマ移行による遡及的意味変化を防ぐ）
  - vulnerable_hidden: 本人が「この時点の中身を第三者に非公開」にしたか（既定 0=false）
    ※消すのではなく第三者表示を止めるだけ。事実・当事者相手への表示は残る。

表示（公開範囲の絞り込み）は呼び出し側の責務。ここは全保存する。
接続: get_connection(db_path)。SQL は %s プレースホルダ（SQLite は内部で ? に変換）。
"""
import json, uuid
from datetime import datetime, timezone
from db_connect import get_connection, is_postgres

_DDL = """CREATE TABLE IF NOT EXISTS user_snapshots (
    snapshot_id       TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    schema_version    TEXT,
    will_text         TEXT,
    state_json        TEXT,
    supporting_json   TEXT,
    necessity_json    TEXT,
    src_input_hash    TEXT,
    vulnerable_hidden INTEGER NOT NULL DEFAULT 0
)"""

_ADDCOLS = [("schema_version", "TEXT"), ("vulnerable_hidden", "INTEGER NOT NULL DEFAULT 0")]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():   # 本番(Postgres)は schema.init() で作成・後付け済み
        con.execute(_DDL)
        cols = {r[1] for r in con.execute("PRAGMA table_info(user_snapshots)").fetchall()}
        for name, typ in _ADDCOLS:            # 既存 SQLite DB への後付け（idempotent）
            if name not in cols:
                con.execute(f"ALTER TABLE user_snapshots ADD COLUMN {name} {typ}")
        con.commit()
    return con


def save_snapshot(user_id, *, will_text, state, supporting, necessity,
                  src_input_hash, schema_version="", db_path="pox.db"):
    """
    再構造化1回につき1点を保存（不変）。直前と src_input_hash が同一なら保存しない（churn 防止）。
    戻り値: 保存した snapshot_id / 重複スキップなら None。
    """
    with _connect(db_path) as con:
        last = con.execute(
            "SELECT src_input_hash FROM user_snapshots WHERE user_id=%s "
            "ORDER BY created_at DESC LIMIT 1", (user_id,)
        ).fetchone()
        if last and src_input_hash and last[0] == src_input_hash:
            return None
        sid = "s_" + uuid.uuid4().hex[:12]
        con.execute(
            "INSERT INTO user_snapshots "
            "(snapshot_id, user_id, created_at, schema_version, will_text, state_json, "
            " supporting_json, necessity_json, src_input_hash, vulnerable_hidden) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (sid, user_id, _now(), schema_version or "", will_text or "",
             json.dumps(state or {}, ensure_ascii=False),
             json.dumps(supporting or {}, ensure_ascii=False),
             json.dumps(necessity or {}, ensure_ascii=False),
             src_input_hash or "", 0),
        )
    return sid


def latest_snapshot_id(user_id, db_path="pox.db"):
    """成立時の結合に使う: その人の最新 snapshot_id（無ければ None）。"""
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT snapshot_id FROM user_snapshots WHERE user_id=%s "
            "ORDER BY created_at DESC LIMIT 1", (user_id,)
        ).fetchone()
    return row[0] if row else None


def get_snapshots(user_id, db_path="pox.db"):
    """その人の全スナップショットを created_at 昇順で返す（necessity は full・絞りは呼出側）。"""
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT snapshot_id, created_at, schema_version, will_text, state_json, "
            "supporting_json, necessity_json, src_input_hash, vulnerable_hidden "
            "FROM user_snapshots WHERE user_id=%s ORDER BY created_at ASC",
            (user_id,)
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "snapshot_id": r[0], "created_at": r[1], "schema_version": r[2] or "",
            "will_text": r[3],
            "state": json.loads(r[4] or "{}"),
            "supporting": json.loads(r[5] or "{}"),
            "necessity": json.loads(r[6] or "{}"),
            "src_input_hash": r[7],
            "vulnerable_hidden": bool(r[8]),
        })
    return out


def set_snapshot_hidden(snapshot_id, user_id, hidden, db_path="pox.db") -> bool:
    """
    本人が「この時点の中身を第三者に非公開」を切り替える（指示書12改訂 §4-4）。
    所有者一致（user_id）のときだけ更新。戻り値: 更新できたか。
    消すのではなく第三者表示を止めるだけ（記録・当事者相手への表示は残る）。
    """
    with _connect(db_path) as con:
        cur = con.execute(
            "UPDATE user_snapshots SET vulnerable_hidden=%s WHERE snapshot_id=%s AND user_id=%s",
            (1 if hidden else 0, snapshot_id, user_id),
        )
        return bool(getattr(cur, "rowcount", 0))
