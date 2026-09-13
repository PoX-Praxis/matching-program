#!/usr/bin/env python3
"""
PoX 宣言の下書き（指示書28 段階1）。

確定するまで台帳に載せない「下書き」を通常DBに置く。第三者に見えず、削除自由。
- 個人: 本人が「確定」するまで editing のまま。確定で台帳へ（profile.structured +
  necessity.published）＋ベクトル化。
- コミュニティ: 提起（proposed）→メンバー合意（confirmed）で台帳へ（段階3）。

attempt_n（指示書28 §3-2）:
  同じ下書き（= 同一 subject_id × owner_kind × target_intent_id）に JSON が貼られた
  通算回数。初回は 1。手直し・拒否で貼り直すたびに +1（破棄分を含む）。破棄した試行の
  「内容」は残さない（回数だけ）。確定時にこの値を necessity.published に載せる（段階2）。

status: 'editing' | 'proposed' | 'confirmed' | 'rejected'。

台帳には一切書かない（確定/合意のときに app 側が別途 publish する）。
"""
import json
import uuid
from datetime import datetime, timezone

from db_connect import get_connection, is_postgres


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS declaration_drafts ("
            "draft_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, owner_kind TEXT NOT NULL, "
            "target_intent_id TEXT, payload_json TEXT NOT NULL, attempt_n INTEGER NOT NULL, "
            "status TEXT NOT NULL, reject_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        con.commit()
    return con


_COLS = ("draft_id", "subject_id", "owner_kind", "target_intent_id",
         "payload_json", "attempt_n", "status", "reject_json", "created_at", "updated_at")


def _row_to_dict(r):
    if not r:
        return None
    d = dict(zip(_COLS, r))
    d["payload"] = json.loads(d.pop("payload_json"))
    d["rejections"] = json.loads(d["reject_json"]) if d.get("reject_json") else []
    d.pop("reject_json", None)
    return d


def _select(con, where, args):
    return con.execute(
        "SELECT draft_id, subject_id, owner_kind, target_intent_id, payload_json, "
        "attempt_n, status, reject_json, created_at, updated_at "
        "FROM declaration_drafts WHERE " + where, args,
    ).fetchone()


def get_draft(draft_id: str, db_path: str = "pox.db"):
    with _connect(db_path) as con:
        return _row_to_dict(_select(con, "draft_id = %s", (draft_id,)))


def get_active_draft(subject_id: str, owner_kind: str = "subject",
                     target_intent_id=None, db_path: str = "pox.db"):
    """同一 owner の「確定前」下書き（editing/proposed/rejected の最新）を返す。"""
    with _connect(db_path) as con:
        return _row_to_dict(_select(
            con,
            "subject_id = %s AND owner_kind = %s "
            "AND COALESCE(target_intent_id,'') = COALESCE(%s,'') "
            "AND status <> 'confirmed' ORDER BY updated_at DESC LIMIT 1",
            (subject_id, owner_kind, target_intent_id),
        ))


def list_drafts(subject_id: str, db_path: str = "pox.db"):
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT draft_id, subject_id, owner_kind, target_intent_id, payload_json, "
            "attempt_n, status, reject_json, created_at, updated_at "
            "FROM declaration_drafts WHERE subject_id = %s ORDER BY updated_at DESC",
            (subject_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def save_draft(subject_id: str, payload: dict, *, owner_kind: str = "subject",
               target_intent_id=None, db_path: str = "pox.db") -> dict:
    """①出力JSONを下書きに保存する。確定前の下書きがあれば同じ行に上書きし
    attempt_n を +1（貼り直し＝通算回数）。無ければ attempt_n=1 で新規作成。

    確定済み（confirmed）の下書きは対象にしない → 確定後の貼り直しは新しい下書き
    （attempt_n=1）から始まる。手直し中（editing/proposed/rejected）の貼り直しは加算。
    """
    now = _now()
    payload_json = json.dumps(payload, ensure_ascii=False)
    with _connect(db_path) as con:
        existing = _select(
            con,
            "subject_id = %s AND owner_kind = %s "
            "AND COALESCE(target_intent_id,'') = COALESCE(%s,'') "
            "AND status <> 'confirmed' ORDER BY updated_at DESC LIMIT 1",
            (subject_id, owner_kind, target_intent_id),
        )
        if existing:
            d = _row_to_dict(existing)
            attempt_n = d["attempt_n"] + 1
            con.execute(
                "UPDATE declaration_drafts SET payload_json=%s, attempt_n=%s, "
                "status='editing', updated_at=%s WHERE draft_id=%s",
                (payload_json, attempt_n, now, d["draft_id"]),
            )
            draft_id = d["draft_id"]
        else:
            draft_id = f"draft_{uuid.uuid4().hex[:12]}"
            con.execute(
                "INSERT INTO declaration_drafts (draft_id, subject_id, owner_kind, "
                "target_intent_id, payload_json, attempt_n, status, reject_json, "
                "created_at, updated_at) VALUES (%s,%s,%s,%s,%s,1,'editing',NULL,%s,%s)",
                (draft_id, subject_id, owner_kind, target_intent_id, payload_json, now, now),
            )
    return get_draft(draft_id, db_path=db_path)


def set_status(draft_id: str, status: str, db_path: str = "pox.db") -> None:
    if status not in ("editing", "proposed", "confirmed", "rejected"):
        raise ValueError(f"未知の status: {status}")
    with _connect(db_path) as con:
        con.execute(
            "UPDATE declaration_drafts SET status=%s, updated_at=%s WHERE draft_id=%s",
            (status, _now(), draft_id),
        )


def add_rejection(draft_id: str, kind: str, note: str = "", *,
                  by: str = "", db_path: str = "pox.db") -> None:
    """拒否理由を下書きに記録する（台帳には載せない・指示書28 §5-2）。

    kind: 'fact_error'（事実誤認→再生成の入力）/ 'discomfort'（違和感→gate_u 引き上げ）。
    """
    if kind not in ("fact_error", "discomfort"):
        raise ValueError(f"未知の拒否種別: {kind}（fact_error / discomfort）")
    with _connect(db_path) as con:
        row = _select(con, "draft_id = %s", (draft_id,))
        if not row:
            raise ValueError(f"下書きが見つかりません: {draft_id}")
        d = _row_to_dict(row)
        rej = d["rejections"]
        rej.append({"kind": kind, "note": note[:500], "by": by, "at": _now()})
        con.execute(
            "UPDATE declaration_drafts SET reject_json=%s, status='rejected', updated_at=%s "
            "WHERE draft_id=%s",
            (json.dumps(rej, ensure_ascii=False), _now(), draft_id),
        )


def count_rejections(draft: dict, kind: str = None) -> int:
    """下書きの拒否件数（kind 指定でその種別のみ）。"""
    rej = (draft or {}).get("rejections") or []
    return sum(1 for r in rej if kind is None or r.get("kind") == kind)


def gate_u_after_discomfort(gate_u, n_discomfort: int):
    """違和感(discomfort)の件数だけ gate_u を引き上げる（§5-1・決定的）。

    1件につき +0.15、上限 0.9（①の最大ステップ）。fact_error は再生成の入力なので
    ここでは動かさない。gate_u が数値でない/件数0 のときはそのまま返す。
    """
    if not isinstance(gate_u, (int, float)) or isinstance(gate_u, bool) or n_discomfort <= 0:
        return gate_u
    return min(0.9, round(float(gate_u) + 0.15 * n_discomfort, 4))


def delete_draft(draft_id: str, db_path: str = "pox.db") -> None:
    """下書きを削除する（自由・台帳に痕跡を残さない）。"""
    with _connect(db_path) as con:
        con.execute("DELETE FROM declaration_drafts WHERE draft_id=%s", (draft_id,))
