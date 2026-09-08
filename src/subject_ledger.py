#!/usr/bin/env python3
"""
PoX 台帳 — 主体・存在に関する追記イベント（指示書17 §5）。Nomic 非依存の純ロジック。

本モジュールが受け持つのは、必要像（necessities）の対になる **主体側の記録**:
  - profile.structured : 構造化されたプロフィールの時点記録（§5-2 の n・§5-3 の payload）。
    本文は台帳に載せず content_hash のみ。意志＋現状4スロットのハッシュで同一性を持つ。

content_hash は正準化＋SHA-256（canon）。evidence コミットメントのような salt を持たない
純粋な内容ハッシュなので、churn 判定は content_hash 直比較でよい。
"""
from canon import canonicalize, sha256_hex
import ledger_events as le

_STATE_KEYS = ("state_have", "state_can_type", "state_bound", "state_unsorted")
# supporting_material 側の「表示される宣言」キー（指示書18 §2-2）。
# 生テキスト(raw)・evidence・②生成素材（意志要求の素材 等）・系列素材は含めない。
_DECLARE_SM_KEYS = ("背景", "一行紹介", "意志_どこへ", "意志_なぜ", "経験", "要約文")


def profile_content_hash(profile_input: dict) -> str:
    """**表示される宣言のすべて**の内容ハッシュ（指示書18 §2）。本文は台帳に載せない（§4-2）。

    正準化規則（canon_version="c1"）:
      - 対象キー（完全列挙）: will_text / state_have / state_can_type / state_bound /
        state_unsorted /（supporting_raw 内）背景 / 一行紹介 / 意志_どこへ / 意志_なぜ /
        経験 / 要約文。
      - 欠損キーは **空文字 ""** として必ず含める（除外しない＝欠損と空を同一視・ハッシュ安定）。
      - 値は str へ強制。キー順序は JCS（昇順）で正準化 → SHA-256。
      - 除外: 生テキスト(raw)・evidence_span（永久化しないもの）／gate/α/β/γ 等の内部数値・
        match_run_id（宣言ではないもの）。
    """
    sm = profile_input.get("supporting_raw") or {}
    fields = {"will_text": str(profile_input.get("will_text") or "")}
    for k in _STATE_KEYS:
        fields[k] = str(profile_input.get(k) or "")
    for k in _DECLARE_SM_KEYS:
        fields[k] = str(sm.get(k) or "")
    return sha256_hex(canonicalize(fields))


def latest_profile_content_hash(subject_id: str, db_path: str = "pox.db"):
    """subject の最新 profile.structured の content_hash（無ければ None）。接続の根拠解決用（§1-2）。"""
    mine = _mine(subject_id, db_path)
    return mine[-1]["payload"].get("content_hash") if mine else None


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
