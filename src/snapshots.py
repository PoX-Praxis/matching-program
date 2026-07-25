#!/usr/bin/env python3
"""
PoX 軌跡層 — user_snapshots（時点スナップショット）。指示書12。

再構造化（新JSON登録）のたびに1点、その時点の 意志・現状・supporting・必要像を
不変レコードとして保存する。編集・自由記述では作らない（呼び出し側で制御）。
表示（公開範囲の絞り込み）は呼び出し側の責務。ここは「全保存」する。

接続: get_connection(db_path) を使用。SQL は %s プレースホルダ統一（SQLite は内部で ? に変換）。
テーブルは schema.init() で作成されるが、テスト用に SQLite では遅延作成もする（ledger と同型）。
"""
import json, uuid
from datetime import datetime, timezone
from db_connect import get_connection, is_postgres

_DDL = """CREATE TABLE IF NOT EXISTS user_snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    will_text       TEXT,
    state_json      TEXT,
    supporting_json TEXT,
    necessity_json  TEXT,
    src_input_hash  TEXT
)"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():   # 本番(Postgres)は schema.init() で作成済み
        con.execute(_DDL)
        con.commit()
    return con


def save_snapshot(user_id, *, will_text, state, supporting, necessity,
                  src_input_hash, db_path="pox.db"):
    """
    再構造化1回につき1点を保存（不変）。直前スナップショットと src_input_hash が同一なら
    実質同じ内容の再登録として保存しない（churn 防止）。
    戻り値: 保存した snapshot_id / 重複でスキップなら None。
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
            "(snapshot_id, user_id, created_at, will_text, state_json, "
            " supporting_json, necessity_json, src_input_hash) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (sid, user_id, _now(), will_text or "",
             json.dumps(state or {}, ensure_ascii=False),
             json.dumps(supporting or {}, ensure_ascii=False),
             json.dumps(necessity or {}, ensure_ascii=False),
             src_input_hash or ""),
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
    """その人の全スナップショットを created_at 昇順で返す（necessity は full を含む・絞りは呼出側）。"""
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT snapshot_id, created_at, will_text, state_json, "
            "supporting_json, necessity_json, src_input_hash "
            "FROM user_snapshots WHERE user_id=%s ORDER BY created_at ASC",
            (user_id,)
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "snapshot_id": r[0], "created_at": r[1], "will_text": r[2],
            "state": json.loads(r[3] or "{}"),
            "supporting": json.loads(r[4] or "{}"),
            "necessity": json.loads(r[5] or "{}"),
            "src_input_hash": r[6],
        })
    return out
