#!/usr/bin/env python3
"""
PoX 台帳 — 接続の記録（指示書17 §4-5 で RMW を解体）。

旧実装は vessels.vessel_json を丸ごと読み書き（read-modify-write）しており、
これが追記専用化の唯一かつ中心の障害だった。本実装では:

  - 片方向の承認（成立の前段）は通常DB connection_requests に置く（削除自由・§1-3/§5-7）。
  - 二方向が揃った瞬間だけ、追記専用イベント connection.established を台帳に載せる。
  - 接続の終了は connection.closed を載せる。
  - 接続状態（成立・終了・承認集合・スナップショット結合）は **イベント列から導出** する。
    vessel_json への書き込みは全廃（§4-5）。

closed_at の是正（§4-6）: 成立時に closed_at を埋めない。closed_at は
connection.closed が確定した時にのみ入る。

導出結果はフロントエンドが従来使ってきた vessel 形（vessel_id / founder /
is_connected / joins[0]{joiner, approvals, established_at, terminal_state,
closed_at} / snapshots）に合わせて返す（契約は不変）。移行前の旧 vessels 行は
読み取り専用でフォールバック表示する（データを失わない・§3-4 の移行は別途）。
"""
import json
import uuid
from datetime import datetime, timezone

from db_connect import get_connection, is_postgres
import ledger_events as le


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vessel_id(a: str, b: str) -> str:
    return "v_" + "_".join(sorted([a, b]))


def _is_grounded(payload: dict) -> bool:
    """接続の根拠が内容で固定されているか（指示書18 §1-4）。

    是正後（新形）: 両者の a_ref/b_ref.profile_snapshot_hash が非 null（＝profile.structured の
    content_hash）。necessity_hash は null 可（必要像が無い主体もあるため）。
    是正前（旧形）: necessity_hash が空文字 "" という sentinel を持つ（新形は "" を書かない）。
    """
    a_ref = payload.get("a_ref") or {}
    b_ref = payload.get("b_ref") or {}
    if a_ref.get("necessity_hash") == "" or b_ref.get("necessity_hash") == "":
        return False   # 旧形（是正前）＝根拠が固定されていない
    return bool(a_ref.get("profile_snapshot_hash")) and bool(b_ref.get("profile_snapshot_hash"))


def grounding_report(db_path: str = "pox.db") -> dict:
    """connection.established の件数と、根拠あり/なしの内訳（指示書18 §9-3）。"""
    total = grounded = ungrounded = 0
    for e in le.get_events(type_="connection.established", db_path=db_path):
        total += 1
        if _is_grounded(e["payload"]):
            grounded += 1
        else:
            ungrounded += 1
    return {"total": total, "grounded": grounded, "ungrounded": ungrounded}


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS connection_requests ("
            "id TEXT PRIMARY KEY, from_subject TEXT NOT NULL, to_subject TEXT NOT NULL, "
            "necessity_id TEXT, status TEXT NOT NULL DEFAULT 'pending', "
            "created_at TEXT NOT NULL, responded_at TEXT)"
        )
        # チャネル来歴（指示書18 §3）。台帳に載せず通常DBに保持（照合の内部値・削除可能）。
        for col in ("predicted_role", "channel", "match_run_id"):
            try:
                con.execute(f"ALTER TABLE connection_requests ADD COLUMN {col} TEXT")
            except Exception:  # noqa: BLE001（既存なら無視）
                pass
        # 旧接続データの読み取り互換のため（書き込みはしない）。
        con.execute(
            "CREATE TABLE IF NOT EXISTS vessels "
            "(vessel_id TEXT PRIMARY KEY, vessel_json TEXT NOT NULL)"
        )
        con.commit()
    return con


# ── 承認 → 成立 ────────────────────────────────────────────────────────────

def approve(
    from_id: str,
    to_id: str,
    match_run_id: str = None,
    predicted_role: str = None,
    channel: str = None,
    phase: str = None,
    db_path: str = "pox.db",
    establish_hook=None,
    ref_resolver=None,
    require_grounding: bool = False,
) -> dict:
    """
    from_id が to_id を承認する（成立の前段は connection_requests）。
    双方向が揃うと connection.established を台帳へ追記して成立させる。

    指示書18 §1: 接続の根拠を内容で固定する。
      - ref_resolver(subject) -> {"profile_snapshot_hash", "necessity_hash"} を渡すと、
        a_ref/b_ref をその値（profile.structured の content_hash / necessity.published の
        event_hash・無ければ None）で埋める。未指定なら両方 None（テスト/レガシー）。
      - require_grounding=True かつ、いずれかの profile_snapshot_hash が None なら
        **成立させない**（根拠のない接続を作らない・§1-3）。申請は pending のまま残り、
        両者に profile.structured が揃った後の再承認で成立する。

    §3: predicted_role / channel / match_run_id は台帳に載せず connection_requests に保持。
    戻り値: {"vessel_id", "established": bool[, "reason"]}。
    """
    vid = _vessel_id(from_id, to_id)

    # すでに成立済み（active）なら冪等に返す（§5-6: active な間は1件のみ）。
    if _active_established(from_id, to_id, db_path=db_path):
        return {"vessel_id": vid, "established": True}

    with _connect(db_path) as con:
        # この向きの承認を記録（重複させない）。チャネル来歴も保持（§3）。
        row = con.execute(
            "SELECT id FROM connection_requests WHERE from_subject=%s AND to_subject=%s "
            "AND status='pending'",
            (from_id, to_id),
        ).fetchone()
        if not row:
            con.execute(
                "INSERT INTO connection_requests "
                "(id, from_subject, to_subject, necessity_id, status, created_at, responded_at, "
                " predicted_role, channel, match_run_id) "
                "VALUES (%s,%s,%s,%s,'pending',%s,NULL,%s,%s,%s)",
                (f"cr_{uuid.uuid4().hex[:10]}", from_id, to_id, None, _now(),
                 predicted_role, channel, match_run_id),
            )
        recip = con.execute(
            "SELECT created_at FROM connection_requests WHERE from_subject=%s AND to_subject=%s "
            "AND status='pending'",
            (to_id, from_id),
        ).fetchone()
        mine = con.execute(
            "SELECT created_at FROM connection_requests WHERE from_subject=%s AND to_subject=%s "
            "AND status='pending'",
            (from_id, to_id),
        ).fetchone()

    if not recip:
        return {"vessel_id": vid, "established": False}

    # ── 双方向が揃った ── 根拠の解決とゲート（§1）。
    a, b = sorted([from_id, to_id])
    at_from = mine[0] if mine else _now()
    at_recip = recip[0]
    founder = to_id if at_recip <= at_from else from_id   # 先に承認した側が起点
    other = b if founder == a else a

    def _resolve(x):
        if ref_resolver is None:
            return {"profile_snapshot_hash": None, "necessity_hash": None}
        try:
            r = ref_resolver(x) or {}
        except Exception:  # noqa: BLE001
            r = {}
        return {"profile_snapshot_hash": r.get("profile_snapshot_hash"),
                "necessity_hash": r.get("necessity_hash")}

    a_ref, b_ref = _resolve(a), _resolve(b)
    # §1-3: 新規接続は両者に profile.structured（根拠）が要る。無ければ成立させない。
    if require_grounding and (a_ref["profile_snapshot_hash"] is None
                              or b_ref["profile_snapshot_hash"] is None):
        return {"vessel_id": vid, "established": False, "reason": "missing_profile_structured"}

    # スナップショット結合（指示書12・timeline 用）。a_ref/b_ref とは別の情報。
    snaps = None
    if establish_hook is not None:
        try:
            snaps = establish_hook(founder, other)
        except Exception:  # noqa: BLE001
            snaps = None
    snaps = snaps or {}

    as_of_seq = (le.get_last_event(db_path=db_path) or {}).get("seq", 0)
    payload = {
        "a": a, "b": b, "initiator": founder,
        "a_ref": a_ref, "b_ref": b_ref,           # §1: content_hash / event_hash（無ければ null）
        "as_of_seq": as_of_seq,
        "approved_at": {a: {from_id: at_from, to_id: at_recip}.get(a, ""),
                        b: {from_id: at_from, to_id: at_recip}.get(b, "")},
        "contributions": [
            {"actor": founder, "role": "founded"},
            {"actor": other, "role": "approved"},
        ],
        "snapshots": {k: v for k, v in snaps.items() if v} or None,   # timeline 用（指示書12）
    }
    le.append_event(from_id, "connection.established", payload, db_path=db_path)

    # 前段の申請は成立に畳む（通常DB・削除自由）。
    with _connect(db_path) as con:
        con.execute(
            "UPDATE connection_requests SET status='established', responded_at=%s "
            "WHERE (from_subject=%s AND to_subject=%s) OR (from_subject=%s AND to_subject=%s)",
            (_now(), from_id, to_id, to_id, from_id),
        )
    return {"vessel_id": vid, "established": True}


def close_connection(a: str, b: str, by: str, reason: str = "", db_path: str = "pox.db") -> dict:
    """接続を終了する（connection.closed を追記）。理由は事実として残すが判定はしない。"""
    if not _active_established(a, b, db_path=db_path):
        return {"vessel_id": _vessel_id(a, b), "closed": False}
    aa, bb = sorted([a, b])
    le.append_event(by, "connection.closed",
                    {"a": aa, "b": bb, "by": by, "reason": reason}, db_path=db_path)
    return {"vessel_id": _vessel_id(a, b), "closed": True}


# ── 導出（イベント列 → vessel 形）───────────────────────────────────────────

def _derive_from_events(db_path: str = "pox.db") -> dict:
    """connection.* イベントを vessel 形へ導出。vessel_id -> vessel dict。"""
    out: dict[str, dict] = {}
    for e in le.get_events(db_path=db_path):
        t = e["type"]
        if t not in ("connection.established", "connection.closed"):
            continue
        p = e["payload"]
        a, b = p["a"], p["b"]
        vid = _vessel_id(a, b)
        if t == "connection.established":
            founder = p.get("initiator") or a
            joiner = b if founder == a else a
            appr = p.get("approved_at") or {}
            approvals = [
                {"from": a, "to": b, "at": appr.get(a, "")},
                {"from": b, "to": a, "at": appr.get(b, "")},
            ]
            opened_candidates = [x for x in appr.values() if x] or [e["at"]]
            vessel = {
                "schema_version": "1.0",
                "vessel_id": vid,
                "founder": founder,
                "is_connected": True,
                "membership_count": 2,
                "joins": [{
                    "join_id": "j_" + vid[2:12],
                    "joiner": joiner,
                    "opened_at": min(opened_candidates),
                    "approvals": approvals,
                    "established_at": e["at"],
                    "terminal_state": "active",
                    "closed_at": None,     # §4-6: 成立では閉じない
                    "contributions": p.get("contributions") or [],
                }],
            }
            # timeline 用スナップショット結合（指示書12）: 新形は payload["snapshots"]、
            # 旧形（是正前）は a_ref/b_ref に snapshot_id が入っていたのでそこから拾う。
            snaps = p.get("snapshots")
            if not snaps:
                snaps = {k: v for k, v in (
                    (a, (p.get("a_ref") or {}).get("profile_snapshot_hash")),
                    (b, (p.get("b_ref") or {}).get("profile_snapshot_hash")),
                ) if v}
            if snaps:
                vessel["snapshots"] = snaps
            vessel["grounded"] = _is_grounded(p)     # §1-4: 根拠あり/なしを識別可能に
            out[vid] = vessel
        elif t == "connection.closed":
            v = out.get(vid)
            if v:
                j = v["joins"][0]
                j["terminal_state"] = "dissolved"
                j["closed_at"] = e["at"]
    return out


def _pending_from_requests(db_path: str = "pox.db") -> dict:
    """connection_requests（pending）を vessel 形（未成立）へ導出。"""
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT from_subject, to_subject, created_at FROM connection_requests "
            "WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()
    by_vid: dict[str, list] = {}
    for frm, to, at in rows:
        by_vid.setdefault(_vessel_id(frm, to), []).append((frm, to, at))
    out: dict[str, dict] = {}
    for vid, reqs in by_vid.items():
        founder = reqs[0][0]                       # 最先の申請者
        other = reqs[0][1]
        approvals = [{"from": f, "to": t, "at": at} for (f, t, at) in reqs]
        out[vid] = {
            "schema_version": "1.0",
            "vessel_id": vid,
            "founder": founder,
            "is_connected": False,
            "membership_count": 1,
            "joins": [{
                "join_id": "j_" + vid[2:12],
                "joiner": other,
                "opened_at": reqs[0][2],
                "approvals": approvals,
                "established_at": None,
                "terminal_state": "active",
                "closed_at": None,
                "contributions": [{"actor": founder, "role": "founded"}],
            }],
        }
    return out


def _legacy_vessels(db_path: str = "pox.db") -> dict:
    """移行前の旧 vessels 行を読み取り専用で返す（書き込みはしない・§3-4）。"""
    try:
        with _connect(db_path) as con:
            rows = con.execute("SELECT vessel_json FROM vessels").fetchall()
        return {v["vessel_id"]: v for v in (json.loads(r[0]) for r in rows)}
    except Exception:  # noqa: BLE001
        return {}


def _active_established(a: str, b: str, db_path: str = "pox.db") -> bool:
    vid = _vessel_id(a, b)
    v = _derive_from_events(db_path=db_path).get(vid)
    return bool(v and v["is_connected"] and v["joins"][0]["closed_at"] is None)


def load_all_vessels(db_path: str = "pox.db") -> list[dict]:
    """全 vessel を vessel_id 順で返す（イベント導出 ＋ 未成立の申請 ＋ 旧行フォールバック）。"""
    merged = dict(_derive_from_events(db_path=db_path))
    for vid, v in _pending_from_requests(db_path=db_path).items():
        if vid not in merged:
            merged[vid] = v
    for vid, v in _legacy_vessels(db_path=db_path).items():
        if vid not in merged:
            merged[vid] = v
    return [merged[k] for k in sorted(merged)]
