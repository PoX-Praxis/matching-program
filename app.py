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
from werkzeug.utils import secure_filename
from db_connect import is_postgres
from db import (save_seeker, load_all_seekers, save_profile, get_profile_view,
                get_seeker, list_candidate_pool, get_profile_edit_data,
                save_view_overrides, update_seeker_core)
from profile_view import parse_registration_text, normalize_to_seeker
from connection_layer import run_matching
from ledger import approve, load_all_vessels
from messages import send_message, get_conversation, get_inbox_summary, get_unread_count
from community import (create_community, get_community, get_all_communities,
                       request_join, approve_member, get_members, is_member,
                       is_founder, get_pending_requests, update_community)
from messages import get_community_messages

app = Flask(__name__)
DB = os.environ.get("POX_DB", "pox.db")

# Postgres 接続時は起動時にスキーマを初期化（冪等・再デプロイ安全）
if is_postgres():
    import schema
    schema.init()
    # v4 スキーマも初期化（既存テーブルと並存・非破壊）
    try:
        import schema_v4
        schema_v4.init_v4()
    except SystemExit:
        pass  # init_v4 は CLI 用に sys.exit する。起動時は握りつぶす
    except Exception as e:
        print(f"[app] v4 スキーマ初期化スキップ: {e}")

# ── 添付ファイルのアップロード設定 ──────────────────────────────
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "pdf", "txt", "csv", "zip"}


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ── 既存ルート ────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.post("/seekers")
def post_seeker():
    """
    登録の受け口（修正指示書 v3.1 §3・§4）。
      raw_text があれば strip_code_fence → parse → normalize_to_seeker を通す。
      なければ body 自体を素JSONとみなして normalize する（後方互換）。
    user_id を渡されれば上書き（再登録で重複を作らない §4）。新規のみ UUID 採番。
    """
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON が読めません"}), 400

    raw_text = body.get("raw_text")
    user_id  = body.get("user_id")

    if raw_text is not None:
        try:
            raw = parse_registration_text(raw_text)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    else:
        # 後方互換: すでにパース済みの素JSONが直接来た場合
        raw = {k: v for k, v in body.items() if k != "user_id"}

    seeker = normalize_to_seeker(raw)

    if not user_id:
        user_id = f"u_{uuid.uuid4().hex[:8]}"   # UUID由来・連番ではない（不変条件4）

    save_profile(user_id, seeker, db_path=DB)   # seeker + profile_view を同時保存（UPSERT）
    return jsonify({"id": user_id, "handle": seeker.get("id")}), 201


@app.get("/seekers")
def get_seekers():
    return jsonify(load_all_seekers(db_path=DB))


@app.post("/match")
def post_match():
    """
    seeker_id の seeker（非公開）を使ってマッチング。ranking のみ返す。
    seeker 原文はレスポンスに含まれない。
    """
    body = request.get_json(force=True, silent=True)
    if body is None:
        abort(400, "JSON が読めません")

    seeker_id = body.get("seeker_id")
    want      = body.get("want", "balanced")

    target_seeker = get_seeker(seeker_id, db_path=DB)
    if target_seeker is None:
        abort(404, f"seeker_id={seeker_id!r} が見つかりません")

    candidate_pool = list_candidate_pool(seeker_id, db_path=DB)
    if not candidate_pool:
        return jsonify({"error": "候補が0人です。seekerをもう1人以上登録してください。"}), 400

    demo_mode = not bool(os.environ.get("ANTHROPIC_API_KEY"))
    judge = _demo_judge if demo_mode else None

    try:
        result = run_matching(target_seeker, candidate_pool, want=want, judge_fn=judge)
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


# ── v4 embedding接続システム（F章 / 非破壊で並存）────────────────────────────
# 既存 /seekers・/match（v3.1）はそのまま。v4 は profiles_v4 系テーブルを使う。
# Postgres（DATABASE_URL）必須: vector / JSONB / HNSW は SQLite 非対応。

import threading, time as _time

# 必要像フィールド（①各自AIが構造化と同時に生成し body に同梱する正式ルートのキー）
_NECESSITY_FIELDS = ("necessity_text", "gate_s", "gate_u", "p_sharpness",
                     "alpha", "beta", "evidence_span", "generator")


def _v4_store():
    """v4 用 PostgresStore を返す。Postgres でなければ 503 を投げる。"""
    from db_v4 import PostgresStore
    return PostgresStore(db_path=DB)


def _v4_profile_input(body):
    """body から profiles_v4 の flat フィールド＋supporting_raw を組む。"""
    return {
        "will_text":      body.get("will_text", ""),
        "state_have":     body.get("state_have", ""),
        "state_can_type": body.get("state_can_type", ""),
        "state_bound":    body.get("state_bound", ""),
        "state_unsorted": body.get("state_unsorted", ""),
        "supporting_raw": body.get("supporting_raw") or {},
    }


# ①構造化プロンプト（v4.2 統合版）が出すネストJSONの現状スロット対応（src/db.py と一致）
_V4_STATE_MAP = {
    "state_have":     "持っているもの",
    "state_can_type": "できること_型",
    "state_bound":    "縛られているもの",
    "state_unsorted": "未分類",
}


def _normalize_v4_body(body):
    """
    ①v4.2 プロンプトのネストJSON（seeker/現状/supporting_material/necessity）を、
    /v4/seekers が期待するフラット body へ機械的に変換する（アダプタ）。

    - 既にフラットな body（seeker も necessity も無い＝curl テスト等）はそのまま返す（後方互換）。
    - 変換は「形を整えるだけ」。値の検証・クランプ（gate_s/u→[0,1] 等）・γ算出は
      下流の build_user_necessity / compute_gamma が従来どおり行う（無検証で信頼しない原則は不変）。
    - necessity ブロックが無ければ necessity_text も出ない → フォールバック経路に落ちる（従来通り）。
    """
    if not isinstance(body, dict):
        return body
    if "seeker" not in body and "necessity" not in body:
        return body  # 既にフラット

    seeker = body.get("seeker") or {}
    state = seeker.get("現状") or {}
    nec = body.get("necessity") or {}

    flat = {
        "user_id":        body.get("user_id") or body.get("id"),
        "will_text":      seeker.get("意志", ""),
        "supporting_raw": body.get("supporting_material") or body.get("supporting_raw") or {},
    }
    for eng, jp in _V4_STATE_MAP.items():
        flat[eng] = state.get(jp, "")
    # necessity ブロックをトップレベルへ展開（build_user_necessity は body 直下を読む）
    for k in _NECESSITY_FIELDS:
        if k in nec:
            flat[k] = nec[k]
    return flat


def _run_with_retry(fn, *, tries=3, base_delay=2.0):
    """指数バックオフ付きリトライ（2s→4s→8s）。最後の例外を送出する。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001（生成/埋め込みの一過性障害を吸収）
            last = e
            if i < tries - 1:
                _time.sleep(base_delay * (2 ** i))
    raise last


def _v4_async_job(profile_id, profile_input, necessity, *, is_fallback):
    """
    非同期ジョブ（daemon スレッド）: 必要像生成（フォールバック時のみ）＋4ベクトル生成。
    生成状態は generation_status（preparing→ready / error）で追跡する。
    - user-supplied 経路: necessity は受付時に検証・保存済み → ベクトル化のみ。
    - fallback 経路: ここで ② サーバー生成（ANTHROPIC_API_KEY 必須）→ 保存 → ベクトル化。
    """
    from db_v4 import (generate_necessity_v4, vectorize_profile_v4,
                       GEN_ERROR)
    store = _v4_store()
    try:
        if is_fallback:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                store.set_generation_status(
                    profile_id, GEN_ERROR,
                    error=("必要像フィールド未同梱かつサーバー生成キー未設定。"
                           "各自AIで必要像を生成して同梱し再送するか、"
                           "管理者に ② 生成キー設定を依頼してください。"))
                return
            necessity = _run_with_retry(
                lambda: generate_necessity_v4(store, profile_id, profile_input))
        _run_with_retry(
            lambda: vectorize_profile_v4(
                store, profile_id, profile_input, necessity["necessity_text"]))
    except Exception as e:  # noqa: BLE001
        try:
            store.set_generation_status(profile_id, GEN_ERROR, error=str(e)[:500])
        except Exception:
            pass


def _spawn_v4_job(profile_id, profile_input, necessity, *, is_fallback):
    t = threading.Thread(
        target=_v4_async_job, args=(profile_id, profile_input, necessity),
        kwargs={"is_fallback": is_fallback}, daemon=True)
    t.start()


@app.post("/v4/seekers")
def post_v4_seeker():
    """
    ①v4 の構造化出力を取り込む（F章 登録/更新）。二経路:

      A. 必要像フィールド同梱（各自AI生成・正式ルート）:
         build_user_necessity で **無検証で信頼せず** 範囲検証・クランプし、
         γ は compute_gamma で算出（供給 gamma は使わない）。受付で profile+necessity
         を同期保存（status=preparing）→ 202。ベクトル化は非同期。
      B. 必要像フィールド無し（フォールバック・判断B）:
         受付で profile のみ同期保存（status=preparing）→ 202。必要像のサーバー生成
         （ANTHROPIC_API_KEY 必須）とベクトル化を非同期ジョブで実行。

    supporting_raw は保存するが embedding/② には redacted のみ渡す（I章）。
    s,u,γ,p,α,β は ② が所有。生ベクトルはレスポンスに含めない。
    """
    if not is_postgres():
        return jsonify({"error": "v4 は Postgres（DATABASE_URL）が必要です"}), 503
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON が読めません"}), 400
    # ①v4.2 プロンプトのネストJSON（seeker/現状/necessity）も受理する（アダプタで平坦化）
    body = _normalize_v4_body(body)
    if not (body.get("will_text") or "").strip():
        return jsonify({"error": "will_text が必要です（意志が空です）"}), 400

    from db_v4 import receive_profile_v4, GEN_PREPARING
    from necessity_gen import build_user_necessity
    from pii_redaction import redact_for_storage

    profile_id = body.get("user_id") or f"u_{uuid.uuid4().hex[:8]}"
    profile_input = _v4_profile_input(body)
    store = _v4_store()

    # necessity_text の有無で経路を分岐（正式ルート＝各自AI生成の必要像同梱）
    supplied = (body.get("necessity_text") or "").strip()
    is_fallback = not supplied

    necessity = None
    if not is_fallback:
        # A. 正式ルート: 各自AI出力を検証・クランプ（無検証で信頼しない）。
        # src_input_hash は保存後の再生成判定（get_profile 経由）と一致させるため、
        # 保存時と同じ supporting_redacted を与えて計算する。
        supporting_redacted, _ = redact_for_storage(profile_input.get("supporting_raw") or {})
        hash_profile = {**profile_input, "supporting_redacted": supporting_redacted}
        try:
            necessity = build_user_necessity(hash_profile, body)
        except (ValueError, TypeError) as e:
            return jsonify({"error": f"必要像フィールド不正: {e}"}), 400

    try:
        receive_profile_v4(store, profile_id, profile_input, necessity,
                           generation_status=GEN_PREPARING)
    except Exception as e:
        return jsonify({"error": f"受付失敗: {e}"}), 500

    _spawn_v4_job(profile_id, profile_input, necessity, is_fallback=is_fallback)

    resp = {
        "id": profile_id,
        "generation_status": GEN_PREPARING,
        "route": "fallback" if is_fallback else "user-supplied",
        "status_url": f"/v4/seekers/{profile_id}/status",
    }
    if necessity is not None:  # 正式ルートは受付時点で数値が確定（②所有分のみ返す）
        resp.update({
            "necessity_text": necessity["necessity_text"],
            "gate_s": necessity["gate_s"], "gate_u": necessity["gate_u"],
            "gamma": necessity["gamma"],
        })
    return jsonify(resp), 202


@app.get("/v4/seekers/<profile_id>/status")
def get_v4_seeker_status(profile_id):
    """非同期生成の進捗（preparing / ready / error / needs_regeneration）を返す。"""
    if not is_postgres():
        return jsonify({"error": "v4 は Postgres（DATABASE_URL）が必要です"}), 503
    st = _v4_store().get_profile_status(profile_id)
    if st is None:
        return jsonify({"error": "プロフィールが見つかりません"}), 404
    return jsonify({"id": profile_id, **st})


@app.post("/v4/seekers/<profile_id>/retry")
def retry_v4_seeker(profile_id):
    """
    失敗した非同期生成を再試行する。保存済み necessity があれば再ベクトル化のみ、
    無ければフォールバック生成からやり直す。status=preparing に戻して再ジョブ。
    """
    if not is_postgres():
        return jsonify({"error": "v4 は Postgres（DATABASE_URL）が必要です"}), 503
    store = _v4_store()
    from db_v4 import GEN_PREPARING, MODEL_TAG

    profile = store.get_profile(profile_id)
    if profile is None:
        return jsonify({"error": "プロフィールが見つかりません"}), 404

    profile_input = {
        "will_text":      profile.get("will_text", ""),
        "state_have":     profile.get("state_have", ""),
        "state_can_type": profile.get("state_can_type", ""),
        "state_bound":    profile.get("state_bound", ""),
        "state_unsorted": profile.get("state_unsorted", ""),
        "supporting_raw": profile.get("supporting_raw") or {},
    }
    necessity = store.get_necessity(profile_id, MODEL_TAG)
    is_fallback = necessity is None  # necessity 未保存なら②生成からやり直す

    store.set_generation_status(profile_id, GEN_PREPARING, error=None)
    _spawn_v4_job(profile_id, profile_input, necessity, is_fallback=is_fallback)
    return jsonify({
        "id": profile_id, "generation_status": GEN_PREPARING,
        "route": "fallback" if is_fallback else "user-supplied",
        "status_url": f"/v4/seekers/{profile_id}/status",
    }), 202


@app.post("/v4/match")
def post_v4_match():
    """
    seeker_id を起点に v4 エンジンで照合し ranking を返す（E章）。
    フル次元総当たり（stage1）→ nested complement → 律速軸/寄与率。ledger_v4 に監査記録。
    seeker の生テキスト・ベクトルはレスポンスに含めない。
    """
    if not is_postgres():
        return jsonify({"error": "v4 は Postgres（DATABASE_URL）が必要です"}), 503
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON が読めません"}), 400
    seeker_id = body.get("seeker_id")
    if not seeker_id:
        return jsonify({"error": "seeker_id が必要です"}), 400
    top_k = body.get("top_k")
    migrate_pool = bool(body.get("migrate_pool"))

    from db_v4 import match_v4
    from migrate_v4 import ensure_migrated
    store = _v4_store()
    loader = lambda uid: get_seeker(uid, db_path=DB)

    # F-5 遅延移行: seeker が v3.1 のみなら、ここで一度だけ v4 へ移行してから照合。
    try:
        ensure_migrated(store, seeker_id, loader)
        # 移行期のブリッジ: 明示要求時のみ既存 v3.1 全員も v4 化（既定は真の遅延）。
        if migrate_pool:
            for row in load_all_seekers(db_path=DB):
                ensure_migrated(store, row["id"], loader)
    except Exception as e:
        return jsonify({"error": f"移行失敗: {e}"}), 500

    try:
        out = match_v4(store, seeker_id, top_k=top_k)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"照合失敗: {e}"}), 500

    out["match_run_id"] = f"run_{uuid.uuid4().hex[:8]}"
    return jsonify(out)


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

    result = approve(
        from_id=from_id,
        to_id=to_id,
        match_run_id=body.get("match_run_id"),
        predicted_role=body.get("predicted_role"),
        phase=None,
        db_path=DB,
    )
    return jsonify(result), 200


@app.get("/ledger")
def get_ledger():
    return jsonify(load_all_vessels(db_path=DB))


@app.get("/seekers/<seeker_id>")
def route_seeker_by_id(seeker_id):
    rows = load_all_seekers(db_path=DB)
    target = next((r for r in rows if r["id"] == seeker_id), None)
    if target is None:
        abort(404, f"seeker_id={seeker_id!r} が見つかりません")
    return jsonify(target)


@app.get("/api/profile/<user_id>")
def api_profile(user_id):
    """profile_view（view_overrides 適用済み）のみ返す。seeker は絶対に返さない。"""
    pv = get_profile_view(user_id, db_path=DB)
    if pv is None:
        return jsonify({"error": "プロフィールが見つかりません"}), 404
    return jsonify(pv)


@app.get("/api/profile/<user_id>/edit")
def api_profile_edit(user_id):
    """「見せ方を編集」用。base profile_view と現在の view_overrides を返す（seeker原文は返さない）。"""
    data = get_profile_edit_data(user_id, db_path=DB)
    if data is None:
        return jsonify({"error": "プロフィールが見つかりません"}), 404
    return jsonify(data)


@app.post("/api/profile/<user_id>/core")
def api_profile_core(user_id):
    """「中身を編集」。v4（意志/現状4スロット）対応。profile_view を再生成する。"""
    body = request.get_json(force=True, silent=True) or {}
    fields = {}
    if "意志" in body:
        fields["意志"] = body["意志"]
    for k in ("state_have", "state_can_type", "state_bound", "state_unsorted"):
        if k in body:
            fields[k] = body[k]
    # v3 後方互換
    for k in ("求めている", "能力", "フェーズ"):
        if k in body:
            fields[k] = body[k]
    if not update_seeker_core(user_id, fields, db_path=DB):
        return jsonify({"error": "プロフィールが見つかりません"}), 404
    return jsonify({"ok": True})


@app.put("/api/profile/<user_id>/overrides")
def api_profile_overrides(user_id):
    """「見せ方を編集」の保存。view_overrides のみ更新（再生成なし §5.1）。"""
    body = request.get_json(force=True, silent=True) or {}
    overrides = body.get("overrides", body)
    if not save_view_overrides(user_id, overrides, db_path=DB):
        return jsonify({"error": "プロフィールが見つかりません"}), 404
    return jsonify({"ok": True})


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
    msg_body = (body.get("body") or "").strip()
    attachment_url = body.get("attachment_url")
    if not from_id or not to_id or (not msg_body and not attachment_url):
        return jsonify({"error": "from_id, to_id, body か attachment_url が必要です"}), 400
    msg = send_message(from_id, to_id, msg_body, attachment_url=attachment_url, db_path=DB)
    return jsonify(msg), 201


@app.get("/api/conversation")
def api_conversation():
    me    = request.args.get("me")
    other = request.args.get("with")
    if not me or not other:
        return jsonify({"error": "me と with が必要です"}), 400
    msgs = get_conversation(me, other, db_path=DB)
    return jsonify(msgs)


# ── ファイルアップロード API ──────────────────────────────────
@app.post("/api/upload")
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "ファイルがありません"}), 400
    f = request.files["file"]
    if not f.filename or not _allowed_file(f.filename):
        return jsonify({"error": "許可されていないファイル形式です"}), 400
    ext = secure_filename(f.filename).rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex[:12]}.{ext}"
    f.save(os.path.join(UPLOAD_DIR, unique_name))
    return jsonify({"url": f"/static/uploads/{unique_name}"}), 201


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
    from_id  = (body.get("from_id") or "").strip()
    msg_body = (body.get("body") or "").strip()
    attachment_url = body.get("attachment_url")
    if not from_id or (not msg_body and not attachment_url):
        return jsonify({"error": "from_id と body か attachment_url が必要です"}), 400
    if not is_member(community_id, from_id, db_path=DB) and not is_founder(community_id, from_id, db_path=DB):
        return jsonify({"error": "メンバーではありません"}), 403
    msg = send_message(from_id, community_id, msg_body, attachment_url=attachment_url, db_path=DB)
    return jsonify(msg), 201


@app.patch("/api/community/<community_id>")
def api_community_update(community_id):
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    requester_id = body.get("requester_id")
    name = (body.get("name") or "").strip()
    if not requester_id or not name:
        return jsonify({"error": "requester_id と name が必要です"}), 400
    result = update_community(community_id, name, body.get("description", ""), requester_id, db_path=DB)
    if result is None:
        return jsonify({"error": "見つからないか権限がありません"}), 403
    return jsonify(result)


def _to_profile(seeker: dict) -> str:
    return "。".join(seeker[k] for k in ("意志", "求めている", "能力") if seeker.get(k))


if __name__ == "__main__":
    # 環境変数で起動設定を変更できる:
    #   POX_HOST=0.0.0.0  ← LAN/トンネル公開時（既定は localhost のみ）
    #   POX_PORT=5000
    #   POX_DEBUG=1        ← 開発時のみ。公開時は必ず 0（debugger は遠隔実行の危険）
    host  = os.environ.get("POX_HOST", "127.0.0.1")
    port  = int(os.environ.get("POX_PORT", "5000"))
    debug = os.environ.get("POX_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
