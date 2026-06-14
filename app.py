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

from flask import Flask, request, jsonify, render_template, abort
from db import save_seeker, load_all_seekers
from connection_layer import run_matching
from ledger import approve, load_all_vessels

app = Flask(__name__)
DB = os.environ.get("POX_DB", "pox.db")


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
    """
    {"seeker_id": "...", "want": "complement"} を受け取り、
    他の全員を候補プールにして run_matching を呼ぶ。
    ANTHROPIC_API_KEY があれば実 Claude、無ければデモ採点（固定ロジック）。
    """
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

    # API キーが無ければデモ採点へフォールバック（背骨の動作確認用）
    demo_mode = not bool(os.environ.get("ANTHROPIC_API_KEY"))
    judge = _demo_judge if demo_mode else None

    try:
        result = run_matching(target["seeker"], candidate_pool, want=want, judge_fn=judge)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503

    result["match_run_id"] = f"run_{uuid.uuid4().hex[:8]}"
    result["demo_mode"] = demo_mode
    return jsonify(result)


# ── デモ採点（APIキー無しで背骨を動かすための固定ロジック。実LLMの代用ではない） ──
# 注意: 仕様8.5節の通り、文字一致は意味照合の代用にならない。これはあくまで
# 「投稿→マッチング→承認→台帳」の動線を API 無しで確認するためのデモ用。
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
    """comp=実装系キーワードの当たり具合 / sim=意志の文字bigram重なり。"""
    profile = cand.get("profile", "")
    hits = sum(1 for k in _IMPL_KEYWORDS if k in profile)
    comp = round(min(1.0, hits / 3.0), 2)          # 3語以上ヒットで 1.0
    comp_via = roles[0]["role"] if roles else None
    sim = round(min(1.0, _jaccard(seeker.get("意志", ""), profile) * 2), 2)
    return {"comp": comp, "comp_via": comp_via, "sim": sim}


@app.post("/approve")
def post_approve():
    """
    {"from_id": "...", "to_id": "...", "match_run_id": "...", "predicted_role": "..."}
    from_id が to_id を承認する。相互承認で接続成立。
    """
    body = request.get_json(force=True, silent=True)
    if body is None:
        abort(400, "JSON が読めません")

    from_id = body.get("from_id")
    to_id   = body.get("to_id")
    if not from_id or not to_id:
        abort(400, "from_id と to_id が必要です")
    if from_id == to_id:
        abort(400, "自分自身は承認できません")

    # founder のフェーズを seeker DB から取得
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
    """単一 seeker を返す。マイページ用。"""
    rows = load_all_seekers(db_path=DB)
    target = next((r for r in rows if r["id"] == seeker_id), None)
    if target is None:
        abort(404, f"seeker_id={seeker_id!r} が見つかりません")
    return jsonify(target)


@app.get("/mypage")
def mypage():
    return render_template("mypage.html")


def _to_profile(seeker: dict) -> str:
    return "。".join(seeker[k] for k in ("意志", "求めている", "能力") if seeker.get(k))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
