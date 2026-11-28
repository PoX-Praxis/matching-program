"""説明を作る予定（キュー。指示書81 §3）。本体の DB。台帳には書かない。文は持たない（subject・状態・時刻・数だけ）。

- 登録・更新で、作る組（許可リストの組だけ）を積む。同じ組は 1 行にまとめる
- 作っている途中にどちらかが更新したら、終わった後に 1 回だけ作り直す（again）
- 作れなかった（推論先が止まっていた）組は、予定に残す（pending に戻す）"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from db_connect import get_connection, is_postgres

QUEUE_DDL = """CREATE TABLE IF NOT EXISTS explanation_queue (
    pair_key      TEXT PRIMARY KEY,
    a_subject     TEXT NOT NULL,
    b_subject     TEXT NOT NULL,
    status        TEXT NOT NULL,
    requested_at  TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    again         INTEGER NOT NULL DEFAULT 0,
    reason        TEXT,
    inputs_digest TEXT,
    record_json   TEXT
)"""
BATCH_DDL = """CREATE TABLE IF NOT EXISTS explanation_batches (
    id          TEXT PRIMARY KEY,
    backend     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    pairs       INTEGER NOT NULL DEFAULT 0,
    made        INTEGER NOT NULL DEFAULT 0,
    record_json TEXT
)"""
# 費用のかかるジョブを起動した記録（指示書84 §1-3）。起動する前に 1 行書く（途中で止まっても数える。閉じる側に倒す）。
# day・month は日本の暦（Asia/Tokyo）。1 日・1 か月の上限は、この行の数で数える。文は持たない
LAUNCH_DDL = """CREATE TABLE IF NOT EXISTS explanation_job_launches (
    id          TEXT PRIMARY KEY,
    backend     TEXT NOT NULL,
    launched_at TEXT NOT NULL,
    day         TEXT NOT NULL,
    month       TEXT NOT NULL,
    batch_id    TEXT,
    job_id      TEXT,
    record_json TEXT
)"""
DDL = (QUEUE_DDL, BATCH_DDL, LAUNCH_DDL)
JST = timezone(timedelta(hours=9))
STATUSES = ("pending", "running", "done", "failed", "skipped")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db_path="pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        for ddl in DDL:
            con.execute(ddl)
        con.commit()
    return con


def pair_key(a, b):
    return "|".join(sorted((a, b)))


def ordered(a, b):
    """組の二人の順（一人目＝ subject の小さいほう）。同じ組はいつも同じ順で作る（プロンプトの番号が変わらないように）。"""
    return tuple(sorted((a, b)))


def enqueue(a, b, db_path="pox.db"):
    """組を積む。作っている途中なら、終わった後に 1 回だけ作り直す印を付ける。戻り値: "queued"・"again"・"same"。"""
    if not a or not b or a == b:
        return None
    k = pair_key(a, b)
    x, y = ordered(a, b)
    now = _now()
    with _connect(db_path) as con:
        r = con.execute("SELECT status FROM explanation_queue WHERE pair_key=%s", (k,)).fetchone()
        if r and r[0] == "running":
            con.execute("UPDATE explanation_queue SET again=1 WHERE pair_key=%s", (k,))
            return "again"
        if r and r[0] == "pending":
            return "same"
        if r:
            con.execute("UPDATE explanation_queue SET status='pending', requested_at=%s, again=0, reason=NULL WHERE pair_key=%s", (now, k))
        else:
            con.execute("INSERT INTO explanation_queue (pair_key, a_subject, b_subject, status, requested_at) VALUES (%s, %s, %s, 'pending', %s)",
                        (k, x, y, now))
    return "queued"


def pending(db_path="pox.db"):
    """作る予定の組（積んだ順）。[{"pair_key", "a", "b"}]。"""
    with _connect(db_path) as con:
        rows = con.execute("SELECT pair_key, a_subject, b_subject FROM explanation_queue WHERE status='pending' "
                           "ORDER BY requested_at, pair_key").fetchall()
    return [{"pair_key": r[0], "a": r[1], "b": r[2]} for r in rows]


def claim(k, db_path="pox.db"):
    """作り始める（pending のときだけ）。取れたら True。"""
    with _connect(db_path) as con:
        cur = con.execute("UPDATE explanation_queue SET status='running', started_at=%s, attempts=attempts+1 "
                          "WHERE pair_key=%s AND status='pending'", (_now(), k))
        n = getattr(cur, "rowcount", None)
        if n is None or n < 0:
            n = 1 if con.execute("SELECT 1 FROM explanation_queue WHERE pair_key=%s AND status='running'", (k,)).fetchone() else 0
    return n > 0


def finish(k, status, reason=None, inputs_digest=None, record=None, db_path="pox.db"):
    """作り終わった（status: done・failed・skipped）。途中で更新があった組（again）は、もう 1 回だけ pending に戻す。
    推論先が止まっていたとき（status="pending"）は、予定に残す。"""
    assert status in STATUSES
    with _connect(db_path) as con:
        r = con.execute("SELECT again FROM explanation_queue WHERE pair_key=%s", (k,)).fetchone()
        again = bool(r and r[0])
        final = "pending" if (again and status in ("done", "failed", "skipped")) else status
        con.execute("UPDATE explanation_queue SET status=%s, finished_at=%s, again=0, reason=%s, inputs_digest=%s, record_json=%s "
                    "WHERE pair_key=%s", (final, _now(), reason, inputs_digest,
                                         json.dumps(record, ensure_ascii=False, sort_keys=True) if record is not None else None, k))
    return final


def skip(k, reason, db_path="pox.db"):
    """作らない組（許可リストから外れた組など）を skipped にする。作り直しの印（again）も消す。予定の行だったときだけ True。"""
    with _connect(db_path) as con:
        cur = con.execute("UPDATE explanation_queue SET status='skipped', finished_at=%s, again=0, reason=%s "
                          "WHERE pair_key=%s AND status='pending'", (_now(), reason, k))
        n = getattr(cur, "rowcount", None)
    return bool(n and n > 0)


def reset_stale(minutes=90, db_path="pox.db"):
    """作っている途中のまま止まった組（入れ物が止まった・再起動した）を、予定に戻す。戻した数を返す。"""
    cut = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with _connect(db_path) as con:
        n = con.execute("SELECT COUNT(*) FROM explanation_queue WHERE status='running' AND started_at<%s", (cut,)).fetchone()[0]
        con.execute("UPDATE explanation_queue SET status='pending', reason='stale' WHERE status='running' AND started_at<%s", (cut,))
    return n


def row(k, db_path="pox.db"):
    with _connect(db_path) as con:
        r = con.execute("SELECT pair_key, a_subject, b_subject, status, attempts, again, reason, inputs_digest, record_json "
                        "FROM explanation_queue WHERE pair_key=%s", (k,)).fetchone()
    if not r:
        return None
    return {"pair_key": r[0], "a": r[1], "b": r[2], "status": r[3], "attempts": r[4], "again": bool(r[5]), "reason": r[6],
            "inputs_digest": r[7], "record": json.loads(r[8]) if r[8] else None}


def counts(db_path="pox.db"):
    with _connect(db_path) as con:
        rows = con.execute("SELECT status, COUNT(*) FROM explanation_queue GROUP BY status").fetchall()
    return {s: n for s, n in rows}


def start_batch(backend, db_path="pox.db"):
    bid = f"xb_{uuid.uuid4().hex[:16]}"
    with _connect(db_path) as con:
        con.execute("INSERT INTO explanation_batches (id, backend, started_at) VALUES (%s, %s, %s)", (bid, backend, _now()))
    return bid


def finish_batch(bid, pairs, made, record, db_path="pox.db"):
    with _connect(db_path) as con:
        con.execute("UPDATE explanation_batches SET finished_at=%s, pairs=%s, made=%s, record_json=%s WHERE id=%s",
                    (_now(), pairs, made, json.dumps(record, ensure_ascii=False, sort_keys=True), bid))


def last_batch_started(backend, db_path="pox.db"):
    """その推論先の、最後のまとめて作る回の始まり（ISO の文字列）。無ければ None。"""
    with _connect(db_path) as con:
        r = con.execute("SELECT MAX(started_at) FROM explanation_batches WHERE backend=%s", (backend,)).fetchone()
    return r[0] if r else None


# ── 費用のかかるジョブの起動の数（指示書84 §1-3）──────────────────────────────────────────────
def jst_day_month(now=None):
    """日本の暦の日（YYYY-MM-DD）と月（YYYY-MM）。"""
    t = (now or datetime.now(timezone.utc)).astimezone(JST)
    return t.strftime("%Y-%m-%d"), t.strftime("%Y-%m")


def record_launch(backend, batch_id=None, now=None, db_path="pox.db"):
    """ジョブを起動する前に 1 行書く。戻り値は行の id（ジョブの番号は後で set_launch で足す）。"""
    lid = f"xl_{uuid.uuid4().hex[:16]}"
    t = now or datetime.now(timezone.utc)
    day, month = jst_day_month(t)
    with _connect(db_path) as con:
        con.execute("INSERT INTO explanation_job_launches (id, backend, launched_at, day, month, batch_id) VALUES (%s, %s, %s, %s, %s, %s)",
                    (lid, backend, t.isoformat(timespec="seconds"), day, month, batch_id))
    return lid


def set_launch(lid, job_id=None, record=None, db_path="pox.db"):
    """起動の行に、ジョブの番号と数（待った秒・動いた秒・費用の見積もり・止まり方）を足す。"""
    with _connect(db_path) as con:
        con.execute("UPDATE explanation_job_launches SET job_id=%s, record_json=%s WHERE id=%s",
                    (job_id, json.dumps(record, ensure_ascii=False, sort_keys=True) if record is not None else None, lid))


def launches(backend, now=None, db_path="pox.db"):
    """その推論先の起動の数：{"today": 日本の暦の今日, "month": 今月}。"""
    day, month = jst_day_month(now)
    with _connect(db_path) as con:
        d = con.execute("SELECT COUNT(*) FROM explanation_job_launches WHERE backend=%s AND day=%s", (backend, day)).fetchone()[0]
        m = con.execute("SELECT COUNT(*) FROM explanation_job_launches WHERE backend=%s AND month=%s", (backend, month)).fetchone()[0]
    return {"today": int(d), "month": int(m)}
