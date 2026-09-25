#!/usr/bin/env python3
"""指示書41 段階3 — トーク4種のデータモデルと合意コミット。

トークの本文（投稿・結論）は公開の通常DBに残し、台帳へはハッシュのみ（§4-2/§10）。
合意判定は governance（＝基準点の台帳から分母を導出）＋ agreement（純粋関数）に委ねる。

kind:
  chat            … メンバー同士の雑談（合意なし・メンバーのみ可視・§3）
  proposal        … 提議トーク（目的の合意を目指す）→ 合意で purpose.agreed
  admission       … 加入トーク（新メンバーの加入）→ 合意で member.joined
  project         … プロジェクトトーク（intent.launched で開く器）
  project_join    … プロジェクトへの参加 → 既存参加者の合意で intent.participant.joined
  project_complete… プロジェクトの達成 → 合意で intent.completed

status: open（審議中）/ agreed / completed / dormant（合意に至らず休眠・§3-3。取消は無い）
"""
import json
import uuid
from datetime import datetime, timezone

from db_connect import get_connection, is_postgres
import ledger_events as le
import governance as gov
from agreement import evaluate_agreement

CHAT, PROPOSAL, ADMISSION, PROJECT, PROJECT_JOIN, PROJECT_COMPLETE = (
    "chat", "proposal", "admission", "project", "project_join", "project_complete")
KINDS = {CHAT, PROPOSAL, ADMISSION, PROJECT, PROJECT_JOIN, PROJECT_COMPLETE}
DECISION_KINDS = {PROPOSAL, ADMISSION, PROJECT_JOIN, PROJECT_COMPLETE}
PUBLIC_KINDS = KINDS - {CHAT}          # チャット以外はすべて公開（§3）


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS talks ("
            "talk_id TEXT PRIMARY KEY, ctx TEXT NOT NULL, kind TEXT NOT NULL, "
            "title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', "
            "created_by TEXT NOT NULL, created_at TEXT NOT NULL, "
            "basis_seq INTEGER NOT NULL, ruleset_version TEXT, "
            "parent_talk_id TEXT, target_json TEXT, result_json TEXT)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS talk_posts ("
            "post_id TEXT PRIMARY KEY, talk_id TEXT NOT NULL, author TEXT NOT NULL, "
            "body TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS talk_votes ("
            "talk_id TEXT NOT NULL, voter TEXT NOT NULL, stance TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, PRIMARY KEY (talk_id, voter))"
        )
        con.commit()
    return con


# ── 生成・取得 ──────────────────────────────────────────────────────────────
def create_talk(ctx, kind, title, created_by, *, target=None, parent_talk_id=None,
                db_path="pox.db"):
    """トークを作る。基準点(basis_seq)と規則の版を作成時点で記録する（§5-5）。

    合意はこの時点の台帳を基準に判定される（後から人が増えても判定は変わらない・§5）。
    """
    if kind not in KINDS:
        raise ValueError(f"未知の kind: {kind}")
    talk_id = f"tk_{uuid.uuid4().hex[:12]}"
    last = le.get_last_event(db_path=db_path)
    basis_seq = last["seq"] if last else 0
    rv = gov.resolve_ruleset_version(ctx, db_path=db_path)
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO talks (talk_id, ctx, kind, title, status, created_by, created_at, "
            "basis_seq, ruleset_version, parent_talk_id, target_json, result_json) "
            "VALUES (%s,%s,%s,%s,'open',%s,%s,%s,%s,%s,%s,NULL)",
            (talk_id, ctx, kind, title, created_by, _now(), basis_seq, rv,
             parent_talk_id, json.dumps(target or {})),
        )
    return get_talk(talk_id, db_path=db_path)


def _row_to_talk(r):
    return {"talk_id": r[0], "ctx": r[1], "kind": r[2], "title": r[3], "status": r[4],
            "created_by": r[5], "created_at": r[6], "basis_seq": r[7],
            "ruleset_version": r[8], "parent_talk_id": r[9],
            "target": json.loads(r[10]) if r[10] else {},
            "result": json.loads(r[11]) if r[11] else None}


def get_talk(talk_id, *, db_path="pox.db"):
    with _connect(db_path) as con:
        r = con.execute(
            "SELECT talk_id, ctx, kind, title, status, created_by, created_at, basis_seq, "
            "ruleset_version, parent_talk_id, target_json, result_json FROM talks WHERE talk_id=%s",
            (talk_id,)).fetchone()
    return _row_to_talk(r) if r else None


def list_talks(ctx, *, db_path="pox.db"):
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT talk_id, ctx, kind, title, status, created_by, created_at, basis_seq, "
            "ruleset_version, parent_talk_id, target_json, result_json FROM talks "
            "WHERE ctx=%s ORDER BY created_at ASC", (ctx,)).fetchall()
    return [_row_to_talk(r) for r in rows]


# ── 投稿（経緯）──────────────────────────────────────────────────────────────
def add_post(talk_id, author, body, *, db_path="pox.db"):
    pid = f"tp_{uuid.uuid4().hex[:12]}"
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO talk_posts (post_id, talk_id, author, body, created_at) "
            "VALUES (%s,%s,%s,%s,%s)", (pid, talk_id, author, body, _now()))
    return {"post_id": pid, "talk_id": talk_id, "author": author, "body": body}


def get_posts(talk_id, *, db_path="pox.db"):
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT post_id, talk_id, author, body, created_at FROM talk_posts "
            "WHERE talk_id=%s ORDER BY created_at ASC", (talk_id,)).fetchall()
    return [{"post_id": r[0], "talk_id": r[1], "author": r[2], "body": r[3], "created_at": r[4]}
            for r in rows]


def _discussion_text(talk_id, db_path):
    """経緯（トーク本文）の連結。discussion_hash の入力。順序は投稿の時系列で決定的。"""
    return "\n".join(f"{p['author']}: {p['body']}" for p in get_posts(talk_id, db_path=db_path))


# ── 投票（賛成・反対の明示。沈黙は棄権・§5-1）────────────────────────────────
def set_vote(talk_id, voter, stance, *, db_path="pox.db"):
    if stance not in ("approve", "dissent"):
        raise ValueError("stance は approve か dissent")
    with _connect(db_path) as con:
        if is_postgres():
            con.execute(
                "INSERT INTO talk_votes (talk_id, voter, stance, updated_at) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (talk_id, voter) DO UPDATE SET stance=EXCLUDED.stance, "
                "updated_at=EXCLUDED.updated_at", (talk_id, voter, stance, _now()))
        else:
            con.execute("DELETE FROM talk_votes WHERE talk_id=%s AND voter=%s", (talk_id, voter))
            con.execute(
                "INSERT INTO talk_votes (talk_id, voter, stance, updated_at) VALUES (%s,%s,%s,%s)",
                (talk_id, voter, stance, _now()))
    return {"talk_id": talk_id, "voter": voter, "stance": stance}


def get_votes(talk_id, *, db_path="pox.db"):
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT voter, stance FROM talk_votes WHERE talk_id=%s", (talk_id,)).fetchall()
    approvals = {r[0] for r in rows if r[1] == "approve"}
    dissents = {r[0] for r in rows if r[1] == "dissent"}
    return {"approvals": approvals, "dissents": dissents}


def is_closed(talk, *, db_path="pox.db"):
    """closure 済みか（追記拒否の条件・指示書44 §2-1）。休眠・審議中は closed ではない。
      提議 → purpose.agreed あり（status='agreed'）／プロジェクト容器 → intent.completed あり
      加入・参加・達成 → 合意/完了。チャットは never closed。"""
    kind, status = talk["kind"], talk["status"]
    if kind == CHAT:
        return False
    if kind == PROJECT:
        iid = (talk.get("target") or {}).get("intent_id")
        return any(e["payload"].get("intent_id") == iid
                   for e in le.get_events(type_="intent.completed", db_path=db_path))
    return status in ("agreed", "completed")


def display_status(talk, *, db_path="pox.db"):
    """画面表示用の状態語彙（指示書42 §3・43 §1-2。'open' を使わない）。"""
    kind, status = talk["kind"], talk["status"]
    if kind == CHAT:
        return None
    if kind == PROJECT:
        return "完了" if is_closed(talk, db_path=db_path) else "実行中"
    if kind == PROJECT_COMPLETE:
        return "完了" if status == "completed" else ("休眠" if status == "dormant" else "審議中")
    # proposal / admission / project_join
    if status == "agreed":
        return "合意済み"
    if status == "dormant":
        return "休眠"
    return "審議中"


def _set_status(talk_id, status, result, db_path):
    with _connect(db_path) as con:
        con.execute("UPDATE talks SET status=%s, result_json=%s WHERE talk_id=%s",
                    (status, json.dumps(result) if result is not None else None, talk_id))


# ── 合意コミット（投票のたびに呼ぶ。成立したら台帳へ記録する）─────────────────
def evaluate_talk(talk, *, db_path="pox.db"):
    """トークの現時点の合意判定。分母は基準点(basis_seq)時点の台帳から導出（§5）。"""
    votes = get_votes(talk["talk_id"], db_path=db_path)
    kind = talk["kind"]
    if kind in (PROPOSAL, ADMISSION):
        return gov.judge_community_decision(talk["ctx"], talk["basis_seq"],
                                            votes["approvals"], votes["dissents"], db_path=db_path)
    if kind == PROJECT_COMPLETE:
        intent_id = talk["target"].get("intent_id")
        return gov.judge_project_decision(intent_id, talk["basis_seq"],
                                          votes["approvals"], votes["dissents"], db_path=db_path)
    if kind == PROJECT_JOIN:
        # 分母＝基準点時点の既存参加者のみ（申し出た当人は含めない・指示書48 108-r）。
        intent_id = talk["target"].get("intent_id")
        denom = gov.participants_at(intent_id, talk["basis_seq"], db_path=db_path)
        crossed = gov.anchors_crossed(talk["basis_seq"], db_path=db_path)
        return evaluate_agreement(denominator=denom, approvals=votes["approvals"],
                                  dissents=votes["dissents"], anchors_crossed=crossed)
    return {"agreed": False, "reason": "no_decision"}   # chat / project 容器は決定を持たない


def try_commit(talk_id, *, db_path="pox.db"):
    """合意が成立していれば、対応する台帳イベントを1回だけ書く（冪等）。

    戻り値: {committed: bool, agreed: bool, reason: str, event_hash?: str}
    """
    talk = get_talk(talk_id, db_path=db_path)
    if talk is None:
        return {"committed": False, "agreed": False, "reason": "not_found"}
    if talk["status"] != "open" or talk["kind"] not in DECISION_KINDS:
        return {"committed": False, "agreed": False, "reason": "not_open_decision"}

    res = evaluate_talk(talk, db_path=db_path)
    if not res.get("agreed"):
        return {"committed": False, "agreed": False, "reason": res.get("reason")}

    approvals = res["approvers"]        # 分母内で実際に賛成したアカウント（台帳に刻む）
    basis = talk["basis_seq"]
    rv = talk["ruleset_version"]
    arange = gov.anchor_range(basis, db_path=db_path)
    dhash = gov.discussion_hash(_discussion_text(talk_id, db_path))
    tgt = talk["target"] or {}
    ev_hash = None

    if talk["kind"] == PROPOSAL:
        ev = gov.publish_purpose_agreed(
            talk_id, talk["ctx"], gov.conclusion_hash(tgt.get("conclusion", talk["title"])),
            approvals=approvals, basis_seq=basis, ruleset_version=rv, anchor_range_=arange,
            discussion_hash_=dhash, declaration_hash=tgt.get("declaration_hash"),
            target_intent_id=tgt.get("target_intent_id"),
            changes_ruleset=bool(tgt.get("changes_ruleset")), db_path=db_path)
        ev_hash = ev["event_hash"]
        _set_status(talk_id, "agreed", {"purpose_event_hash": ev_hash}, db_path)

    elif talk["kind"] == ADMISSION:
        candidate = tgt.get("candidate")
        from community import _admit_agreed_member
        _admit_agreed_member(talk["ctx"], candidate, approvals=approvals, basis_seq=basis,
                             ruleset_version=rv, anchor_range=arange, discussion_hash=dhash,
                             introduced_by=talk["created_by"], db_path=db_path)
        _set_status(talk_id, "agreed", {"member": candidate}, db_path)

    elif talk["kind"] == PROJECT_JOIN:
        ev = gov.publish_participant_joined(
            tgt.get("intent_id"), tgt.get("participant"),
            participant_kind=tgt.get("participant_kind", "individual"),
            approvals=approvals, basis_seq=basis, ruleset_version=rv, anchor_range_=arange,
            discussion_hash_=dhash, consent_ref=tgt.get("consent_ref"),
            introduced_by=talk["created_by"], db_path=db_path)
        ev_hash = ev["event_hash"]
        _set_status(talk_id, "agreed", {"joined": tgt.get("participant")}, db_path)

    elif talk["kind"] == PROJECT_COMPLETE:
        ev = gov.publish_intent_completed(
            tgt.get("intent_id"), gov.result_hash(tgt.get("result", "")),
            approvals=approvals, basis_seq=basis, ruleset_version=rv, anchor_range_=arange,
            discussion_hash_=dhash, db_path=db_path)
        ev_hash = ev["event_hash"]
        _set_status(talk_id, "completed", {"result_hash": gov.result_hash(tgt.get("result", ""))}, db_path)

    return {"committed": True, "agreed": True, "reason": res["reason"],
            "immediate": res.get("immediate", False), "event_hash": ev_hash}


def open_community_join(intent_id, community_id, consent_ref, *, opener, db_path="pox.db"):
    """コミュニティとしてプロジェクトに参加する（§6）。

    新しい仕組みは作らない: 参加するコミュニティが自分の提議トークで合意した purpose.agreed
    （target_intent_id=intent_id）の event_hash を consent_ref として渡す。ここでは
    project_join トークを開き、そのコミュニティの賛成を consent_ref に基づいて自動計上する
    （コミュニティは投票アカウントではないため、内部合意＝consent_ref が賛成の根拠）。
    プロジェクト側の既存参加者は通常どおり投票し、成立で participant.joined（kind=community）。
    """
    pa = gov.verify_community_consent(consent_ref, community_id=community_id,
                                      intent_id=intent_id, db_path=db_path)
    if pa is None:
        raise ValueError("consent_ref が無効（該当コミュニティの参加合意が見つからない）")
    ctx = gov.intent_ctx(intent_id, db_path=db_path)
    talk = create_talk(ctx, PROJECT_JOIN,
                       f"{community_id} の参加", opener,
                       target={"intent_id": intent_id, "participant": community_id,
                               "participant_kind": "community", "consent_ref": consent_ref},
                       db_path=db_path)
    # コミュニティ側の賛成を自動計上（内部合意による）。
    set_vote(talk["talk_id"], community_id, "approve", db_path=db_path)
    try_commit(talk["talk_id"], db_path=db_path)     # 単独参加者なら即時成立し得る
    return {"talk": get_talk(talk["talk_id"], db_path=db_path)}


def _proposal_talk_id_for_purpose(purpose_ref, db_path):
    """purpose.agreed の event_hash から、その提議トークの talk_id を導出（指示書42 §2）。"""
    if not purpose_ref:
        return None
    for e in le.get_events(type_="purpose.agreed", db_path=db_path):
        if e["event_hash"] == purpose_ref:
            return e["payload"].get("talk_id")
    return None


def origin_of(talk, *, db_path="pox.db"):
    """プロジェクト（実行トーク）の出自＝親の提議トーク {talk_id, title}（指示書43 §1-3・42 §2）。
    真実は台帳（intent.launched.purpose_ref → purpose.agreed.talk_id）。parent_talk_id はキャッシュ。"""
    if talk["kind"] != PROJECT:
        return None
    pid = talk.get("parent_talk_id") or _proposal_talk_id_for_purpose(
        (talk.get("target") or {}).get("purpose_ref"), db_path)
    if not pid:
        return None
    p = get_talk(pid, db_path=db_path)
    return {"talk_id": pid, "title": p["title"] if p else None}


def child_project_of(proposal_talk, *, db_path="pox.db"):
    """合意済み提議トークから立ち上がったプロジェクト {talk_id, title}。無ければ None（§2-2）。"""
    if proposal_talk["kind"] != PROPOSAL:
        return None
    pref = (proposal_talk.get("result") or {}).get("purpose_event_hash")
    for t in list_talks(proposal_talk["ctx"], db_path=db_path):
        if t["kind"] != PROJECT:
            continue
        if t.get("parent_talk_id") == proposal_talk["talk_id"] or (
                pref and (t.get("target") or {}).get("purpose_ref") == pref):
            return {"talk_id": t["talk_id"], "title": t["title"]}
    return None


def launch_project(ctx, launcher, title, purpose_ref, *, db_path="pox.db"):
    """合意された目的（purpose.agreed）からプロジェクトを立ち上げる（§2-4）。

    intent.launched を台帳へ書き、実行トーク（プロジェクトの器）を作る。実行トークは
    intent.launched の直後に自動で始まり（§1-5）、親の提議トークを parent_talk_id に持つ
    （真実は台帳の連鎖・42 §2。ここではキャッシュとして保存）。
    """
    intent_id = f"int_{uuid.uuid4().hex[:12]}"
    gov.publish_intent_launched(intent_id, ctx, launcher, purpose_ref, db_path=db_path)
    parent = _proposal_talk_id_for_purpose(purpose_ref, db_path)
    talk = create_talk(ctx, PROJECT, title, launcher,
                       target={"intent_id": intent_id, "purpose_ref": purpose_ref},
                       parent_talk_id=parent, db_path=db_path)
    return {"intent_id": intent_id, "talk": talk}
