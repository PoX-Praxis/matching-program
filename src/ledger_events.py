#!/usr/bin/env python3
"""
PoX 接続台帳 v2 — 追記専用イベント列（指示書17 §4）。

台帳の唯一の書き込み経路は append_event() のみ。他のどこからも ledger_events へ
直接 INSERT しない（§4-4）。seq の採番と prev_hash の連結は単一の書き込み経路が
直列に行う:

  - プロセス内は threading.Lock で直列化。
  - Postgres（本番・複数ワーカー）では pg_advisory_xact_lock で全ワーカーを
    跨いで直列化する（advisory lock はトランザクション終端で解放）。
  - マルチノード化が必要になったら canon_version を上げて別方式へ移る（今は決めない）。

共通6フィールド（§4-1）: seq / at / actor / type / prev_hash / canon_version。
attestation は共通フィールドに置かない（§1-7・§4-1）。署名は attestations テーブルへ
0〜n 本ぶら下がるだけ。

event_hash = SHA256(JCS(共通6フィールド + payload))（§4-2）。
"""
import json
import threading
from datetime import datetime, timezone

from db_connect import get_connection, is_postgres
from canon import canonicalize, sha256_hex, CANON_VERSION

# PoX 台帳専用の固定 advisory キー（他用途と衝突しない任意の定数）。
_ADVISORY_KEY = 906734
_write_lock = threading.Lock()

SYSTEM_ACTOR = "system"   # 自動発生イベントの予約 actor（§4-1）


def _now_ms() -> str:
    """UTC ISO 8601 ミリ秒（サーバー記録時刻。防御の根拠にはしない・§4-1）。"""
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS ledger_events ("
            "seq INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, "
            "type TEXT NOT NULL, prev_hash TEXT, canon_version TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE)"
        )
        con.commit()
    return con


def _reject_floats(obj, path="payload"):
    """payload に float が混ざっていたら例外（指示書18 §6-1）。

    正準化は JCS 実用サブセットで、実数の ECMAScript 直列化に非対応。将来 payload に
    実数が入ると静かにハッシュが揺れるため、書き込み時点で弾く。bool は int の一種だが
    許容（True/False は JSON でも安定）。数値素材は necessities 側の列に置き台帳へは
    content_hash として畳む設計（§7-2）なので、台帳イベントに実数は不要。
    """
    if isinstance(obj, float):
        raise ValueError(f"台帳 payload に float は不可（{path}）。数値は content_hash に畳むこと")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _reject_floats(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _reject_floats(v, f"{path}[{i}]")


def append_event(actor: str, type_: str, payload: dict, db_path: str = "pox.db") -> dict:
    """台帳への唯一の書き込み経路。event_hash を返す（§4-4）。

    seq は (MAX(seq)+1) を採番し、prev_hash は直前イベントの event_hash を連結する。
    最初の1件のみ prev_hash=null。payload は正準化して保存し、再計算で検証できる。
    """
    if not isinstance(payload, dict):
        raise ValueError("payload は dict でなければならない")
    _reject_floats(payload)   # 指示書18 §6-1: JCS 実用サブセットは実数を扱わない
    with _write_lock:
        with _connect(db_path) as con:
            if is_postgres():
                con.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_KEY,))
            row = con.execute(
                "SELECT seq, event_hash FROM ledger_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if row:
                seq = row[0] + 1
                prev_hash = row[1]
            else:
                seq = 1
                prev_hash = None
            at = _now_ms()
            body = {
                "seq": seq, "at": at, "actor": actor, "type": type_,
                "prev_hash": prev_hash, "canon_version": CANON_VERSION,
                "payload": payload,
            }
            event_hash = sha256_hex(canonicalize(body))
            # payload は正準化した形で保存（再ハッシュが再現可能になる）。
            payload_json = canonicalize(payload).decode("utf-8")
            con.execute(
                "INSERT INTO ledger_events "
                "(seq, at, actor, type, prev_hash, canon_version, payload_json, event_hash) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (seq, at, actor, type_, prev_hash, CANON_VERSION, payload_json, event_hash),
            )
    return {"seq": seq, "at": at, "event_hash": event_hash, "prev_hash": prev_hash}


def _row_to_event(r) -> dict:
    return {
        "seq": r[0], "at": r[1], "actor": r[2], "type": r[3],
        "prev_hash": r[4], "canon_version": r[5],
        "payload": json.loads(r[6]), "event_hash": r[7],
    }


def get_events(type_: str = None, actor: str = None, db_path: str = "pox.db") -> list[dict]:
    """seq 昇順でイベントを返す。type_/actor で絞れる。"""
    q = ("SELECT seq, at, actor, type, prev_hash, canon_version, payload_json, event_hash "
         "FROM ledger_events")
    conds, args = [], []
    if type_ is not None:
        conds.append("type = %s"); args.append(type_)
    if actor is not None:
        conds.append("actor = %s"); args.append(actor)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY seq ASC"
    with _connect(db_path) as con:
        rows = con.execute(q, tuple(args)).fetchall()
    return [_row_to_event(r) for r in rows]


def get_last_event(db_path: str = "pox.db") -> dict | None:
    with _connect(db_path) as con:
        r = con.execute(
            "SELECT seq, at, actor, type, prev_hash, canon_version, payload_json, event_hash "
            "FROM ledger_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    return _row_to_event(r) if r else None


def verify_chain(db_path: str = "pox.db") -> dict:
    """鎖の整合を検証する: prev_hash の連結・seq の連番・event_hash の再計算。

    戻り値 {"ok": bool, "count": int, "error": str|None, "seq": int|None}。
    """
    events = get_events(db_path=db_path)
    prev = None
    expected_seq = 1
    for e in events:
        if e["seq"] != expected_seq:
            return {"ok": False, "count": len(events), "seq": e["seq"],
                    "error": f"seq 連番が壊れている（期待 {expected_seq}）"}
        if e["prev_hash"] != prev:
            return {"ok": False, "count": len(events), "seq": e["seq"],
                    "error": "prev_hash の連結が壊れている"}
        body = {"seq": e["seq"], "at": e["at"], "actor": e["actor"], "type": e["type"],
                "prev_hash": e["prev_hash"], "canon_version": e["canon_version"],
                "payload": e["payload"]}
        if sha256_hex(canonicalize(body)) != e["event_hash"]:
            return {"ok": False, "count": len(events), "seq": e["seq"],
                    "error": "event_hash の再計算が一致しない"}
        prev = e["event_hash"]
        expected_seq += 1
    return {"ok": True, "count": len(events), "seq": prev and events[-1]["seq"], "error": None}


def next_n(owner_key_col: str, owner_value: str, type_: str, db_path: str = "pox.db") -> int:
    """同一 owner の単調カウンタ n（§5-2）の次値を返す。1 始まり。

    payload_json 内の owner_key_col が owner_value に一致する type_ イベント数 + 1。
    追記は append_event 内の単一ライター下で行うため、採番→追記の間に他の書き込みは
    割り込まない（呼び出し側で append_event と同一の直列区間に入れること）。
    """
    events = get_events(type_=type_, db_path=db_path)
    count = sum(1 for e in events if e["payload"].get(owner_key_col) == owner_value)
    return count + 1
