#!/usr/bin/env python3
"""
PoX ③ 最小プラットフォーム
  POST /seekers   v3 JSON を受け取り DB に保存
  GET  /seekers   全 seeker を返す
  POST /match     run_matching を呼び ranking を返す
  POST /approve   承認を記録（相互承認で接続成立）
  GET  /ledger    台帳（全 vessel）を返す
  GET  /          HTML UI

実行: python app.py
      ANTHROPIC_API_KEY=xxx python app.py  ← 実LLM判定
      POX_DB=/path/to/pox.db python app.py ← DB パス変更
"""
import sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from flask import Flask, request, jsonify, render_template, abort, redirect, url_for
from db import save_seeker, load_all_seekers
from connection_layer import run_matching
from ledger import approve, load_all_vessels
from messages import send_message, get_conversation, get_inbox_summary, get_unread_count
from community import (create_community, get_community, get_all_communities,
                       request_join, approve_member, get_members, is_member,
                       is_founder, get_pending_requests)
from messages import get_community_messages

app = Flask(__name__)
DB = os.environ.get("POX_DB", "pox.db")


# ── 既存ルート ────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.post("/seekers")
def post_seeker():
    """v3 JSON を受け取り DB に保存。id が無ければ自動採番。"""
    body = request.get_json(force=True, silent=True)
    if body is None:
        abort(400, "JSON が読めません")
    seeker_id = body.pop("id", None) or f"u_{uuid.uuid4().hex[:8]}"
    save_seeker(seeker_id, body, db_path=DB)
    return jsonify({"id": seeker_id}), 201


@app.get("/seekers")
def get_seekers():
    return jsonify(load_all_seekers(db_path=DB))


@app.post("/match")
def post_match():
    body = request.get_json(force=True, silent=True)
    if body is None:
        abort(400, "JSON が読めません")

    seeker_id = body.get("seeker_id")
    want = body.get("want", "balanced")

    rows = load_all_seekers(db_path=DB)
    target = next((r for r in rows if r["id"] == seeker_id), None)
    if target is None:
        abort(404, f"seeker_id={seeker_id!r} が見つかりません")

    candidate_pool = [
        {"id": r["id"], "profile": _to_profile(r["seeker"])}
        for r in rows if r["id"] != seeker_id
    ]
    if not candidate_pool:
        return jsonify({"error": "候補が0人です。seekerをもう1人以上登録してください。"}), 400

    demo_mode = not bool(os.environ.get("ANTHROPIC_API_KEY"))
    judge = _demo_judge if demo_mode else None

    try:
        result = run_matching(target["seeker"], candidate_pool, want=want, judge_fn=judge)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503

    result["match_run_id"] = f"run_{uuid.uuid4().hex[:8]}"
    result["demo_mode"] = demo_mode
    return jsonify(result)


_IMPL_KEYWORDS = [
    "実装", "開発", "エンジニア", "SaaS", "バックエンド", "フロント", "API",
    "DB", "データベース", "コード", "プログラ", "作れる", "プロダクト", "アプリ", "技術",
]


def _bigrams(s: str) -> set:
    s = (s or "").replace(" ", "").replace("　", "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jaccard(a: str, b: str) -> float:
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _demo_judge(seeker, cand, roles):
    profile = cand.get("profile", "")
    hits = sum(1 for k in _IMPL_KEYWORDS if k in profile)
    comp = round(min(1.0, hits / 3.0), 2)
    comp_via = roles[0]["role"] if roles else None
    sim = round(min(1.0, _jaccard(seeker.get("意志", ""), profile) * 2), 2)
    return {"comp": comp, "comp_via": comp_via, "sim": sim}


@app.post("/approve")
def post_approve():
    body = request.get_json(force=True, silent=True)
    if body is None:
        abort(400, "JSON が読めません")

    from_id = body.get("from_id")
    to_id   = body.get("to_id")
    if not from_id or not to_id:
        abort(400, "from_id と to_id が必要です")
    if from_id == to_id:
        abort(400, "自分自身は承認できません")

    rows = load_all_seekers(db_path=DB)
    founder_row = next((r for r in rows if r["id"] == from_id), None)
    phase = founder_row["seeker"].get("フェーズ") if founder_row else None

    result = approve(
        from_id=from_id,
        to_id=to_id,
        match_run_id=body.get("match_run_id"),
        predicted_role=body.get("predicted_role"),
        phase=phase,
        db_path=DB,
    )
    return jsonify(result), 200


@app.get("/ledger")
def get_ledger():
    return jsonify(load_all_vessels(db_path=DB))


@app.get("/seekers/<seeker_id>")
def get_seeker(seeker_id):
    rows = load_all_seekers(db_path=DB)
    target = next((r for r in rows if r["id"] == seeker_id), None)
    if target is None:
        abort(404, f"seeker_id={seeker_id!r} が見つかりません")
    return jsonify(target)


@app.get("/mypage")
def mypage():
    return render_template("mypage.html")


# ── 新規ページルート ────────────────────────────────────────

@app.get("/about")
def about():
    return render_template("about.html")


@app.get("/register")
def register():
    return render_template("register.html")


@app.get("/profile/<seeker_id>")
def profile(seeker_id):
    return render_template("profile.html")


@app.get("/inbox")
def inbox():
    return render_template("inbox.html")


@app.get("/edit")
def edit():
    return render_template("edit.html")


@app.get("/conversation")
def conversation():
    return render_template("conversation.html")


@app.get("/communities")
def communities():
    return render_template("communities.html")


@app.get("/community/<community_id>")
def community_page(community_id):
    return render_template("community.html")


# ── メッセージ API ────────────────────────────────────────────

@app.post("/messages")
def post_message():
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    from_id = body.get("from_id")
    to_id   = body.get("to_id")
    msg_body = body.get("body", "").strip()
    if not from_id or not to_id or not msg_body:
        return jsonify({"error": "from_id, to_id, body が必要です"}), 400
    msg = send_message(from_id, to_id, msg_body, db_path=DB)
    return jsonify(msg), 201


@app.get("/api/conversation")
def api_conversation():
    me    = request.args.get("me")
    other = request.args.get("with")
    if not me or not other:
        return jsonify({"error": "me と with が必要です"}), 400
    msgs = get_conversation(me, other, db_path=DB)
    return jsonify(msgs)


# ── インボックス API ──────────────────────────────────────────

@app.get("/api/inbox")
def api_inbox():
    my_id = request.args.get("id")
    if not my_id:
        return jsonify({"error": "id が必要です"}), 400
    convs   = get_inbox_summary(my_id, db_path=DB)
    unread  = get_unread_count(my_id, db_path=DB)
    vessels = load_all_vessels(db_path=DB)
    need_approval = sum(
        1 for v in vessels
        if not v["is_connected"]
        and (v["founder"] == my_id or (v["joins"][0]["joiner"] if v["joins"] else "") == my_id)
        and _needs_my_approval(v, my_id)
    )
    return jsonify({"conversations": convs, "unread_count": unread, "pending_approvals": need_approval})


def _needs_my_approval(vessel, my_id: str) -> bool:
    j = (vessel.get("joins") or [{}])[0]
    approvers = {a["from"] for a in (j.get("approvals") or [])}
    other = j.get("joiner") if vessel["founder"] == my_id else vessel["founder"]
    return bool(other) and other in approvers and my_id not in approvers


# ── コミュニティ API ──────────────────────────────────────────

@app.post("/api/communities")
def api_create_community():
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    name        = body.get("name", "").strip()
    description = body.get("description", "")
    founder_id  = body.get("founder_id", "").strip()
    if not name or not founder_id:
        return jsonify({"error": "name と founder_id が必要です"}), 400
    community = create_community(founder_id, name, description, db_path=DB)
    return jsonify(community), 201


@app.get("/api/communities")
def api_get_communities():
    return jsonify(get_all_communities(db_path=DB))


@app.get("/api/community/<community_id>")
def api_get_community(community_id):
    c = get_community(community_id, db_path=DB)
    if c is None:
        return jsonify({"error": "コミュニティが見つかりません"}), 404
    members  = get_members(community_id, db_path=DB)
    pending  = get_pending_requests(community_id, db_path=DB)
    messages = get_community_messages(community_id, db_path=DB)
    return jsonify({**c, "members": members, "pending": pending, "messages": messages})


@app.post("/api/community/<community_id>/join")
def api_join_community(community_id):
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    member_id = body.get("member_id", "").strip()
    if not member_id:
        return jsonify({"error": "member_id が必要です"}), 400
    result = request_join(community_id, member_id, db_path=DB)
    return jsonify(result), 200


@app.post("/api/community/<community_id>/approve")
def api_approve_member(community_id):
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    member_id   = body.get("member_id", "").strip()
    approver_id = body.get("approver_id", "").strip()
    if not member_id or not approver_id:
        return jsonify({"error": "member_id と approver_id が必要です"}), 400
    if not is_founder(community_id, approver_id, db_path=DB):
        return jsonify({"error": "承認権限がありません"}), 403
    result = approve_member(community_id, member_id, db_path=DB)
    return jsonify(result), 200


@app.post("/api/community/<community_id>/message")
def api_community_message(community_id):
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    from_id  = body.get("from_id", "").strip()
    msg_body = body.get("body", "").strip()
    if not from_id or not msg_body:
        return jsonify({"error": "from_id と body が必要です"}), 400
    msg = send_message(from_id, community_id, msg_body, db_path=DB)
    return jsonify(msg), 201


def _to_profile(seeker: dict) -> str:
    return "。".join(seeker[k] for k in ("意志", "求めている", "能力") if seeker.get(k))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
