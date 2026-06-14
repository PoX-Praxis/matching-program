#!/usr/bin/env python3
"""
PoX ③ 最小プラットフォーム
  POST /seekers   v3 JSON を受け取り DB に保存（ステップ2）
  GET  /seekers   全 seeker を返す
  POST /match     run_matching を呼び ranking を返す（ステップ3）
  GET  /          投稿・一覧・結果表示 HTML（ステップ4）

実行: python app.py
      ANTHROPIC_API_KEY=xxx python app.py  # ← 実LLM判定
      POX_DB=/path/to/pox.db python app.py # ← DB パス変更
"""
import sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from flask import Flask, request, jsonify, render_template, abort
from db import save_seeker, load_all_seekers
from connection_layer import run_matching

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
    ANTHROPIC_API_KEY が必要（未設定なら 503 でエラー内容を返す）。
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

    try:
        result = run_matching(target["seeker"], candidate_pool, want=want)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503

    return jsonify(result)


def _to_profile(seeker: dict) -> str:
    """seeker dict → run_matching が受け取る profile 文字列。"""
    return "。".join(seeker[k] for k in ("意志", "求めている", "能力") if seeker.get(k))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
