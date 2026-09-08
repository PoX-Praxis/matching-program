#!/usr/bin/env python3
"""
PoX 台帳 — コミュニティ構成イベント（指示書17 §5）。Nomic 非依存。

境界（§1-3）: 招待・応答（申請）は通常DB（community_members の pending・削除自由）。
成立＝承認で active になった瞬間だけ member.joined を台帳へ。自主離脱は member.left。
（追い出し member.removed は実装しない・§2-1。乗っ取りの出口はフォーク。）

member.joined payload（§5-3）:
  { ctx, subject_id, introduced_by, approved_by[], members_before_hash, members_after_hash }
introduced_by は参加した本人のイベント内にのみ書ける（後から主張する経路を作らない・§5-5）。
"""
from canon import canonicalize, sha256_hex
import ledger_events as le


def members_hash(member_ids) -> str:
    """active メンバー集合のハッシュ（順序非依存）。"""
    return sha256_hex(canonicalize(sorted(set(member_ids or []))))


def publish_member_joined(ctx: str, subject_id: str, *, introduced_by=None,
                          approved_by=None, members_before, members_after,
                          actor: str = None, db_path: str = "pox.db") -> dict:
    return le.append_event(actor or subject_id, "member.joined", {
        "ctx": ctx, "subject_id": subject_id,
        "introduced_by": introduced_by, "approved_by": list(approved_by or []),
        "members_before_hash": members_hash(members_before),
        "members_after_hash": members_hash(members_after),
    }, db_path=db_path)


def publish_member_left(ctx: str, subject_id: str, *, members_before, members_after,
                        actor: str = None, db_path: str = "pox.db") -> dict:
    return le.append_event(actor or subject_id, "member.left", {
        "ctx": ctx, "subject_id": subject_id,
        "members_before_hash": members_hash(members_before),
        "members_after_hash": members_hash(members_after),
    }, db_path=db_path)


def active_members_from_events(ctx: str, db_path: str = "pox.db") -> set:
    """member.joined / member.left から現在の active メンバー集合を導出（ガバナンスの素地）。"""
    members: set = set()
    for e in le.get_events(db_path=db_path):
        p = e["payload"]
        if p.get("ctx") != ctx:
            continue
        if e["type"] == "member.joined":
            members.add(p.get("subject_id"))
        elif e["type"] == "member.left":
            members.discard(p.get("subject_id"))
    members.discard(None)
    return members
