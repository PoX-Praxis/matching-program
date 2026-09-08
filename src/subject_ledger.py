#!/usr/bin/env python3
"""
PoX 台帳 — 主体・存在に関する追記イベント（指示書17 §5）。Nomic 非依存の純ロジック。

本モジュールが受け持つのは、必要像（necessities）の対になる **主体側の記録**:
  - profile.structured : 構造化されたプロフィールの時点記録（§5-2 の n・§5-3 の payload）。
    本文は台帳に載せず content_hash のみ。意志＋現状4スロットのハッシュで同一性を持つ。

content_hash は正準化＋SHA-256（canon）。evidence コミットメントのような salt を持たない
純粋な内容ハッシュなので、churn 判定は content_hash 直比較でよい。
"""
from canon import content_hash as _content_hash
import ledger_events as le

_STATE_KEYS = ("state_have", "state_can_type", "state_bound", "state_unsorted")


def profile_content_hash(profile_input: dict) -> str:
    """意志＋現状4スロットの内容ハッシュ（本文は台帳に載せない・§4-2）。"""
    state = {k: (profile_input.get(k) or "") for k in _STATE_KEYS}
    return _content_hash(profile_input.get("will_text") or "", state)


def _mine(subject_id: str, db_path: str):
    return [e for e in le.get_events(type_="profile.structured", db_path=db_path)
            if e["payload"].get("subject_id") == subject_id]


def publish_profile_structured(subject_id: str, profile_input: dict, *,
                               members_after_hash=None, actor: str = None,
                               skip_if_unchanged: bool = True,
                               db_path: str = "pox.db") -> dict:
    """構造化プロフィールの時点を台帳へ記録する（§5-2/§5-3）。

    - n は subject ごとの単調カウンタ（1 始まり）。
    - prev_snapshot は同 subject の直前 profile.structured の content_hash。
    - members_after_hash はコミュニティのみ（individual は None）。
    - skip_if_unchanged: 直前と content_hash が同一なら記録しない（churn 防止）。
    戻り値: {"skipped", "content_hash", "n", "prev_snapshot"}。
    """
    ch = profile_content_hash(profile_input)
    mine = _mine(subject_id, db_path)
    if skip_if_unchanged and mine and mine[-1]["payload"].get("content_hash") == ch:
        return {"skipped": True, "content_hash": ch,
                "n": mine[-1]["payload"].get("n"), "prev_snapshot": None}
    n = len(mine) + 1
    prev_snapshot = mine[-1]["payload"].get("content_hash") if mine else None
    le.append_event(actor or subject_id, "profile.structured", {
        "subject_id": subject_id, "n": n, "content_hash": ch,
        "prev_snapshot": prev_snapshot, "members_after_hash": members_after_hash,
    }, db_path=db_path)
    return {"skipped": False, "content_hash": ch, "n": n, "prev_snapshot": prev_snapshot}


def publish_visibility_changed(subject_id: str, scope: str, *, actor: str = None,
                               skip_if_unchanged: bool = True,
                               db_path: str = "pox.db") -> dict:
    """公開範囲の変更を台帳へ記録（§5-1/§5-3: visibility.changed {subject_id, scope}）。

    直前の visibility.changed と scope が同一なら churn スキップ。
    """
    if skip_if_unchanged:
        mine = [e for e in le.get_events(type_="visibility.changed", db_path=db_path)
                if e["payload"].get("subject_id") == subject_id]
        if mine and mine[-1]["payload"].get("scope") == scope:
            return {"skipped": True, "scope": scope}
    le.append_event(actor or subject_id, "visibility.changed", {
        "subject_id": subject_id, "scope": scope,
    }, db_path=db_path)
    return {"skipped": False, "scope": scope}
