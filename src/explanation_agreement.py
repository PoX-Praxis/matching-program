"""接続の説明 — 合意の記録と削除（指示書69 §4-1・§4-2。KAORU の確認 2026-11-10）。

- 申請者が画面2 で読んだ表示を保存して申請し（段階1）、受けた人が画面3 で「内容を確認しました」にチェックして承認し、
  接続が成立したとき、申請者の向き（申請者の目的 × 受けた人の文）の組について、台帳に `explanation.agreed` を 1 件書く
- 記録に残すのは ②③④（句は HMAC）・関係のラベル・向きだけ。① は入れない。数値・プロフィール全体のハッシュも入れない
- 接続のイベント（connection.established）は変えない（案 B）。合意のイベントから `connection_ref` で接続を指す
- 生成のモデル・revision・プロンプトの sha は、まとめたハッシュ 1 つ（generation_digest）だけを入れる
- 削除（退会・削除の求め）: 本体の DB から本文・①・表示を消し、合意のイベント（ハッシュ）は残す。
  `redaction.recorded` に hash_kind "explanation" を 1 件書く（本文が消えて pair_digest を再計算できない理由を残す）
- 日次のアンカーは台帳のすべてのイベントを含むので、このイベントも含まれる
"""
import hashlib
import hmac
import json
from datetime import datetime, timezone

from canon import canonicalize, sha256_hex

import explanation as X
import explanation_store as S

EVENT_TYPE = "explanation.agreed"
CANON_VERSION = "a1"
HASH_KIND_EXPLANATION = "explanation"


def _now_ms():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _hmac(text):
    k = S.digest_key()
    return hmac.new(k, str(text).encode("utf-8"), hashlib.sha256).hexdigest()


def generation_digest(generation):
    """モデル id・revision・プロンプトの sha をまとめたハッシュ（KAORU 2026-11-10）。公開の値なので鍵は使わない。"""
    g = generation or {}
    return sha256_hex(canonicalize({"model": g.get("model"), "revision": g.get("revision"),
                                    "prompt1_sha": g.get("prompt1_sha"), "prompt2_sha": g.get("prompt2_sha")}))


def a1_object(card, applicant, recipient, necessity_c4=None):
    """正準形 a1 の中身: 申請者の向きの explained の目的の組（②③④・関係）。句と、引用元の欄の文は HMAC。① は入れない。
    necessity_c4: {purpose_id: その目的の必要像の版の c4 ハッシュ}（分からなければ null）。"""
    src = {c["ref"]: c["text"] for c in card.get("citations") or []}
    purposes = []
    for p in sorted(card.get("purposes") or [], key=lambda x: x["order"]):
        if p.get("status") != "explained":
            continue
        pid = p["ref"].split(".", 2)[2]
        pairs = []
        for e in p["elements"]:
            o_side, o_kind, o_rest = e["offer_ref"].split(".", 2)
            pairs.append({"need_element": e["need_ref"].rsplit(".", 1)[-1],
                          "need_quote_h": _hmac(e["need_quote"]),
                          "offer_ref": f"{o_kind}.{o_rest}",
                          "offer_text_h": _hmac(src.get(e["offer_ref"], "")),
                          "offer_quote_h": _hmac(e["offer_quote"]),
                          "relation": e["relation"]})
        pairs.sort(key=lambda x: (x["need_element"], x["offer_ref"], x["offer_quote_h"]))
        purposes.append({"purpose_id": pid, "necessity_c4": (necessity_c4 or {}).get(pid), "pairs": pairs})
    return {"canon_version": CANON_VERSION, "relation_rule_version": card.get("relation_rule_version", X.RELATION_RULE_VERSION),
            "need_subject": applicant, "offer_subject": recipient, "purposes": purposes}


def pair_digest(a1):
    """pair_digest = HMAC-SHA256(POX_EXPLANATION_DIGEST_KEY, canonicalize(a1))。(digest, key_id)。"""
    k = S.digest_key()
    return hmac.new(k, canonicalize(a1), hashlib.sha256).hexdigest(), S.key_id(k)


def record_agreement(render, connection_ref, necessity_c4=None, db_path="pox.db"):
    """二人の確認がそろったとき（申請の表示＋承認の確認＋接続の成立）だけ、explanation.agreed を 1 件書く。
    書いたら render に pair_digest とイベントのハッシュを残す。書かなければ None（説明の組が無い・確認が欠けている）。"""
    import ledger_events as le
    if not render or render.get("status") != "approved" or not render.get("viewer_digest") or not render.get("recipient_digest"):
        return None
    card = json.loads(render.get("card_json") or "null")
    if not card or not connection_ref:
        return None
    a1 = a1_object(card, render["from_subject"], render["to_subject"], necessity_c4)
    if not any(p["pairs"] for p in a1["purposes"]):
        return None                                   # 説明の組が無ければ記録しない
    digest, kid = pair_digest(a1)
    sup = S.approve_supplements(render["id"], db_path=db_path)
    payload = {
        "connection_ref": connection_ref,
        "pair_digest": digest, "digest_key_id": kid, "canon_version": CANON_VERSION,
        "relation_rule_version": a1["relation_rule_version"], "spec_version": card.get("schema_version", X.SCHEMA_VERSION),
        "direction": {"need": render["from_subject"], "offer": render["to_subject"]},
        "assents": [{"by": render["from_subject"], "at": render["created_at"], "render_digest": render["viewer_digest"],
                     "render_key_id": render["viewer_key_id"]},
                    {"by": render["to_subject"], "at": render["approved_at"], "render_digest": render["recipient_digest"],
                     "render_key_id": render["recipient_key_id"]}],
        "supplemented": bool(sup),
        "generation_digest": generation_digest(card.get("generation")),
    }
    res = le.append_event(render["to_subject"], EVENT_TYPE, payload, db_path=db_path)
    S.mark_agreed(render["id"], digest, res["event_hash"], db_path=db_path)
    return {**res, "payload": payload, "a1": a1}


def connection_event_hash(a, b, db_path="pox.db"):
    """二人の間の最も新しい connection.established の event_hash（無ければ None）。"""
    import ledger_events as le
    pair = {a, b}
    found = None
    for e in le.get_events(type_="connection.established", db_path=db_path):
        if {e["payload"].get("a"), e["payload"].get("b")} == pair:
            found = e["event_hash"]
    return found


def record_redaction(agreed_event_hash, *, reason_class, decided_by, db_path="pox.db"):
    """合意の説明の本文を消した事実を、redaction.recorded（hash_kind "explanation"）として台帳に残す。
    本文が消えると pair_digest は再計算できない。正規の削除であることを第三者が区別できるようにする。"""
    import ledger_events as le
    import redaction as R
    if reason_class not in R.REASON_CLASSES:
        raise ValueError(f"reason_class は {R.REASON_CLASSES} のいずれか")
    if not decided_by:
        raise ValueError("decided_by が必要です")
    target = next((e for e in le.get_events(type_=EVENT_TYPE, db_path=db_path) if e["event_hash"] == agreed_event_hash), None)
    if target is None:
        raise ValueError("target_ref の合意のイベントがありません")
    chain = [e for e in le.get_events(type_=R.EVENT_TYPE, db_path=db_path)
             if e["payload"].get("target_ref") == agreed_event_hash]
    payload = {
        "target_ref": agreed_event_hash,
        "hash_kind": HASH_KIND_EXPLANATION,
        "canon_version": CANON_VERSION,
        "prev_hash": chain[-1]["payload"]["result_hash"] if chain else target["payload"]["pair_digest"],
        "result_hash": "deleted",
        "scope_digest": sha256_hex(canonicalize({"target_ref": agreed_event_hash, "scope": "explanation_body"})),
        "reason_class": reason_class,
        "decided_by": decided_by,
        "recorded_at": _now_ms(),
    }
    res = le.append_event(decided_by, R.EVENT_TYPE, payload, db_path=db_path)
    return {**res, "type": R.EVENT_TYPE, "payload": payload}


def delete_for_subject(subject_id, *, reason_class="subject_request", decided_by="ops", apply=False, db_path="pox.db"):
    """退会・削除の求め（§4-2 (5)）: その人が関わる説明の本文・①・表示・補足・向きのレコードを本体の DB から消す。
    合意のイベントは台帳に残し、合意ごとに redaction.recorded を書く。既定は数えるだけ（apply=True で消す）。"""
    plan = S.subject_rows(subject_id, db_path=db_path)
    if not apply:
        return {"apply": False, **{k: len(v) if isinstance(v, list) else v for k, v in plan.items()}}
    agreed = S.delete_subject_texts(subject_id, db_path=db_path)
    recorded = [record_redaction(h, reason_class=reason_class, decided_by=decided_by, db_path=db_path)["event_hash"]
                for h in agreed]
    return {"apply": True, "renders": len(plan["renders"]), "directions": plan["directions"],
            "supplements": plan["supplements"], "redactions": len(recorded)}
