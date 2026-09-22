#!/usr/bin/env python3
"""指示書41 §4/§5 — コミュニティのガバナンス台帳（新設・拡張イベントと合意判定の組み立て）。

このモジュールは「台帳から合意判定の入力を組み立てる」層と「新設イベントの書き込み」層。
判定そのものは agreement.evaluate_agreement（純粋関数・段階1）に委ねる。
決定性のため、判定入力は必ず基準点(basis_seq)時点の台帳から導出する（現在時刻・現在集合を使わない・§10）。

新設イベント（§4）:
  purpose.agreed   … 目的の合意（提議トークの結論）
  intent.launched  … プロジェクトの立ち上げ

拡張イベント（§4・共通項目 §4-2 を持たせる）:
  member.joined（member_ledger 側で拡張）/ intent.participant.joined / intent.completed

共通項目（§4-2）: approvals[] / basis_seq / ruleset_version / anchor_range / discussion_hash
本文は台帳に載せない（ハッシュのみ・§10 禁則）。
"""
from canon import sha256_hex
import ledger_events as le
from member_ledger import members_hash
from agreement import evaluate_agreement


# ── ハッシュ ────────────────────────────────────────────────────────────────
def discussion_hash(body: str) -> str:
    """経緯（トークの本文）のハッシュ。本文は公開の通常DBに残し、台帳へはハッシュのみ（§4-2）。"""
    return sha256_hex(body or "")


def conclusion_hash(text: str) -> str:
    return sha256_hex(text or "")


def result_hash(text: str) -> str:
    return sha256_hex(text or "")


# ── 基準点(basis_seq)時点のスナップショット導出（決定性の要）────────────────
def members_at(ctx: str, basis_seq: int, *, db_path: str = "pox.db") -> set:
    """basis_seq 時点の active メンバー集合（頭数の分母・§5-2）。seq<=basis_seq のみを見る。"""
    members: set = set()
    for e in le.get_events(db_path=db_path):
        if e["seq"] > basis_seq:
            break                                   # get_events は seq 昇順
        p = e["payload"]
        if p.get("ctx") != ctx:
            continue
        if e["type"] == "member.joined":
            members.add(p.get("subject_id"))
        elif e["type"] == "member.left":
            members.discard(p.get("subject_id"))
    members.discard(None)
    return members


def participants_at(intent_id: str, basis_seq: int, *, db_path: str = "pox.db") -> set:
    """basis_seq 時点のプロジェクト参加者集合（頭数の分母・§5-2）。立ち上げ者＋参加者。"""
    parts: set = set()
    for e in le.get_events(db_path=db_path):
        if e["seq"] > basis_seq:
            break
        p = e["payload"]
        if e["type"] == "intent.launched" and p.get("intent_id") == intent_id:
            parts.add(p.get("launcher"))
        elif e["type"] == "intent.participant.joined" and p.get("intent_id") == intent_id:
            parts.add(p.get("participant"))
    parts.discard(None)
    return parts


def _anchors_after(basis_seq: int, db_path: str) -> list:
    """basis_seq より後に公開されたアンカー（anchor.published）を seq 昇順で返す。"""
    return [e for e in le.get_events(type_="anchor.published", db_path=db_path)
            if e["seq"] > basis_seq]


def anchors_crossed(basis_seq: int, *, db_path: str = "pox.db") -> int:
    """基準点以降に跨いだアンカー数（§5-1 の期間判定に使う）。"""
    return len(_anchors_after(basis_seq, db_path))


def anchor_range(basis_seq: int, *, db_path: str = "pox.db"):
    """跨いだアンカーの範囲（§4-2）。1つも跨いでいなければ None（即時成立など・§5-3）。"""
    ev = _anchors_after(basis_seq, db_path)
    if not ev:
        return None
    return {"from_seq": ev[0]["seq"], "to_seq": ev[-1]["seq"], "count": len(ev)}


# ── 規則の版（§4-5）────────────────────────────────────────────────────────
def _genesis_member_joined_hash(ctx: str, db_path: str):
    empty = members_hash([])
    for e in le.get_events(type_="member.joined", db_path=db_path):
        p = e["payload"]
        if p.get("ctx") == ctx and p.get("members_before_hash") == empty:
            return e["event_hash"]
    return None


def resolve_ruleset_version(ctx: str, *, db_path: str = "pox.db"):
    """判定に使う規則の版の識別子（§4-5）。

    今後は「規則を変える宣言を含む purpose.agreed の event_hash」。無ければ初期値として
    subject.created（kind=community）の event_hash。それも無ければ（現状の実装では
    subject.created を書いていないため）コミュニティのジェネシス member.joined の
    event_hash を初期値とする。旧版の intent.agreed による版はそのまま有効（本関数は新経路用）。
    """
    latest = None
    for e in le.get_events(type_="purpose.agreed", db_path=db_path):
        p = e["payload"]
        if p.get("ctx") == ctx and p.get("changes_ruleset"):
            latest = e["event_hash"]
    if latest:
        return latest
    for e in le.get_events(type_="subject.created", db_path=db_path):
        p = e["payload"]
        if p.get("subject_id") == ctx or p.get("ctx") == ctx:
            return e["event_hash"]
    return _genesis_member_joined_hash(ctx, db_path)


# ── 合意判定の組み立て（台帳 → 純粋関数）──────────────────────────────────
def judge_community_decision(ctx, basis_seq, approvals, dissents, *,
                             period_anchors=None, db_path="pox.db"):
    """コミュニティの決定（加入・目的の合意）。分母＝基準点時点のメンバーの頭数（§5-2）。"""
    denom = members_at(ctx, basis_seq, db_path=db_path)
    crossed = anchors_crossed(basis_seq, db_path=db_path)
    kw = {} if period_anchors is None else {"period_anchors": period_anchors}
    return evaluate_agreement(denominator=denom, approvals=approvals,
                              dissents=dissents, anchors_crossed=crossed, **kw)


def judge_project_decision(intent_id, basis_seq, approvals, dissents, *,
                           period_anchors=None, db_path="pox.db"):
    """プロジェクトの決定（参加・達成）。分母＝基準点時点の参加者の頭数（§5-2）。"""
    denom = participants_at(intent_id, basis_seq, db_path=db_path)
    crossed = anchors_crossed(basis_seq, db_path=db_path)
    kw = {} if period_anchors is None else {"period_anchors": period_anchors}
    return evaluate_agreement(denominator=denom, approvals=approvals,
                              dissents=dissents, anchors_crossed=crossed, **kw)


def _common_items(approvals, basis_seq, ruleset_version, anchor_range_, discussion_hash_):
    """§4-2 の共通項目。判定の入力をすべて刻み、台帳だけから再計算できるようにする。"""
    return {
        "approvals": sorted(set(approvals or [])),
        "basis_seq": basis_seq,
        "ruleset_version": ruleset_version,
        "anchor_range": anchor_range_,          # 即時成立などは None
        "discussion_hash": discussion_hash_,
    }


# ── 新設・拡張イベントの書き込み（§4-3）──────────────────────────────────
def publish_purpose_agreed(talk_id, ctx, conclusion_hash_, *, approvals, basis_seq,
                           ruleset_version, anchor_range_, discussion_hash_,
                           declaration_hash=None, target_intent_id=None,
                           changes_ruleset=False, actor=None, db_path="pox.db"):
    """目的の合意（提議トークの結論）。§4-3 purpose.agreed。"""
    payload = {
        "talk_id": talk_id, "ctx": ctx,
        "conclusion_hash": conclusion_hash_,
        "declaration_hash": declaration_hash,       # 宣言（意志・現状・必要像・規則）を含む場合
        "target_intent_id": target_intent_id,       # 他コミュニティのプロジェクト参加に合意する場合（§6）
        "changes_ruleset": bool(changes_ruleset),   # この合意が規則を変えるなら、以後の版識別子になる（§4-5）
        **_common_items(approvals, basis_seq, ruleset_version, anchor_range_, discussion_hash_),
    }
    return le.append_event(actor or (sorted(set(approvals or [])) or [ctx])[0],
                           "purpose.agreed", payload, db_path=db_path)


def publish_intent_launched(intent_id, ctx, launcher, purpose_ref, *,
                            actor=None, db_path="pox.db"):
    """プロジェクトの立ち上げ。§4-3 intent.launched（合意ではないので共通項目は持たない）。"""
    return le.append_event(actor or launcher, "intent.launched", {
        "intent_id": intent_id, "ctx": ctx,
        "launcher": launcher,
        "purpose_ref": purpose_ref,                 # どの purpose.agreed から生まれたか（event_hash）
    }, db_path=db_path)


def publish_participant_joined(intent_id, participant, *, participant_kind,
                               approvals, basis_seq, ruleset_version, anchor_range_,
                               discussion_hash_, consent_ref=None, introduced_by=None,
                               actor=None, db_path="pox.db"):
    """プロジェクトへの参加。§4-3 intent.participant.joined（拡張）。

    participant_kind: 'individual' | 'community'
    consent_ref: コミュニティ参加の場合、その参加を決めた purpose.agreed の event_hash（§6）
    """
    if participant_kind not in ("individual", "community"):
        raise ValueError("participant_kind は 'individual' か 'community'")
    payload = {
        "intent_id": intent_id, "participant": participant,
        "participant_kind": participant_kind,
        "consent_ref": consent_ref,
        "introduced_by": introduced_by,
        **_common_items(approvals, basis_seq, ruleset_version, anchor_range_, discussion_hash_),
    }
    return le.append_event(actor or participant, "intent.participant.joined", payload, db_path=db_path)


def publish_intent_completed(intent_id, result_hash_, *, approvals, basis_seq,
                             ruleset_version, anchor_range_, discussion_hash_,
                             actor=None, db_path="pox.db"):
    """プロジェクトの達成。§4-3 intent.completed（拡張）。

    下流（実績・できること_型・正解ラベル・将来の発行）は intent_id と result_hash のみ読む（§4-6）。
    追加した共通項目は読み飛ばされるだけ。intent_id / result_hash は変えない。
    """
    payload = {
        "intent_id": intent_id,
        "result_hash": result_hash_,
        **_common_items(approvals, basis_seq, ruleset_version, anchor_range_, discussion_hash_),
    }
    return le.append_event(actor or (sorted(set(approvals or [])) or [intent_id])[0],
                           "intent.completed", payload, db_path=db_path)
