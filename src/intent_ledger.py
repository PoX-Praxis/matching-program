#!/usr/bin/env python3
"""
PoX 台帳 — 意志形成（intent）とガバナンス導出（指示書17 §5・§5-8）。AI/Nomic 非依存・決定論的。

intent は「人」ではなく、あるコミュニティ（ctx）の中の合意プロセス。ループ:
  proposed（提起・basis_seq/ruleset_version を固定）→ agreed（合意が閾値到達）→
  completed（実績＝2つ目の⭐️）／cancelled（取消）。participant.joined で後続参加。

本文（body/declaration/result）は台帳に載せず **ハッシュのみ**（§4-2）。状態はイベント列から
導出（connection/member と同じ）。

ガバナンス（§5-8・§1-6）:
  - basis_seq＝提起時点の seq に固定。判定は **提起時の ruleset_version** で行う（現在の閾値ではない）。
  - 母数＝持ち分（履歴の割合）。ブートストラップ: 初回 intent.completed までは創設者のみ議決権 1.0。
  - 初回 completed 以降の持ち分（＝発行式）は §2-1 で未確定のため本段では未算出（None を返す）。
"""
import uuid
from datetime import datetime, timezone

from canon import sha256_hex, canonicalize
import ledger_events as le
import intent_content
from member_ledger import members_hash

DEFAULT_RULESET = "r1"   # 初期閾値: 全員一致（§1-6）


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _events(db_path):
    return le.get_events(db_path=db_path)


def _proposed(intent_id, db_path):
    for e in le.get_events(type_="intent.proposed", db_path=db_path):
        if e["payload"].get("intent_id") == intent_id:
            return e
    return None


# ── 書き込み ────────────────────────────────────────────────────────────────

def _declaration_hash(declaration) -> str:
    """宣言（構造）の内容ハッシュ。宣言なしは空文字の SHA-256（§4-2: 本文は載せない）。"""
    norm = intent_content.normalize_declaration(declaration)
    if not norm:
        return sha256_hex(b"")
    return sha256_hex(canonicalize(norm))


def propose_intent(ctx, proposer, *, body="", declaration="",
                   ruleset_version=DEFAULT_RULESET, actor=None, db_path="pox.db"):
    intent_id = f"int_{uuid.uuid4().hex[:12]}"
    n = 1 + sum(1 for e in le.get_events(type_="intent.proposed", db_path=db_path)
                if e["payload"].get("ctx") == ctx)
    last = le.get_last_event(db_path=db_path)
    basis_seq = last["seq"] if last else 0
    # 本文・宣言（構造）は DB へ（可読性・§1-5/§3-1）。台帳は content_hash のみ。
    intent_content.save_proposal(intent_id, ctx, body or "", declaration, db_path=db_path)
    le.append_event(actor or proposer, "intent.proposed", {
        "intent_id": intent_id, "ctx": ctx, "n": n, "proposer": proposer,
        "body_hash": sha256_hex(body or ""), "declaration_hash": _declaration_hash(declaration),
        "ruleset_version": ruleset_version, "basis_seq": basis_seq,
    }, db_path=db_path)
    return {"intent_id": intent_id, "ctx": ctx, "n": n, "basis_seq": basis_seq,
            "ruleset_version": ruleset_version}


def agree_intent(intent_id, subject, *, actor=None, db_path="pox.db"):
    """subject の合意を積む。既に合意済みなら churn スキップ。"""
    base = _proposed(intent_id, db_path)
    if not base:
        return {"intent_id": intent_id, "error": "not_found"}
    if subject in _approvers(intent_id, db_path):
        return {"intent_id": intent_id, "skipped": True, "approver": subject}
    le.append_event(actor or subject, "intent.agreed", {
        "intent_id": intent_id, "approvals": [{"from": subject, "at": _now()}],
    }, db_path=db_path)
    agreed = is_agreed(intent_id, db_path=db_path)
    confirmed = None
    if agreed is True:
        # 合意が成立した時点で宣言が確定し、対応するイベントが書かれる（§1-2）。churn ガードで冪等。
        confirmed = _confirm_declaration(intent_id, base["payload"], db_path)
    return {"intent_id": intent_id, "skipped": False, "approver": subject,
            "agreed": agreed, "declaration_confirmed": confirmed}


def _confirm_declaration(intent_id, base_payload, db_path):
    """intent が agreed になった時点で宣言を確定させる（§1-2）。冪等（各生成関数の churn ガード）。

    - policy（全体方針）: コミュニティ(ctx) の profile.structured を書く（意志・現状）
    - recruit（目的別募集）: owner_ref=intent_id の necessity.published を書く（origin=self_declared）
    - kind 無し（自由記述）/ 宣言なし: 何も確定しない（通常の「何かをする」意志形成）
    """
    content = intent_content.get_content(intent_id, db_path=db_path)
    decl = content.get("declaration")
    if not decl or not decl.get("kind"):
        return None
    ctx = base_payload["ctx"]
    if decl["kind"] == "policy":
        from subject_ledger import publish_profile_structured
        from member_ledger import active_members_from_events
        members = sorted(active_members_from_events(ctx, db_path=db_path))
        profile_input = {k: decl.get(k, "") for k in
                         ("will_text", "state_have", "state_can_type", "state_bound", "state_unsorted")}
        r = publish_profile_structured(ctx, profile_input,
                                       members_after_hash=members_hash(members),
                                       actor=ctx, db_path=db_path)
        return {"kind": "policy", "profile_structured": r}
    if decl["kind"] == "recruit":
        from necessities import publish_necessity
        nec = {"will_text": decl.get("will_text", ""),
               "necessity_text": decl.get("necessity_text", "")}
        r = publish_necessity(intent_id, "intent", nec,
                              origin="self_declared", actor=ctx, db_path=db_path)
        return {"kind": "recruit", "necessity_published": r}
    # ── コミュニティ版①（指示書28 §4）。必要像は生成物（origin=generated・非クランプ）。
    if decl["kind"] == "community_overall":
        from subject_ledger import publish_profile_structured
        from member_ledger import active_members_from_events
        from necessities import publish_necessity
        members = sorted(active_members_from_events(ctx, db_path=db_path))
        profile_input = {k: decl.get(k, "") for k in
                         ("will_text", "state_have", "state_can_type", "state_bound", "state_unsorted")}
        prof = publish_profile_structured(ctx, profile_input,
                                          members_after_hash=members_hash(members),
                                          actor=ctx, db_path=db_path)
        nec_r = None
        nb = decl.get("necessity")
        if nb:
            nec_r = publish_necessity(
                ctx, "subject", _nec_from_block(nb, decl.get("will_text", "")),
                origin="generated", generator=nb.get("generator", ""),
                seeking=nb.get("seeking", ""), source_snapshot_hash=prof.get("content_hash"),
                generator_tag=nb.get("generator_tag") or None, attempt_n=nb.get("attempt_n"),
                actor=ctx, db_path=db_path)
        return {"kind": "community_overall", "profile_structured": prof, "necessity_published": nec_r}
    if decl["kind"] == "intent_necessity":
        from subject_ledger import latest_profile_content_hash
        from necessities import publish_necessity
        nb = decl.get("necessity") or {}
        # 生成元は確定済みのコミュニティ宣言の content_hash（§4-3）。宣言ブロックに明示があれば優先。
        src = nb.get("source_snapshot_hash") or latest_profile_content_hash(ctx, db_path=db_path)
        r = publish_necessity(
            intent_id, "intent", _nec_from_block(nb, ""),
            origin="generated", generator=nb.get("generator", ""),
            seeking=nb.get("seeking", ""), source_snapshot_hash=src,
            generator_tag=nb.get("generator_tag") or None, attempt_n=nb.get("attempt_n"),
            actor=ctx, db_path=db_path)
        return {"kind": "intent_necessity", "necessity_published": r}
    return None


def _nec_from_block(nb: dict, will_text: str) -> dict:
    """正規化済み necessity ブロック → publish_necessity が読む necessity dict。"""
    return {
        "will_text": will_text,
        "necessity_text": nb.get("necessity_text", ""),
        "gate_s": nb.get("gate_s"), "gate_u": nb.get("gate_u"),
        "p_sharpness": nb.get("p_sharpness"), "alpha": nb.get("alpha"), "beta": nb.get("beta"),
        "evidence_span": nb.get("evidence_span", ""),
        "seeking": nb.get("seeking", ""),
        "generator_name": nb.get("generator", ""),
    }


def completed_episodes_for_prompt(ctx, *, db_path="pox.db"):
    """コミュニティ版①（全体用）が できること_型 抽出に使う完了した取り組みを、
    **台帳側の決定的規則**で選ぶ（指示書28 §4-4。代表性より再現性）。

    0件 → []／1〜3件 → 全件／4件以上 → 直近の完了3件（完了 seq の新しい順）。
    提起者が①に貼れるよう body/result（本文はDB）を添えて返す。
    """
    proposed = {e["payload"]["intent_id"]: e
                for e in le.get_events(type_="intent.proposed", db_path=db_path)
                if e["payload"].get("ctx") == ctx}
    completed = [e for e in le.get_events(type_="intent.completed", db_path=db_path)
                 if e["payload"]["intent_id"] in proposed]
    completed.sort(key=lambda e: e["seq"], reverse=True)   # 完了の新しい順
    total = len(completed)
    chosen = completed if total <= 3 else completed[:3]
    episodes = []
    for e in chosen:
        iid = e["payload"]["intent_id"]
        c = intent_content.get_content(iid, db_path=db_path)
        episodes.append({"intent_id": iid, "body": c.get("body", ""),
                         "result": c.get("result", "")})
    return {"count": total, "episodes": episodes}


def complete_intent(intent_id, by, *, result="", db_path="pox.db"):
    """実績確定（§5-6: intent_id ごとに1回）。合意が閾値に達している時のみ。"""
    base = _proposed(intent_id, db_path)
    if not base:
        return {"intent_id": intent_id, "error": "not_found"}
    st = get_intent(intent_id, db_path=db_path)
    if st["status"] == "completed":
        return {"intent_id": intent_id, "skipped": True, "status": "completed"}
    if st["status"] == "cancelled":
        return {"intent_id": intent_id, "error": "cancelled"}
    if is_agreed(intent_id, db_path=db_path) is not True:
        return {"intent_id": intent_id, "error": "not_agreed"}
    intent_content.save_result(intent_id, result or "", db_path=db_path)   # 結果本文は DB（§1-5）
    le.append_event(by, "intent.completed", {
        "intent_id": intent_id, "result_hash": sha256_hex(result or ""),
    }, db_path=db_path)
    return {"intent_id": intent_id, "status": "completed"}


def cancel_intent(intent_id, by, *, reason="", db_path="pox.db"):
    base = _proposed(intent_id, db_path)
    if not base:
        return {"intent_id": intent_id, "error": "not_found"}
    st = get_intent(intent_id, db_path=db_path)
    if st["status"] in ("completed", "cancelled"):
        return {"intent_id": intent_id, "skipped": True, "status": st["status"]}
    le.append_event(by, "intent.cancelled", {
        "intent_id": intent_id, "by": by, "reason": reason,
    }, db_path=db_path)
    return {"intent_id": intent_id, "status": "cancelled"}


def join_participant(intent_id, participant, *, introduced_by=None, approved_by=None,
                     actor=None, db_path="pox.db"):
    base = _proposed(intent_id, db_path)
    if not base:
        return {"intent_id": intent_id, "error": "not_found"}
    le.append_event(actor or participant, "intent.participant.joined", {
        "intent_id": intent_id, "participant": participant,
        "introduced_by": introduced_by, "approved_by": list(approved_by or []),
        "intent_snapshot_hash": base["event_hash"],   # 提起時点への不変参照
    }, db_path=db_path)
    return {"intent_id": intent_id, "participant": participant}


# ── 導出 ────────────────────────────────────────────────────────────────────

def _approvers(intent_id, db_path):
    out = set()
    for e in le.get_events(type_="intent.agreed", db_path=db_path):
        if e["payload"].get("intent_id") == intent_id:
            for a in e["payload"].get("approvals", []):
                out.add(a.get("from"))
    out.discard(None)
    return out


def _has(intent_id, type_, db_path):
    return any(e["payload"].get("intent_id") == intent_id
               for e in le.get_events(type_=type_, db_path=db_path))


def _founder_of(ctx, db_path):
    """member.joined のうち members_before が空（＝創設者）の subject を返す。"""
    empty = members_hash([])
    for e in le.get_events(type_="member.joined", db_path=db_path):
        p = e["payload"]
        if p.get("ctx") == ctx and p.get("members_before_hash") == empty:
            return p.get("subject_id")
    return None


def _completed_ctx_count(ctx, upto_seq, db_path):
    """ctx に属する intent.completed の件数（seq<=upto_seq）。intent_id→ctx は proposed 経由。"""
    ctx_of = {e["payload"]["intent_id"]: e["payload"]["ctx"]
              for e in le.get_events(type_="intent.proposed", db_path=db_path)}
    n = 0
    for e in le.get_events(type_="intent.completed", db_path=db_path):
        if e["seq"] <= upto_seq and ctx_of.get(e["payload"].get("intent_id")) == ctx:
            n += 1
    return n


def voting_power(ctx, subject_id, basis_seq, *, db_path="pox.db"):
    """basis_seq 時点の議決権（§5-8）。

    ブートストラップ（basis_seq 時点で ctx に完了 intent が無い）: 創設者 1.0・他 0.0。
    初回 completed 以降の持ち分は発行式（§2-1・未確定）に依存するため **None**（未算出）。
    """
    if _completed_ctx_count(ctx, basis_seq, db_path) == 0:
        return 1.0 if subject_id == _founder_of(ctx, db_path) else 0.0
    return None


def _voting_members(ctx, basis_seq, db_path):
    """basis_seq 時点で議決権を持つメンバー集合（ブートストラップでは創設者のみ）。"""
    if _completed_ctx_count(ctx, basis_seq, db_path) == 0:
        f = _founder_of(ctx, db_path)
        return {f} if f else set()
    return None   # 未確定（発行式待ち）


def is_agreed(intent_id, *, db_path="pox.db"):
    """提起時の ruleset_version の閾値で合意判定（§5-8）。判定不能なら None。

    初期 ruleset（r1・全員一致）: basis_seq 時点の議決権メンバー全員が approvers に含まれる。
    ブートストラップ超（発行式未確定）の場合は None を返す（過大判定しない）。
    """
    base = _proposed(intent_id, db_path)
    if not base:
        return None
    p = base["payload"]
    voters = _voting_members(p["ctx"], p["basis_seq"], db_path)
    if voters is None:
        return None                      # 持ち分未確定（§2-1）
    if not voters:
        return False
    approvers = _approvers(intent_id, db_path)
    # r1 = 全員一致。未知 ruleset は判定不能（None）。
    if p.get("ruleset_version", DEFAULT_RULESET) != DEFAULT_RULESET:
        return None
    return voters.issubset(approvers)


def get_intent(intent_id, *, db_path="pox.db"):
    base = _proposed(intent_id, db_path)
    if not base:
        return None
    p = base["payload"]
    cancelled = _has(intent_id, "intent.cancelled", db_path)
    completed = _has(intent_id, "intent.completed", db_path)
    if cancelled:
        status = "cancelled"
    elif completed:
        status = "completed"
    elif is_agreed(intent_id, db_path=db_path) is True:
        status = "agreed"
    else:
        status = "proposed"
    return {"intent_id": intent_id, "ctx": p["ctx"], "n": p["n"], "proposer": p["proposer"],
            "ruleset_version": p["ruleset_version"], "basis_seq": p["basis_seq"],
            "approvers": sorted(_approvers(intent_id, db_path)), "status": status,
            "agreed": is_agreed(intent_id, db_path=db_path)}


def list_intents(ctx, *, db_path="pox.db"):
    out = []
    for e in le.get_events(type_="intent.proposed", db_path=db_path):
        if e["payload"].get("ctx") == ctx:
            out.append(get_intent(e["payload"]["intent_id"], db_path=db_path))
    return out


def _participants(intent_id, db_path):
    out = []
    for e in le.get_events(type_="intent.participant.joined", db_path=db_path):
        if e["payload"].get("intent_id") == intent_id:
            out.append({"participant": e["payload"].get("participant"),
                        "introduced_by": e["payload"].get("introduced_by"),
                        "approved_by": e["payload"].get("approved_by", []),
                        "at": e.get("at")})
    return out


def _event_at(intent_id, type_, db_path):
    for e in le.get_events(type_=type_, db_path=db_path):
        if e["payload"].get("intent_id") == intent_id:
            return e.get("at")
    return None


def get_intent_detail(intent_id, *, db_path="pox.db"):
    """UI（宣言/実績/進行中）用の可読ビュー。本文は DB から、状態は台帳から。

    数値（gate/α/β 等）は出さない。recruit の necessity_text は公開してよいが、
    necessities の内部数値には触れない（本文置き場から読むだけ）。
    """
    base = get_intent(intent_id, db_path=db_path)
    if not base:
        return None
    content = intent_content.get_content(intent_id, db_path=db_path)
    decl = content.get("declaration")
    proposed = _proposed(intent_id, db_path)
    return {
        **base,
        "body": content.get("body", ""),
        "result": content.get("result", ""),
        "declaration_kind": (decl or {}).get("kind"),
        "declaration": decl,
        "participants": _participants(intent_id, db_path),
        "proposed_at": proposed.get("at") if proposed else None,
        "completed_at": _event_at(intent_id, "intent.completed", db_path),
        "cancelled_at": _event_at(intent_id, "intent.cancelled", db_path),
    }


def list_intent_details(ctx, *, db_path="pox.db"):
    return [get_intent_detail(e["payload"]["intent_id"], db_path=db_path)
            for e in le.get_events(type_="intent.proposed", db_path=db_path)
            if e["payload"].get("ctx") == ctx]


def latest_policy_declaration(ctx, *, db_path="pox.db"):
    """コミュニティ(ctx) が確定させた最新の全体方針宣言（意志・現状）。無ければ None（§3-1）。

    「確定」＝agreed に達した policy 宣言。取消は含めない。profile.structured は content_hash
    しか持たないため、本文は intent_content から読む（最新の agreed policy を採用）。
    """
    latest = None
    for e in le.get_events(type_="intent.proposed", db_path=db_path):
        if e["payload"].get("ctx") != ctx:
            continue
        iid = e["payload"]["intent_id"]
        st = get_intent(iid, db_path=db_path)
        if st["status"] not in ("agreed", "completed"):
            continue
        c = intent_content.get_content(iid, db_path=db_path)
        d = c.get("declaration")
        if d and d.get("kind") == "policy":
            latest = {"will_text": d.get("will_text", ""),
                      "state_have": d.get("state_have", ""),
                      "state_can_type": d.get("state_can_type", ""),
                      "state_bound": d.get("state_bound", ""),
                      "state_unsorted": d.get("state_unsorted", ""),
                      "intent_id": iid, "at": e.get("at")}
    return latest
