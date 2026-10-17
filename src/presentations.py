"""
提示の記録（指示書65B-5 §7-1）。通常 DB（台帳ではない）。本人には見せない。集計にだけ使う。

照合の結果として「誰に・誰を・どの目的の・どの要素で・どの向きで・どの判定の版で」見せたか、と、
見せたか・開いたかを残す。これが無いと提示の精度（基準2）が測れない。

**記録の開始はフラグで閉じる**（POX_PRESENTATION_LOG_OPEN。既定は閉じる）。プライバシーポリシーへの
書き足しが済んでから開ける。閉じている間は何も書かない（表・書き込みの経路・保存期間の設定までを実装する）。
判定そのものは記録しない（数値は持たない）。相手の目的の門の結果も記録しない。
"""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from db_connect import get_connection, is_postgres

RETENTION_DAYS_DEFAULT = 180


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def recording_open() -> bool:
    """提示の記録を書いてよいか（既定は閉じる）。"""
    return os.environ.get("POX_PRESENTATION_LOG_OPEN", "").strip().lower() in ("1", "true", "on", "open")


def retention_days() -> int:
    """保存期間（日）。POX_PRESENTATION_RETENTION_DAYS で設定する（既定 180 日）。"""
    try:
        return max(1, int(os.environ.get("POX_PRESENTATION_RETENTION_DAYS") or RETENTION_DAYS_DEFAULT))
    except ValueError:
        return RETENTION_DAYS_DEFAULT


def judgment_version() -> dict:
    """判定の版（背景文集の版・変換の版・作動点・規則の版・埋め込みの id と revision）。"""
    from match_config import LOWER_OPERATING_POINT, UPPER_OPERATING_POINT
    from embedding_config import MODEL_TAG
    return {"background": None,                       # いまの作動点は固定値（背景文集を使わない）
            "transform": "g=(1+cos)/2",
            "upper": dict(UPPER_OPERATING_POINT), "lower": dict(LOWER_OPERATING_POINT),
            "rules": "65B",
            "embedding": MODEL_TAG, "embedding_revision": os.environ.get("POX_EMBED_MODEL_REVISION") or None}


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS presentation_log ("
            "id TEXT PRIMARY KEY, run_id TEXT, viewer TEXT NOT NULL, counterpart TEXT NOT NULL, "
            "purpose_id TEXT, necessity_ref TEXT, element_idx INTEGER, direction TEXT NOT NULL, "
            "layer TEXT NOT NULL, judgment_version TEXT NOT NULL, created_at TEXT NOT NULL, "
            "shown INTEGER NOT NULL DEFAULT 1, opened_at TEXT)")
        con.commit()
    return con


def record(viewer: str, run_id: str, rows: list, db_path: str = "pox.db") -> int:
    """提示を書く。rows: [{counterpart, purpose_id, necessity_ref, element_idx, direction, layer}]。
    フラグが閉じている間は何も書かない（0 を返す）。書いたら保存期間を過ぎた行を消す。"""
    if not recording_open() or not rows:
        return 0
    ver = json.dumps(judgment_version(), ensure_ascii=False, sort_keys=True)
    now = _now()
    with _connect(db_path) as con:
        for r in rows:
            con.execute(
                "INSERT INTO presentation_log (id, run_id, viewer, counterpart, purpose_id, necessity_ref, element_idx, "
                "direction, layer, judgment_version, created_at, shown, opened_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,NULL)",
                (f"pr_{uuid.uuid4().hex[:12]}", run_id, viewer, r["counterpart"], r.get("purpose_id"),
                 r.get("necessity_ref"), r.get("element_idx"), r["direction"], r["layer"], ver, now))
    purge(db_path=db_path)
    return len(rows)


def mark_opened(viewer: str, run_id: str, counterpart: str, db_path: str = "pox.db") -> int:
    """その提示から相手のプロフィールを開いた。フラグが閉じている間は何もしない。"""
    if not recording_open() or not run_id or not counterpart:
        return 0
    with _connect(db_path) as con:
        cur = con.execute("UPDATE presentation_log SET opened_at=%s WHERE viewer=%s AND run_id=%s AND counterpart=%s "
                          "AND opened_at IS NULL", (_now(), viewer, run_id, counterpart))
        return getattr(cur, "rowcount", 0) or 0


def purge(now: datetime = None, db_path: str = "pox.db") -> None:
    """保存期間を過ぎた行を消す。"""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=retention_days())).isoformat()
    with _connect(db_path) as con:
        con.execute("DELETE FROM presentation_log WHERE created_at < %s", (cutoff,))


def rows_for(viewer: str = None, db_path: str = "pox.db") -> list:
    """集計・テスト用。"""
    q = ("SELECT run_id, viewer, counterpart, purpose_id, necessity_ref, element_idx, direction, layer, "
         "judgment_version, created_at, shown, opened_at FROM presentation_log")
    args = ()
    if viewer:
        q += " WHERE viewer=%s"
        args = (viewer,)
    with _connect(db_path) as con:
        rs = con.execute(q + " ORDER BY created_at", args).fetchall()
    keys = ("run_id", "viewer", "counterpart", "purpose_id", "necessity_ref", "element_idx", "direction", "layer",
            "judgment_version", "created_at", "shown", "opened_at")
    return [dict(zip(keys, r)) for r in rs]
