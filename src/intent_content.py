#!/usr/bin/env python3
"""
PoX 意志形成の本文置き場（指示書23 §1-5・§3-1）。

台帳（ledger_events）には本文を載せず content_hash のみを畳む（指示書17 §4-2・禁則）。
しかし実績の「可読性」——誰と誰が・いつ・何を完了させたか——のために、本文（body・
宣言・結果）を **DB に平文で** 置く。台帳が事実の骨格（イベント鎖＋アンカー）を保証し、
本文は請求で削除されても骨格は残る、という二層構造（§0）を成り立たせるための置き場。

- ここは「台帳ではない」。単一ライター（append_event）の制約は無い。
- 宣言（declaration）は種別つき構造（§1-2）: kind=policy（全体方針）/ recruit（目的別募集）。
  種別と本文フィールドを JSON で保持する。
"""
import json
from datetime import datetime, timezone

from db_connect import get_connection, is_postgres


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS intent_content ("
            "intent_id TEXT PRIMARY KEY, ctx TEXT NOT NULL, "
            "body TEXT, declaration_json TEXT, result TEXT, updated_at TEXT NOT NULL)"
        )
        con.commit()
    return con


def save_proposal(intent_id: str, ctx: str, body: str, declaration, db_path: str = "pox.db"):
    """提起時の本文と宣言（構造）を保存する。declaration は dict か文字列か None。
    提起は intent_id につき1回だが、再送に備え read-then-write で冪等に扱う（result は保持）。"""
    decl_json = json.dumps(normalize_declaration(declaration), ensure_ascii=False)
    with _connect(db_path) as con:
        exists = con.execute("SELECT 1 FROM intent_content WHERE intent_id=%s", (intent_id,)).fetchone()
        if exists:
            con.execute(
                "UPDATE intent_content SET body=%s, declaration_json=%s, updated_at=%s WHERE intent_id=%s",
                (body or "", decl_json, _now(), intent_id))
        else:
            con.execute(
                "INSERT INTO intent_content (intent_id, ctx, body, declaration_json, result, updated_at) "
                "VALUES (%s,%s,%s,%s,NULL,%s)",
                (intent_id, ctx, body or "", decl_json, _now()))
    return decl_json


def save_result(intent_id: str, result: str, db_path: str = "pox.db"):
    """完了時の結果本文を保存する。"""
    with _connect(db_path) as con:
        con.execute("UPDATE intent_content SET result=%s, updated_at=%s WHERE intent_id=%s",
                    (result or "", _now(), intent_id))


def get_content(intent_id: str, db_path: str = "pox.db") -> dict:
    with _connect(db_path) as con:
        r = con.execute(
            "SELECT intent_id, ctx, body, declaration_json, result FROM intent_content "
            "WHERE intent_id=%s", (intent_id,),
        ).fetchone()
    if not r:
        return {}
    return {"intent_id": r[0], "ctx": r[1], "body": r[2] or "",
            "declaration": json.loads(r[3]) if r[3] else None, "result": r[4] or ""}


def normalize_declaration(declaration) -> dict | None:
    """宣言を種別つき構造に正規化する（§1-2）。

    - None / 空文字 → None（宣言なし）
    - 文字列 → {"kind": None, "text": ...}（後方互換・種別なしの自由記述）
    - dict → kind を policy/recruit/None に丸め、該当フィールドのみ残す
    """
    if declaration is None:
        return None
    if isinstance(declaration, str):
        return {"kind": None, "text": declaration} if declaration.strip() else None
    if not isinstance(declaration, dict):
        return None
    kind = declaration.get("kind")
    if kind == "policy":
        out = {"kind": "policy"}
        for k in ("will_text", "state_have", "state_can_type", "state_bound", "state_unsorted"):
            out[k] = str(declaration.get(k) or "")
        # 何も入っていなければ宣言なし扱い
        if not any(out[k] for k in out if k != "kind"):
            return None
        return out
    if kind == "recruit":
        will = str(declaration.get("will_text") or "")
        nec = str(declaration.get("necessity_text") or "")
        if not (will or nec):
            return None
        return {"kind": "recruit", "will_text": will, "necessity_text": nec}
    # ── コミュニティ版①（指示書28 §4）。手書き(recruit/policy)と違い、必要像は
    #    生成物（origin=generated・クランプしない）で、数値素材・生成元・試行回数を伴う。
    if kind == "community_overall":
        out = {"kind": "community_overall"}
        for k in ("will_text", "state_have", "state_can_type", "state_bound", "state_unsorted"):
            out[k] = str(declaration.get(k) or "")
        out["necessity"] = _normalize_necessity_block(declaration.get("necessity"))
        if not (any(out[k] for k in out if k not in ("kind", "necessity")) or out["necessity"]):
            return None
        return out
    if kind == "intent_necessity":
        purpose = str(declaration.get("purpose_text") or "")
        nb = _normalize_necessity_block(declaration.get("necessity"))
        if not (purpose or nb):
            return None
        return {"kind": "intent_necessity", "purpose_text": purpose, "necessity": nb}
    # kind 無し dict は自由記述として扱う
    txt = str(declaration.get("text") or "")
    return {"kind": None, "text": txt} if txt.strip() else None


_NEC_NUM_KEYS = ("gate_s", "gate_u", "p_sharpness", "alpha", "beta")


def _normalize_necessity_block(nec) -> dict | None:
    """コミュニティ①の必要像ブロックを正準化（数値・seeking・生成元・試行回数を保持）。

    数値は float/int のみ通す（不正は None）。content_hash に載る値なので決定的に。
    """
    if not isinstance(nec, dict):
        return None
    out = {"necessity_text": str(nec.get("necessity_text") or ""),
           "evidence_span": str(nec.get("evidence_span") or ""),
           "seeking": str(nec.get("seeking") or nec.get("求めている") or "")}
    for k in _NEC_NUM_KEYS:
        v = nec.get(k)
        out[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    # 生成元・試行回数（ピン留め用・§3-1/§3-2）
    out["generator"] = str(nec.get("generator") or "")
    out["generator_tag"] = str(nec.get("generator_tag") or "")
    src = nec.get("source_snapshot_hash")
    out["source_snapshot_hash"] = str(src) if src else ""
    an = nec.get("attempt_n")
    out["attempt_n"] = int(an) if isinstance(an, int) and not isinstance(an, bool) else None
    if not out["necessity_text"]:
        return None
    return out
