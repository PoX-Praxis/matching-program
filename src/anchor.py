#!/usr/bin/env python3
"""
PoX 台帳 — 日次 root の計算（指示書17 §6）。

アンカーは遡れない。root を計算していない期間は永久に保証できない。一方、外部
固定（external_ref）は後からまとめて固定できる。だからアンカー先の選定を待たず
root の計算だけ先に始める（§0）。

- 1日1回、その日の全イベントハッシュ（anchor.published を除く）と、その日の
  是認ログのエントリの正準化ハッシュから Merkle root を計算し、anchor.published
  を台帳へ追記する。external_ref は null のまま（§6-1）。
- イベントがゼロの日も空 root を記録する（飛ばしを検出可能にする・§6-1）。
- prev_anchor で連結する（アンカー自体も鎖に乗る）。
- Merkle 実装にライブラリは不要（ハッシュを対にして畳むだけ）。

検証（§6-2）:
  root = MerkleRoot( [その日の event_hash 昇順] + [その日の attestation 正準化ハッシュ] )
是認ログを含めるのは、含めないと署名を後から捏造・削除できるため。
"""
import hashlib
from datetime import datetime, timezone, date as _date, timedelta

from db_connect import get_connection, is_postgres
from canon import canonicalize, sha256_hex
import ledger_events as le


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS anchors ("
            "anchor_seq INTEGER PRIMARY KEY, date TEXT NOT NULL UNIQUE, "
            "from_seq INTEGER NOT NULL, to_seq INTEGER NOT NULL, root TEXT NOT NULL, "
            "prev_anchor TEXT, external_ref TEXT)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS attestations ("
            "event_hash TEXT NOT NULL, by TEXT NOT NULL, pubkey TEXT NOT NULL, "
            "sig TEXT NOT NULL, at TEXT NOT NULL, level TEXT NOT NULL, "
            "canon_version TEXT NOT NULL, PRIMARY KEY (event_hash, by, pubkey))"
        )
        con.commit()
    return con


def merkle_root(leaf_hashes: list[str]) -> str:
    """16進ハッシュの列から Merkle root を計算する。空なら空文字列の SHA-256。

    奇数個の層は最後の要素を自分自身と対にして畳む（複製）。
    """
    if not leaf_hashes:
        return sha256_hex(b"")
    layer = [bytes.fromhex(h) for h in leaf_hashes]
    while len(layer) > 1:
        nxt = []
        for i in range(0, len(layer), 2):
            a = layer[i]
            b = layer[i + 1] if i + 1 < len(layer) else layer[i]
            nxt.append(hashlib.sha256(a + b).digest())
        layer = nxt
    return layer[0].hex()


def _attestations_on(date: str, db_path: str) -> list[str]:
    """その日の是認ログの正準化ハッシュ（昇順）。現段階は通常空。"""
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT event_hash, by, pubkey, sig, at, level, canon_version "
            "FROM attestations WHERE at LIKE %s", (date + "%",),
        ).fetchall()
    hashes = []
    for r in rows:
        rec = {"event_hash": r[0], "by": r[1], "pubkey": r[2], "sig": r[3],
               "at": r[4], "level": r[5], "canon_version": r[6]}
        hashes.append(sha256_hex(canonicalize(rec)))
    return sorted(hashes)


def _last_anchor(db_path: str):
    with _connect(db_path) as con:
        r = con.execute(
            "SELECT anchor_seq, root FROM anchors ORDER BY anchor_seq DESC LIMIT 1"
        ).fetchone()
    return r  # (anchor_seq, root) or None


def compute_root_for_date(date: str, db_path: str = "pox.db") -> dict:
    """その日のイベント（anchor.published を除く）＋是認ログから root を計算（追記はしない）。"""
    day_events = [e for e in le.get_events(db_path=db_path)
                  if (e["at"] or "").startswith(date) and e["type"] != "anchor.published"]
    seqs = [e["seq"] for e in day_events]
    event_hashes = sorted(e["event_hash"] for e in day_events)
    leaves = event_hashes + _attestations_on(date, db_path)
    return {
        "date": date,
        "from_seq": min(seqs) if seqs else 0,
        "to_seq": max(seqs) if seqs else 0,
        "root": merkle_root(leaves),
        "event_count": len(day_events),
    }


def publish_anchor(date: str = None, db_path: str = "pox.db") -> dict:
    """その日の root を計算し anchor.published を台帳に追記＋anchors 行を記録する。

    同じ日付が既にあれば何もしない（冪等）。イベントゼロの日も空 root を記録する。
    """
    date = date or _today_utc()
    with _connect(db_path) as con:
        exists = con.execute("SELECT 1 FROM anchors WHERE date=%s", (date,)).fetchone()
    if exists:
        return {"date": date, "skipped": True, "reason": "already anchored"}

    info = compute_root_for_date(date, db_path=db_path)
    last = _last_anchor(db_path)
    prev_anchor = last[1] if last else None
    anchor_seq = (last[0] + 1) if last else 1

    # 台帳へ（anchor.published も鎖に乗る）。
    le.append_event(
        "system", "anchor.published",
        {"root": info["root"],
         "scope": {"from_seq": info["from_seq"], "to_seq": info["to_seq"], "date": date},
         "prev_anchor": prev_anchor},
        db_path=db_path,
    )
    # 参照用の anchors 行。external_ref は段階4では null。
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO anchors (anchor_seq, date, from_seq, to_seq, root, prev_anchor, external_ref) "
            "VALUES (%s,%s,%s,%s,%s,%s,NULL)",
            (anchor_seq, date, info["from_seq"], info["to_seq"], info["root"], prev_anchor),
        )
    return {"date": date, "skipped": False, "anchor_seq": anchor_seq,
            "root": info["root"], "from_seq": info["from_seq"], "to_seq": info["to_seq"],
            "prev_anchor": prev_anchor, "event_count": info["event_count"]}


def get_anchor(date: str, db_path: str = "pox.db") -> dict | None:
    with _connect(db_path) as con:
        r = con.execute(
            "SELECT anchor_seq, date, from_seq, to_seq, root, prev_anchor, external_ref "
            "FROM anchors WHERE date=%s", (date,),
        ).fetchone()
    if not r:
        return None
    return {"anchor_seq": r[0], "date": r[1], "from_seq": r[2], "to_seq": r[3],
            "root": r[4], "prev_anchor": r[5], "external_ref": r[6]}


def verify_date(date: str, db_path: str = "pox.db") -> dict:
    """保存済みアンカーの root を、その日のイベントから再計算して照合する。"""
    stored = get_anchor(date, db_path=db_path)
    recomputed = compute_root_for_date(date, db_path=db_path)
    if not stored:
        return {"date": date, "anchored": False, "match": None,
                "recomputed_root": recomputed["root"]}
    return {
        "date": date, "anchored": True,
        "match": stored["root"] == recomputed["root"],
        "root": stored["root"], "recomputed_root": recomputed["root"],
        "from_seq": stored["from_seq"], "to_seq": stored["to_seq"],
        "prev_anchor": stored["prev_anchor"], "external_ref": stored["external_ref"],
    }


# ── 日次バッチ（1日1回・欠けた日はバックフィル）────────────────────────────────

def _last_anchor_date(db_path: str = "pox.db"):
    with _connect(db_path) as con:
        r = con.execute("SELECT date FROM anchors ORDER BY date DESC LIMIT 1").fetchone()
    return r[0] if r else None


def _earliest_event_date(db_path: str = "pox.db"):
    ev = le.get_events(db_path=db_path)
    if not ev:
        return None
    return (ev[0]["at"] or "")[:10] or None


def _yesterday_utc() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


def run_daily(db_path: str = "pox.db", up_to: str = None, max_days: int = 400) -> dict:
    """**完了した日**を up_to まで順に publish する（§6）。

    up_to は「アンカーしてよい最後の完了日」で、既定は **前日（UTC）**。当日を含めない
    のは、当日はまだイベントが増えうるため root が後から変わり verify が壊れるから。
    バッチが日次で回れば、常に「昨日まで」を確定させ、今日は明日確定する（1日遅れ・正しい）。

    最後のアンカーの翌日から up_to まで**連続して**（空の日も空 root で）記録するので、
    バッチが数日落ちても gap ができない（§6-1）。初回（アンカー皆無）は最古イベントの日から、
    イベントも無ければ up_to 1日ぶん。冪等: 既にある日は publish_anchor 側でスキップ。
    """
    up_to = up_to or _yesterday_utc()
    end = _date.fromisoformat(up_to)
    last = _last_anchor_date(db_path)
    if last:
        start = _date.fromisoformat(last) + timedelta(days=1)
    else:
        e = _earliest_event_date(db_path)
        start = _date.fromisoformat(e) if e else end

    results, d, guard = [], start, 0
    while d <= end and guard < max_days:
        results.append(publish_anchor(d.isoformat(), db_path=db_path))
        d += timedelta(days=1)
        guard += 1
    return {"from": start.isoformat(), "to": end.isoformat(),
            "count": len(results), "results": results}
