#!/usr/bin/env python3
"""指示書45B — 削除の台帳記録 `redaction.recorded` と、discussion_hash の検証。

発言の本文を伏せると、合意の discussion_hash は再計算しても一致しなくなる。削除の事実を
台帳に残さないと、第三者は**改ざん**と**正規の削除**を区別できない。本モジュールは

  - 削除を確定したときに `redaction.recorded` を 1 件追記する（record_redaction）
  - 現行の本文から discussion_hash を再計算し、台帳の値（最新の削除記録、無ければ合意の
    保存値）と照合する（verify_discussion）

だけを行う。**本文を伏せる操作と申立ての受付経路は作らない**（45B §4。当面は運用者が
通常DBで伏せ、その後 record_redaction を呼ぶ）。

原則（45B §1）:
  - 削除された本文そのものは台帳に入れない（ハッシュと scope_digest のみ）。
  - 申立てをした人物は台帳に書かない（reason_class と scope_digest まで）。
  - 連鎖: 2 回目以降は prev_hash = 直前の redaction.recorded.result_hash。

`content.removed`（指示書17 §5-3）は**実装しない**。削除の記録はこの型だけが担う
（同じ目的の型を台帳に 2 つ置かない・45B §0）。
"""
from datetime import datetime, timezone

import ledger_events as le
import governance as gov
import talks
from canon import canonicalize, sha256_hex

EVENT_TYPE = "redaction.recorded"
HASH_KIND_DISCUSSION = "discussion"      # v1 で記録するのは発言（discussion_hash）のみ
CANON_VERSION = "v1"                     # discussion_hash v1（docs/ledger_limits.md で凍結）
REASON_CLASSES = ("legal", "subject_request")

# legacy の境界（45B §3）: 合意済みトークへの追記を止めた PR #101 が main に入った時刻（UTC）。
# これより前に記録された合意は、合意後の追記が止められていなかったため、再計算が一致しなくても
# 改ざんとは判定しない（台帳の保存値を正とする）。本番台帳の境界 seq は運用側で確定し
# LEGACY_BOUNDARY_SEQ に設定する（設定されていれば seq で判定し、無ければ時刻で判定する）。
LEGACY_BOUNDARY_AT = "2026-09-23T08:49:23.000Z"
LEGACY_BOUNDARY_SEQ = None


def _now_ms() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _event_by_hash(event_hash, db_path):
    for e in le.get_events(db_path=db_path):
        if e["event_hash"] == event_hash:
            return e
    return None


def _target_event(target_ref, db_path):
    ev = _event_by_hash(target_ref, db_path)
    if ev is None:
        raise ValueError("target_ref が台帳に見つかりません")
    if "discussion_hash" not in ev["payload"]:
        raise ValueError("target_ref の合意に discussion_hash がありません")
    return ev


def redactions_for(target_ref, *, db_path="pox.db"):
    """target_ref に掛かる redaction.recorded を台帳の順（seq 昇順）で返す。"""
    return [e for e in le.get_events(type_=EVENT_TYPE, db_path=db_path)
            if e["payload"].get("target_ref") == target_ref]


def is_legacy(agreement_event) -> bool:
    """境界より前に記録された合意か（45B §3）。台帳には何も書かず、境界の定数で判定する。"""
    if LEGACY_BOUNDARY_SEQ is not None:
        return agreement_event["seq"] <= LEGACY_BOUNDARY_SEQ
    return agreement_event["at"] < LEGACY_BOUNDARY_AT


def current_discussion_hash(talk_id, *, db_path="pox.db") -> str:
    """現行の本文から discussion_hash v1 を再計算する。"""
    return gov.discussion_hash(talks._discussion_text(talk_id, db_path))


def scope_digest(post_ids) -> str:
    """削除された範囲の識別子。発言 id の集合の正準化ハッシュ（本文は含めない）。"""
    return sha256_hex(canonicalize(sorted(set(post_ids))))


def record_redaction(talk_id, target_ref, *, redacted_post_ids, reason_class, decided_by,
                     db_path="pox.db") -> dict:
    """本文を伏せた後に呼び、削除の事実を redaction.recorded として台帳に追記する。

    prev_hash は直前の削除記録の result_hash（初回は合意の保存値）、result_hash は現行本文での
    再計算値。申立てた人物は引数にも取らない（台帳に書かないため）。"""
    if reason_class not in REASON_CLASSES:
        raise ValueError(f"reason_class は {REASON_CLASSES} のいずれか")
    if not redacted_post_ids:
        raise ValueError("redacted_post_ids が必要です")
    if not decided_by:
        raise ValueError("decided_by が必要です")
    target = _target_event(target_ref, db_path)
    if target["payload"].get("talk_id") not in (None, talk_id):
        raise ValueError("target_ref の合意は別のトークのものです")
    chain = redactions_for(target_ref, db_path=db_path)
    prev_hash = chain[-1]["payload"]["result_hash"] if chain else target["payload"]["discussion_hash"]
    payload = {
        "talk_id": talk_id,
        "target_ref": target_ref,
        "hash_kind": HASH_KIND_DISCUSSION,
        "canon_version": CANON_VERSION,
        "prev_hash": prev_hash,
        "result_hash": current_discussion_hash(talk_id, db_path=db_path),
        "scope_digest": scope_digest(redacted_post_ids),
        "reason_class": reason_class,
        "decided_by": decided_by,
        "recorded_at": _now_ms(),
    }
    res = le.append_event(decided_by, EVENT_TYPE, payload, db_path=db_path)
    return {**res, "type": EVENT_TYPE, "actor": decided_by, "payload": payload}


def verify_discussion(talk_id, target_ref, *, db_path="pox.db") -> dict:
    """現行本文の discussion_hash を台帳と照合する。

    status:
      intact            … 削除記録なし・合意の保存値と一致
      redacted          … 削除記録あり・連鎖が正しく、最新の result_hash と一致（正規の削除）
      tampered          … 一致しない、または削除記録の連鎖が切れている（改ざん・記録の剥奪）
      legacy_unverified … 境界より前の合意で一致しない（保存値を正とし、改ざんと判定しない）
    """
    target = _target_event(target_ref, db_path)
    stored = target["payload"]["discussion_hash"]
    chain = [e for e in redactions_for(target_ref, db_path=db_path)
             if e["payload"].get("talk_id") == talk_id]
    expected_prev = stored
    for e in chain:
        p = e["payload"]
        if p.get("prev_hash") != expected_prev or p.get("hash_kind") != HASH_KIND_DISCUSSION:
            return {"status": "tampered", "reason": "chain_broken", "at_seq": e["seq"]}
        expected_prev = p["result_hash"]
    current = current_discussion_hash(talk_id, db_path=db_path)
    if current == expected_prev:
        return {"status": "redacted" if chain else "intact", "hash": current,
                "redactions": len(chain)}
    if is_legacy(target):
        return {"status": "legacy_unverified", "hash": current, "stored": expected_prev}
    return {"status": "tampered", "reason": "hash_mismatch", "hash": current, "expected": expected_prev}
