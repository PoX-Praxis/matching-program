#!/usr/bin/env python3
"""指示書50 v3 — 軌跡（版ごとのツリー）。通常DB と台帳から**導出するだけ**（台帳に書かない）。

版（version）: user_snapshots の 1 点＝意志・現状・必要像（standing）の本文が変わった時点
  （R1。churn 判定は subject_ledger.profile_content_hash と同じ範囲＝装飾・view_overrides は版を作らない）。
  版番号は台帳 profile.structured の n（同じ content_hash のもの）。無ければスナップショットの序数。
枝（branch）: その版から生まれた関係。**既存の面で既に公開されている事実だけ**を載せる（軌跡が可視性を広げない）。
  - 宣言   … 提議トークの立ち上げ（talks.kind=proposal・created_by=本人）。提議トークは閲覧公開
  - 接続   … 二者の接続（台帳 connection.established）。相手の版つき「自分 第N版 × 相手 第M版」
  - 参加   … intent.participant.joined（承認された参加だけ）。プロジェクト参加は公開
  - 加入   … member.joined（成立した加入だけ。申請中・見送りは載せない）。メンバー一覧は公開
  - 完了   … intent.completed（その時点で本人が参加者だったプロジェクト）
  見送り・却下・申請中は載せない（F11）。

可視性（§1）:
  本人      … 本文・表示用項目（全部）・根拠（evidence_span）・内部数値・raw
  接続の相手 … 本文・表示用項目（宣言キーのみ）。伏せた時点も見える
  第三者    … 本文・表示用項目（宣言キーのみ）。**伏せた時点（vulnerable_hidden）は本文なし**
  根拠・内部数値・raw は本人のみ（API でも返さない）。
"""
from datetime import datetime, timezone

import ledger_events as le
import talks

# 表示用項目（背景・経験など）として非本人に出す supporting のキー（subject_ledger と同じ宣言キー）。
DECLARE_SM_KEYS = ("背景", "一行紹介", "意志_どこへ", "意志_なぜ", "経験", "要約文")
NUM_KEYS = ("gate_s", "gate_u", "gamma", "p_sharpness", "alpha", "beta")
UNNAMED = "表示名未設定のアカウント"


def _ts(x):
    if not x:
        return None
    try:
        s = str(x).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def version_numbers(user_id, snaps, *, db_path="pox.db"):
    """snapshot_id → 版番号。台帳 profile.structured の n（同じ content_hash）を優先し、無ければ序数。"""
    n_by_hash = {}
    for e in le.get_events(type_="profile.structured", db_path=db_path):
        p = e["payload"]
        if p.get("subject_id") == user_id and p.get("content_hash"):
            n_by_hash[p["content_hash"]] = p.get("n")
    out = {}
    for i, s in enumerate(snaps, start=1):
        out[s["snapshot_id"]] = n_by_hash.get(s.get("content_hash")) or i
    return out


def _content(s, role, necessity_public):
    """版の本文を閲覧者の役割で絞る（§1）。None は「本文なし（伏せ）」。"""
    nec = s.get("necessity") or {}
    hidden = bool(s.get("vulnerable_hidden"))
    if role == "third" and hidden:
        return None
    sup = s.get("supporting") or {}
    c = {"will_text": s.get("will_text", ""),
         "state": s.get("state") or {},
         "necessity_text": nec.get("necessity_text", ""),
         "supporting": {k: sup.get(k) for k in DECLARE_SM_KEYS if sup.get(k)}}
    if role == "third" and not necessity_public:
        del c["necessity_text"]         # 必要像の公開閾値（指示書11・37 の既存の決定を維持）
    if role == "owner":
        c["supporting"] = sup           # raw（生テキスト等）を含む全体は本人のみ
        c["evidence_span"] = nec.get("evidence_span", "")
        c["numbers"] = {k: nec.get(k) for k in NUM_KEYS}
    return c


def _branches(user_id, vessels, names, other_versions, own_version_of, *, db_path):
    """本人に関わる公開済みの事実を枝として集める（見送り・申請中は構造上入らない）。"""
    out = []
    # 接続（二者）
    for v in vessels:
        join = (v.get("joins") or [{}])[0]
        founder, joiner = v.get("founder"), join.get("joiner")
        if user_id not in (founder, joiner) or not join.get("established_at"):
            continue
        other = joiner if user_id == founder else founder
        snaps = v.get("snapshots") or {}
        out.append({"kind": "connection", "at": join["established_at"],
                    "other": other, "other_name": names.get(other) or UNNAMED,
                    "self_version": own_version_of(snaps.get(user_id)),
                    "other_version": other_versions(other, snaps.get(other)),
                    "url": f"/profile/{other}"})
        ts = join.get("terminal_state")
        if ts and ts not in ("active", None) and join.get("closed_at"):
            out.append({"kind": "connection_ended", "at": join["closed_at"],
                        "other": other, "other_name": names.get(other) or UNNAMED,
                        "url": f"/profile/{other}"})
    # 台帳の事実（参加・加入・完了）
    import governance as gov
    events = le.get_events(db_path=db_path)
    project_titles = {}
    for e in events:
        p, t = e["payload"], e["type"]
        if t == "intent.participant.joined" and p.get("participant") == user_id:
            out.append({"kind": "joined_project", "at": e["at"], "intent_id": p.get("intent_id"),
                        "seq": e["seq"], "event_hash": e["event_hash"]})
        elif t == "member.joined" and p.get("subject_id") == user_id:
            out.append({"kind": "joined_community", "at": e["at"], "ctx": p.get("ctx"),
                        "seq": e["seq"], "event_hash": e["event_hash"]})
        elif t == "intent.completed":
            if user_id in gov.participants_at(p.get("intent_id"), e["seq"], db_path=db_path):
                out.append({"kind": "completed", "at": e["at"], "intent_id": p.get("intent_id"),
                            "seq": e["seq"], "event_hash": e["event_hash"]})
    # 宣言（提議トークの立ち上げ）と、プロジェクト名の解決
    from community import get_all_communities
    redacted_talks = {e["payload"].get("talk_id")
                      for e in events if e["type"] == "redaction.recorded"}
    comm_names = {c["id"]: c["name"] for c in get_all_communities(db_path=db_path)}
    for cid in comm_names:
        for t in talks.list_talks(cid, db_path=db_path):
            if t["kind"] == talks.PROJECT:
                project_titles[(t["target"] or {}).get("intent_id")] = (t["title"], t["talk_id"])
            if t["kind"] == talks.PROPOSAL and t["created_by"] == user_id:
                out.append({"kind": "declared", "at": t["created_at"], "title": t["title"],
                            "url": f"/talk/{t['talk_id']}",
                            "partially_redacted": t["talk_id"] in redacted_talks})
    for b in out:
        if b["kind"] in ("joined_project", "completed"):
            title, tk = project_titles.get(b["intent_id"], (None, None))
            b["title"] = title or "プロジェクト"
            b["url"] = f"/talk/{tk}" if tk else None
        elif b["kind"] == "joined_community":
            b["title"] = comm_names.get(b["ctx"]) or "コミュニティ"
            b["url"] = f"/community/{b['ctx']}"
    return out


def build(user_id, role, snaps, vessels, *, names, necessity_public_for, other_snaps_of,
          db_path="pox.db"):
    """版ツリーを組み立てる。台帳・DB には書かない。

    names: {subject_id: 表示名}／necessity_public_for(created_at)->bool（第三者の必要像の閾値）
    other_snaps_of(subject_id)->list（相手の版番号の解決用）
    """
    vn = version_numbers(user_id, snaps, db_path=db_path)

    def own_version_of(snapshot_id):
        return vn.get(snapshot_id) if snapshot_id else None

    def other_versions(other, snapshot_id):
        if not snapshot_id:
            return None
        return version_numbers(other, other_snaps_of(other), db_path=db_path).get(snapshot_id)

    versions = []
    for i, s in enumerate(snaps):
        current = (i == len(snaps) - 1)
        node = {"n": vn[s["snapshot_id"]], "anchor": f"v{vn[s['snapshot_id']]}",
                "snapshot_id": s["snapshot_id"], "at": s.get("created_at"),
                "source": "登録" if i == 0 else "再構造化",
                "current": current, "hidden": bool(s.get("vulnerable_hidden")),
                "branches": []}
        if role == "owner":
            node["vulnerable_hidden"] = bool(s.get("vulnerable_hidden"))
        if current:
            node["content"] = None         # 本文は上の「いまの姿」に表示中（§3-2）
            node["shown_above"] = True
        else:
            node["content"] = _content(s, role, necessity_public_for(s.get("created_at")))
        versions.append(node)

    before = []                            # 最初の版より前に起きた関係
    for b in sorted(_branches(user_id, vessels, names, other_versions, own_version_of,
                              db_path=db_path), key=lambda x: _ts(x["at"]) or datetime.min.replace(tzinfo=timezone.utc)):
        # 接続は「どの版から結ばれたか」を台帳の結合で持つ。無ければ時刻で直前の版に置く。
        idx = None
        if b["kind"] == "connection" and b.get("self_version") is not None:
            idx = next((i for i, v in enumerate(versions) if v["n"] == b["self_version"]), None)
        if idx is None:
            bt = _ts(b["at"])
            for i, s in enumerate(snaps):
                st = _ts(s.get("created_at"))
                if st and bt and st <= bt:
                    idx = i
        (versions[idx]["branches"] if idx is not None else before).append(b)
    return {"versions": versions, "before_first_version": before,
            "version_count": len(versions), "change_count": max(0, len(versions) - 1)}
