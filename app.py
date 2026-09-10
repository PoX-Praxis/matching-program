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
import sys, os, uuid, secrets
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from flask import Flask, request, jsonify, render_template, abort, redirect, url_for, session
from werkzeug.utils import secure_filename
from db_connect import is_postgres
import auth, mailer, anchor
from ledger_events import append_event
from canon import sha256_hex
from db import (save_seeker, load_all_seekers, save_profile, get_profile_view,
                get_seeker, list_candidate_pool, get_profile_edit_data,
                save_view_overrides, update_seeker_core, list_public_seeker_index,
                record_policy_consent, set_profile_visibility, get_profile_visibility)
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

# セッション署名鍵（指示書17 §3）。本番では POX_SECRET_KEY を必ず設定する。
app.secret_key = os.environ.get("POX_SECRET_KEY")
if not app.secret_key:
    app.secret_key = "dev-insecure-key-change-me"
    app.logger.warning("[auth] POX_SECRET_KEY 未設定。開発用の既定鍵で起動（本番では必ず設定すること）")

# ── 起動時ガード（指示書26 §1-3・§5-1）──────────────────────────────
# POX_EMAIL_SALT は email_hash のソルト。既定値で本番起動すると、後から正しい値を
# 入れた瞬間に全アカウントが到達不能になる。警告では足りないので本番では起動を止める。
_PROD = os.environ.get("POX_DEBUG", "0") != "1"
if _PROD and not os.environ.get("POX_EMAIL_SALT"):
    raise SystemExit(
        "[FATAL] POX_EMAIL_SALT 未設定。email_hash のソルトであり、既定値での本番起動は"
        "全アカウント喪失につながるため許可しません。Render の環境変数に設定してください"
        "（開発時のみ POX_DEBUG=1 で既定ソルトにフォールバックします）。"
    )
# メール送信設定の漏れは起動を止めないが、本番で未設定なら開発モード（メール不送）に
# なるため警告する。判定は現在のバックエンド（resend_api / smtp）に応じる。
if _PROD and not mailer.is_configured():
    _mail_backend = os.environ.get("POX_MAIL_BACKEND", "resend_api")
    app.logger.warning(
        f"[mailer] メール送信バックエンド（{_mail_backend}）が未設定です。本番なのに開発モードで"
        "起動しています＝ログインメールは一通も送信されません。"
        "resend_api なら POX_RESEND_API_KEY / POX_MAIL_FROM、smtp なら POX_SMTP_HOST/USER/PASS を設定してください。"
    )

# ── セッションCookie設定 ─────────────────────────────────────────────
# 有効期限は 30 日（auth_verify で session.permanent=True を張る）。既定では毎リクエストで
# 期限が延びる（スライディング）。POX_SECRET_KEY を差し替えると全 Cookie の署名が無効になり
# 全員ログアウトする（＝漏洩時に即差し替えてよい根拠・docs 参照）。
from datetime import timedelta as _timedelta
app.config.update(
    PERMANENT_SESSION_LIFETIME=_timedelta(days=30),
    SESSION_COOKIE_HTTPONLY=True,          # JS から Cookie を読ませない（XSS 緩和）
    SESSION_COOKIE_SAMESITE="Lax",         # クロスサイトの誤送出を抑止（CSRF 緩和）
    SESSION_COOKIE_SECURE=_PROD,           # 本番は HTTPS 限定。開発(POX_DEBUG=1)は False
)

# 規約（プライバシーポリシー）の版。terms.accepted に記録（§3-3）。
# ポリシー本文（最終更新）と揃える。本文差し替えは terms_hash が別途検出する。
TERMS_VERSION = "2026-09"

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

def _debug_enabled():
    """開発コンソール等のゲート。本番（POX_DEBUG 未設定/0）では無効。"""
    return os.environ.get("POX_DEBUG", "0") == "1"


# ── 認証ゲート（指示書22）───────────────────────────────────────
# 本人限定データは「?id= を知っていること」ではなく「セッションに紐づく本人であること」
# で守る。セッションの subject_id を唯一の身元とし、クエリ/パス/本文の id は受け取っても
# よいが、一致しなければ 403、セッションが無ければ 401（404 で隠さない）。
# POX_DEBUG=1 は /dev・/ledger と同じく開発バイパス（セッション無しでも通す）。

def current_subject_id():
    """ログイン中の本人 subject_id（マジックリンク認証で張ったセッション）。未ログインなら None。"""
    return session.get("subject_id")


def _auth_error(code, msg):
    """認証失敗の JSON 応答。フロントは 401 を見て /login へ誘導する（白画面にしない）。"""
    from flask import make_response
    return make_response(jsonify({"error": msg, "auth_required": (code == 401)}), code)


def login_required(fn):
    """認証ゲート（指示書22）。セッションが無ければ 401 を返す。POX_DEBUG=1 は開発バイパス。
    本人一致（403）の判定は各エンドポイントが require_self() で行う。"""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_subject_id() is None and not _debug_enabled():
            return _auth_error(401, "ログインが必要です")
        return fn(*args, **kwargs)

    return wrapper


def require_self(claimed_id):
    """権威ある本人 id を返す。セッションの subject_id を唯一の身元とし、claimed_id
    （クエリ/パス/本文の id）がそれと食い違えば 403 で abort する。セッションが無い場合は
    POX_DEBUG のときのみ claimed_id を信頼（login_required 通過後に呼ぶ前提）。"""
    sid = current_subject_id()
    if sid is None:
        return claimed_id  # DEBUG バイパス（login_required が許可済み）
    if claimed_id is not None and str(claimed_id) != str(sid):
        abort(_auth_error(403, "本人のみアクセスできます"))
    return sid


@app.get("/")
def index():
    # トップは「PoXとは」(about) に付け替え（指示書09 §3-5）。LP 相当の原稿は about に統合済み（指示書15）。
    # 旧 index コンソールは /dev に退避。
    return redirect("/about")


@app.get("/dev")
def dev_console():
    """旧トップの開発コンソール。POX_DEBUG=1 のときのみ表示（本番は 404）。"""
    if not _debug_enabled():
        abort(404)
    return render_template("index.html")


def _terms_hash() -> str:
    """規約本文（privacy.html）の SHA-256。版番号だけでは本文差し替えを検出できない（§3-3）。"""
    try:
        path = os.path.join(os.path.dirname(__file__), "templates", "privacy.html")
        with open(path, encoding="utf-8") as f:
            return sha256_hex(f.read())
    except Exception:  # noqa: BLE001
        return ""


def _safe_next(raw):
    """オープンリダイレクト防止: 同一サイト内パス（/... かつ //・スキーム無し）のみ許可。"""
    if not raw or not raw.startswith("/") or raw.startswith("//") or "://" in raw:
        return None
    return raw


@app.get("/login")
def login_page():
    # 401 で送られてきた元画面へ、ログイン後に戻れるように next を控える（指示書25 §4-2）。
    nxt = _safe_next(request.args.get("next"))
    if nxt:
        session["login_next"] = nxt
    else:
        session.pop("login_next", None)
    return render_template("login.html")


@app.post("/auth/request")
def auth_request():
    """メールアドレスを受け取り、単回・15分有効のマジックリンクを送る（§3-1）。"""
    body = request.get_json(force=True, silent=True) or request.form.to_dict()
    email = (body.get("email") or "").strip()
    if not auth.is_valid_email(email):
        return jsonify({"error": "メールアドレスの形式が正しくありません"}), 400
    token = auth.issue_token(email, db_path=DB)
    link = request.url_root.rstrip("/") + "/auth/verify?token=" + token
    result = mailer.send_magic_link(email, link)
    out = {"sent": True}
    # 開発モード（SMTP 未設定）かつ POX_DEBUG=1 のときのみリンクを返す。本番では返さない。
    if result.get("dev") and _debug_enabled():
        out["dev_link"] = result.get("dev_link")
    return jsonify(out), 200


@app.get("/auth/verify")
def auth_verify():
    """マジックリンクを検証してセッションを張る。初回のみ台帳へ subject.created / terms.accepted（§3-1）。"""
    token = request.args.get("token", "")
    email_hash = auth.consume_token(token, db_path=DB)
    if not email_hash:
        return render_template(
            "login.html",
            error="リンクが無効か、期限切れか、使用済みです。もう一度お試しください。",
        ), 400
    # アドレスの平文は保持していない。同一性は email_hash だけで採番・照合する（指示書26 §3）。
    subject_id, created = auth.get_or_create_identity_by_hash(email_hash, db_path=DB)
    session.permanent = True   # 30日の有効期限を適用（PERMANENT_SESSION_LIFETIME）
    session["subject_id"] = subject_id
    if created:
        # 初回のみ（§3-1 step4・§3-3）。best-effort: 失敗してもログインは成立させる。
        try:
            append_event(subject_id, "subject.created",
                         {"subject_id": subject_id, "kind": "individual"}, db_path=DB)
            append_event(subject_id, "terms.accepted",
                         {"subject_id": subject_id, "terms_version": TERMS_VERSION,
                          "terms_hash": _terms_hash()}, db_path=DB)
        except Exception as e:  # noqa: BLE001
            app.logger.warning(f"[auth] 台帳書き込みskip（ログインは成立）: {e}")
    # 401 から誘導された場合は元画面へ戻す（同一ブラウザのみ・§4-2）。既定はマイページ。
    nxt = _safe_next(session.pop("login_next", None))
    return redirect(nxt or f"/mypage?id={subject_id}")


@app.post("/auth/logout")
def auth_logout():
    session.pop("subject_id", None)
    return jsonify({"ok": True}), 200


@app.get("/api/me")
def api_me():
    """現在のセッション本人。フロントが操作の身元とログイン状態の把握に使う（指示書25 §2-3）。
    ログイン済み → 200 {subject_id}／未ログイン → 401 {auth_required:true}。
    個人情報は返さない（subject_id のみ）。プロフィール本体は公開ビューから取る。"""
    sid = current_subject_id()
    if sid is None:
        return jsonify({"auth_required": True}), 401
    return jsonify({"subject_id": sid}), 200


@app.get("/ledger/verify/<date>")
def ledger_verify(date):
    """日次アンカーの検証（§6-3）。保存 root をその日のイベントから再計算して照合。"""
    return jsonify(anchor.verify_date(date, db_path=DB)), 200


@app.get("/ledger/anchor/status")
def ledger_anchor_status():
    """最終アンカーの状態（指示書20 §3-1）。認証不要（公開情報）。

    strict=1（true/yes）のとき days_behind>=2 なら 503 を返す＝外形監視で停止を検知できる
    （cron-job.org が HTTP 失敗としてメール通知できる）。days_behind が None（アンカー皆無）
    や 1 以下なら 200。
    """
    st = anchor.anchor_status(db_path=DB)
    strict = (request.args.get("strict") or "").lower() in ("1", "true", "yes")
    # 是正（指示書20 追補）: アンカー皆無でもイベントがあれば異常＝503。空の台帳のみ 200。
    if strict and anchor.is_stale(db_path=DB):
        return jsonify(st), 503
    return jsonify(st), 200


def _anchor_token_ok():
    """POX_ANCHOR_TOKEN が設定され、X-Anchor-Token ヘッダが一致するか（外部スケジューラ用）。

    タイミング攻撃を避けるため secrets.compare_digest で定数時間比較する。
    """
    tok = os.environ.get("POX_ANCHOR_TOKEN", "")
    if not tok:
        return False
    return secrets.compare_digest(request.headers.get("X-Anchor-Token", ""), tok)


@app.post("/ledger/anchor")
def ledger_anchor():
    """日次 root を計算して anchor.published を追記する（§6）。

    起動経路は2つ:
      - Render Cron（render.yaml pox-anchor）や任意ランナーが scripts/daily_anchor.py を実行。
      - 無料の外部スケジューラ（cron-job.org / GitHub Actions 等）がこの endpoint を叩く。
        その場合は X-Anchor-Token ヘッダに POX_ANCHOR_TOKEN を付ける。POX_DEBUG=1 でも可。
    date を省略すると run_daily（最後のアンカー翌日〜当日をバックフィル）。date 指定は単日。
    """
    if not (_debug_enabled() or _anchor_token_ok()):
        abort(404)
    body = request.get_json(force=True, silent=True) or {}
    if body.get("date"):
        return jsonify(anchor.publish_anchor(body["date"], db_path=DB)), 200
    return jsonify(anchor.run_daily(db_path=DB)), 200


@app.post("/seekers")
@login_required
def post_seeker():
    """
    登録の受け口（修正指示書 v3.1 §3・§4）。
      raw_text があれば strip_code_fence → parse → normalize_to_seeker を通す。
      なければ body 自体を素JSONとみなして normalize する（後方互換）。

    登録はログイン後に行う（選択肢1）。プロフィール id は **セッションの subject_id に束縛**する。
    これにより「登録した id ≠ ログイン後の id」で登録が宙に浮く問題を解消し、
    同時に他人の id を指定して他人のプロフィールを上書きする経路も塞ぐ。
    （POX_DEBUG バイパス時のみ、セッションが無く body の user_id / 新規採番にフォールバック。）
    """
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON が読めません"}), 400

    raw_text = body.get("raw_text")
    # id はセッション本人に束縛（body の user_id は一致必須・不一致は 403）。§選択肢1。
    user_id  = require_self(body.get("user_id"))

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
        user_id = f"u_{uuid.uuid4().hex[:8]}"   # UUID由来・連番ではない（不変条件4・DEBUG時のみ到達）

    save_profile(user_id, seeker, db_path=DB)   # seeker + profile_view を同時保存（UPSERT）

    # プライバシーポリシー同意の証跡（指示書13・既存 consent とは別物・best-effort）。
    if body.get("privacy_policy_agreed"):
        try:
            record_policy_consent(user_id, str(body.get("privacy_policy_version") or ""), db_path=DB)
        except Exception as e:  # noqa: BLE001
            app.logger.warning(f"[policy-consent] 記録skip（登録は成功）: {e}")

    # dual-write: v4 形の貼り付けJSONなら Nomic 取り込みも非同期起動（非破壊・ベストエフォート）。
    # v4 側で何が起きても v3 登録は成功させる（登録を止めない）。
    v4_status = None
    try:
        if _dual_write_v4(user_id, raw):
            v4_status = "preparing"
    except Exception as e:  # noqa: BLE001
        app.logger.warning(f"[dual-write] v4 取り込みskip（v3は成功）: {e}")

    out = {"id": user_id, "handle": seeker.get("id")}
    if v4_status:
        out["v4_generation_status"] = v4_status
        out["v4_status_url"] = f"/v4/seekers/{user_id}/status"
    return jsonify(out), 201


@app.get("/seekers")
def get_seekers():
    """公開一覧: id・一行紹介・意志抜粋のみ（seeker 原文は返さない。指示書09 §3-2）。
    公開条件を満たす necessity があれば necessity_excerpt（冒頭）も添える（指示書11 §4-5）。"""
    rows = list_public_seeker_index(db_path=DB)
    if is_postgres():
        for r in rows:
            pub = get_public_necessity(r.get("id"))   # 数値なし・日時閾値ゲート済み
            if pub:
                t = pub["necessity_text"]
                r["necessity_excerpt"] = t[:60] + ("…" if len(t) > 60 else "")
    return jsonify(rows)


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


# ── 必要像の公開露出（指示書11）───────────────────────────────────────────────
# 公開してよいのは necessity_text と evidence_span のみ。数値（gate/gamma/p/α/β）は
# 公開しない。「今後の登録から」を generated_at の日時閾値で判定（既存分は非公開）。
# evidence_span は seeker 原文の引用のため、他人向けには出さず本人表示のみ（KH 判断・§7-3）。
_NECESSITY_PUBLIC_SINCE_DEFAULT = "2026-07-25T00:00:00+00:00"


def _necessity_public_since():
    from datetime import datetime, timezone
    raw = os.environ.get("POX_NECESSITY_PUBLIC_SINCE", _NECESSITY_PUBLIC_SINCE_DEFAULT)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = datetime.fromisoformat(_NECESSITY_PUBLIC_SINCE_DEFAULT)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fetch_necessity_public_cols(user_id):
    """store から公開列（necessity_text/evidence_span/generated_at）のみ取得。数値は取らない。"""
    if not is_postgres():
        return None
    try:
        from db_v4 import MODEL_TAG
        n = _v4_store().get_necessity_public(user_id, MODEL_TAG)
    except Exception:  # noqa: BLE001（表示は best-effort・照合や保存には影響させない）
        return None
    if not n or not (n.get("necessity_text") or "").strip():
        return None
    return n


def get_public_necessity(user_id):
    """他人向け公開版: 日時閾値を満たすときだけ necessity_text のみ返す（数値・根拠は返さない）。"""
    from datetime import timezone
    n = _fetch_necessity_public_cols(user_id)
    if n is None:
        return None
    gen = n.get("generated_at")
    if gen is None:
        return None
    if getattr(gen, "tzinfo", None) is None:
        gen = gen.replace(tzinfo=timezone.utc)
    if gen < _necessity_public_since():
        return None   # 「今後の登録から」＝閾値より前に生成された既存分は非公開
    return {"necessity_text": n["necessity_text"]}


def get_owner_necessity(user_id):
    """本人向け: 公開条件に関わらず necessity_text＋evidence_span（数値は出さない・§4-4）。"""
    n = _fetch_necessity_public_cols(user_id)
    if n is None:
        return None
    return {"necessity_text": n["necessity_text"], "evidence_span": n.get("evidence_span") or ""}


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


def _v4_async_job(profile_id, profile_input, necessity, *, is_fallback,
                  final_status=None, snapshot=False):
    """
    非同期ジョブ（daemon スレッド）: 必要像生成（フォールバック時のみ）＋4ベクトル生成。
    生成状態は generation_status（preparing→ready / error）で追跡する。
    - user-supplied 経路: necessity は受付時に検証・保存済み → ベクトル化のみ。
    - fallback 経路: ここで ② サーバー生成（ANTHROPIC_API_KEY 必須）→ 保存 → ベクトル化。
    - final_status を渡すと、ベクトル化成功後に status を ready ではなくその値へ上書きする
      （編集再ベクトル化: 新ベクトル＋旧必要像＝needs_regeneration を維持。指示書08 §3-3）。
    - snapshot=True（登録経路のみ）: ベクトル化成功後に user_snapshots へ1点保存（指示書12 §4-1）。
      編集・retry では False＝スナップショットを作らない。
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
        if final_status is not None:  # 編集経路: ready を上書きして needs_regeneration を維持
            store.set_generation_status(profile_id, final_status, error=None)
        if snapshot:                  # 登録経路のみ: 時点スナップショットを1点（churn は関数側で防止）
            _save_snapshot_best_effort(profile_id, profile_input, necessity)
        # 主体の構造化時点を台帳へ（指示書17 §5-2/§5-3・best-effort・churn は関数側で防止）。
        _publish_profile_structured_best_effort(profile_id, profile_input)
        # 必要像を 1:N 台帳へ記録＋ベクトル化（指示書17 §7・best-effort・churn は関数側で防止）。
        _publish_necessity_best_effort(profile_id, profile_input, necessity)
    except Exception as e:  # noqa: BLE001
        try:
            store.set_generation_status(profile_id, GEN_ERROR, error=str(e)[:500])
        except Exception:
            pass


SNAPSHOT_SCHEMA_VERSION = "v4.3"   # スナップショットに記録するスキーマ版（指示書12改訂 §3-1）


def _save_snapshot_best_effort(profile_id, profile_input, necessity):
    """再構造化の時点スナップショットを保存（失敗しても登録/ベクトル化には影響させない）。"""
    try:
        from snapshots import save_snapshot
        nec = necessity or {}
        save_snapshot(
            profile_id,
            will_text=profile_input.get("will_text", ""),
            state={k: profile_input.get(k, "") for k in
                   ("state_have", "state_can_type", "state_bound", "state_unsorted")},
            supporting=profile_input.get("supporting_raw") or {},
            necessity=nec,
            src_input_hash=nec.get("src_input_hash"),
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            db_path=DB,
        )
    except Exception:  # noqa: BLE001
        pass


def _publish_profile_structured_best_effort(profile_id, profile_input):
    """構造化プロフィールの時点を台帳へ記録（指示書17 §5-2/§5-3・best-effort）。

    失敗しても登録・照合・ベクトル化本体に影響させない。churn は関数側で防止。
    individual のため members_after_hash は None。
    """
    try:
        from subject_ledger import publish_profile_structured
        publish_profile_structured(profile_id, profile_input, actor=profile_id, db_path=DB)
    except Exception as e:  # noqa: BLE001
        app.logger.warning(f"[profile.structured] 記録skip（本体は成功）: {e}")


def _publish_necessity_best_effort(profile_id, profile_input, necessity):
    """必要像を 1:N 台帳（necessities）へ記録し、必要像ベクトルを実体化する（指示書17 §7）。

    失敗しても登録・照合・ベクトル化本体には一切影響させない（best-effort）。
    - origin='generated'（①プロンプト由来／サーバー②生成のいずれも差分導出）。
    - content_hash が直前と同一なら関数側で churn スキップ（編集の再ベクトル化で本文が
      変わらないケースを弾く）。
    - vectorize_necessity は build_vectors と同一の embed() を通す（本番 nomic／ローカル stub）。
    """
    try:
        if not necessity:
            return
        import necessities as _nec
        nec_in = {**necessity, "will_text": profile_input.get("will_text", "")}
        r = _nec.publish_necessity(
            profile_id, "subject", nec_in,
            origin="generated", generator=necessity.get("generator_name") or "",
            actor=profile_id, db_path=DB,
        )
        if not r.get("skipped"):
            _nec.vectorize_necessity(r["necessity_id"], db_path=DB)
    except Exception as e:  # noqa: BLE001
        app.logger.warning(f"[necessity-1:N] 記録skip（本体は成功）: {e}")


def _spawn_v4_job(profile_id, profile_input, necessity, *, is_fallback,
                  final_status=None, snapshot=False):
    t = threading.Thread(
        target=_v4_async_job, args=(profile_id, profile_input, necessity),
        kwargs={"is_fallback": is_fallback, "final_status": final_status,
                "snapshot": snapshot}, daemon=True)
    t.start()


def _ingest_v4_from_flat(body, *, profile_id=None):
    """
    フラット化済み body から v4 受付＋非同期ベクトル化を起動する共通処理
    （/v4/seekers と /seekers の dual-write が共有）。

    必要像フィールドがあれば正式ルート（build_user_necessity で検証・クランプ、
    γ は compute_gamma 算出）、無ければフォールバック（サーバー生成）。
    build_user_necessity の ValueError/TypeError は呼び出し側で 400 等に写す。
    戻り値: (profile_id, necessity or None, is_fallback)。
    """
    from db_v4 import receive_profile_v4, GEN_PREPARING
    from necessity_gen import build_user_necessity
    from pii_redaction import redact_for_storage

    pid = profile_id or body.get("user_id") or f"u_{uuid.uuid4().hex[:8]}"
    profile_input = _v4_profile_input(body)
    store = _v4_store()

    supplied = (body.get("necessity_text") or "").strip()
    is_fallback = not supplied

    necessity = None
    if not is_fallback:
        # 各自AI出力を無検証で信頼せず検証・クランプ。src_input_hash は保存後の
        # 再生成判定（get_profile 経由）と一致させるため同じ supporting_redacted を渡す。
        supporting_redacted, _ = redact_for_storage(profile_input.get("supporting_raw") or {})
        hash_profile = {**profile_input, "supporting_redacted": supporting_redacted}
        necessity = build_user_necessity(hash_profile, body)

    receive_profile_v4(store, pid, profile_input, necessity,
                       generation_status=GEN_PREPARING)
    # snapshot=True: 登録/再構造化のみ時点スナップショットを残す（編集・retry は残さない）。
    _spawn_v4_job(pid, profile_input, necessity, is_fallback=is_fallback, snapshot=True)
    return pid, necessity, is_fallback


def _dual_write_v4(profile_id, raw):
    """
    v3 登録（/seekers）と同時に、貼り付けJSONから v4(Nomic) 取り込みを非同期起動する。
    非破壊・ベストエフォート: v4 側の失敗は v3 登録に影響させない（呼び出し側で握る）。
    v4 形（seeker/necessity ネスト）でなく意志が空なら何もしない（旧v3.1 等はv4化しない）。
    v3 と同じ profile_id で揃える（二層が同一キーで対応）。
    """
    if not is_postgres():
        return False
    flat = _normalize_v4_body(raw)
    if not isinstance(flat, dict) or not (flat.get("will_text") or "").strip():
        return False  # v4 化する意志テキストが無い → v3 のみ
    _ingest_v4_from_flat(flat, profile_id=profile_id)
    return True


@app.post("/v4/seekers")
@login_required
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

    from db_v4 import GEN_PREPARING

    # id はセッション本人に束縛（選択肢1）。body の user_id は一致必須・不一致は 403。
    # DEBUG バイパス時のみ body / 新規採番にフォールバック。
    profile_id = require_self(body.get("user_id")) or f"u_{uuid.uuid4().hex[:8]}"
    try:
        profile_id, necessity, is_fallback = _ingest_v4_from_flat(body, profile_id=profile_id)
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"必要像フィールド不正: {e}"}), 400
    except Exception as e:
        return jsonify({"error": f"受付失敗: {e}"}), 500

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


def _seeker_live_necessity_id(seeker_id, *, db_path=None):
    """seeker 本人の、ベクトル化済みで生きている最新の必要像 id（無ければ None・§7-5）。"""
    dbp = db_path or DB
    try:
        from necessities import get_live_necessities, query_vectors
        live = [n for n in get_live_necessities(seeker_id, db_path=dbp)
                if query_vectors(n["necessity_id"], db_path=dbp)]
        if not live:
            return None
        return max(live, key=lambda n: n["n"])["necessity_id"]
    except Exception:  # noqa: BLE001
        return None


def _match_by_necessity(store, necessity_id, *, model_tag, top_k=None, write_ledger=True,
                        db_path=None):
    """必要像を query 単位として候補をランキング（§7-5）。matcher 内部は不変（rank_candidates）。

    必要像レコード（query 側2本＋数値）＋ owner 主体の will_passage（1:1）で seeker 束を組む。
    未ベクトル化・owner 束欠落など前提が欠ければ LookupError（呼び出し側が人起点へフォールバック）。
    """
    dbp = db_path or DB
    from necessities import get_necessity, query_vectors
    from matching_necessity import rank_for_necessity

    nec = get_necessity(necessity_id, db_path=dbp)
    if not nec:
        raise LookupError("necessity が見つかりません")
    qv = query_vectors(necessity_id, db_path=dbp)
    if not qv:
        raise LookupError("necessity が未ベクトル化です")
    owner = nec["owner_ref"]
    owner_bundle = store.get_bundle(owner, model_tag)
    if not owner_bundle or "will_passage" not in (owner_bundle.get("vectors") or {}):
        raise LookupError("owner の主体ベクトルがありません")
    owner_wp = owner_bundle["vectors"]["will_passage"]

    cand_ids = store.candidate_ids(owner, model_tag)
    cand_bundles = store.get_bundles(cand_ids, model_tag)
    cand_list = [(cid, b["vectors"]) for cid, b in cand_bundles.items()]

    numbers = {k: nec.get(k) for k in ("gate_s", "gate_u", "p_sharpness", "alpha", "beta")}
    results = rank_for_necessity(numbers, qv, owner_wp, cand_list, top_k=top_k)

    if write_ledger:
        for r in results:
            attr = r["attribution"]
            try:
                store.write_ledger(owner, r["candidate_id"], "match_ranked", {
                    "score": r["score"], "limiting_axis": attr["limiting_axis"],
                    "a_sim": attr["a_sim"], "b_sim": attr["b_sim"], "c_sim": attr["c_sim"],
                    "model_tag": model_tag, "necessity_id": necessity_id,
                    "query_unit": "necessity",
                })
            except Exception:  # noqa: BLE001
                pass

    return {"seeker_id": owner, "necessity_id": necessity_id, "query_unit": "necessity",
            "model_tag": model_tag, "results": results, "pool_size": len(cand_list)}


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
    from embedding_config import MODEL_TAG
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

    # §7-5: query 単位を必要像へ。明示 necessity_id か、seeker 本人の生きた必要像があれば
    # 必要像起点で照合し、無い/未ベクトル化なら人起点へフォールバック（個人は同一結果）。
    nec_id = body.get("necessity_id") or _seeker_live_necessity_id(seeker_id, db_path=DB)
    out = None
    if nec_id:
        try:
            out = _match_by_necessity(store, nec_id, model_tag=MODEL_TAG, top_k=top_k)
        except LookupError:
            out = None
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": f"照合失敗: {e}"}), 500
    if out is None:
        try:
            out = match_v4(store, seeker_id, top_k=top_k)
            out["query_unit"] = "person"
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            return jsonify({"error": f"照合失敗: {e}"}), 500

    out["match_run_id"] = f"run_{uuid.uuid4().hex[:8]}"
    return jsonify(out)


@app.post("/approve")
@login_required
def post_approve():
    """接続の承認（相互承認で connection.established を台帳へ）。承認する本人(from_id)
    セッション限定にゲート（指示書23 §4）。偽の承認で「両者が合意した」記録が残るのを防ぐ。"""
    body = request.get_json(force=True, silent=True)
    if body is None:
        abort(400, "JSON が読めません")

    from_id = require_self(body.get("from_id"))
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
        channel=body.get("channel"),
        phase=None,
        db_path=DB,
        establish_hook=_snapshot_pair_resolver,   # 成立時に両者の最新スナップショットを結合（指示書12）
        ref_resolver=_conn_ref_resolver,          # 接続の根拠（指示書18 §1）
        require_grounding=True,                    # 両者に profile.structured が無ければ成立させない（§1-3）
    )
    return jsonify(result), 200


def _snapshot_pair_resolver(founder, joiner):
    """成立時: 両者の最新 snapshot_id を返す（未再構造化は None）。指示書12 §4-2。"""
    try:
        from snapshots import latest_snapshot_id
        return {founder: latest_snapshot_id(founder, db_path=DB),
                joiner:  latest_snapshot_id(joiner, db_path=DB)}
    except Exception:  # noqa: BLE001
        return None


def _conn_ref_resolver(subject):
    """接続の根拠（指示書18 §1-2）: profile.structured の content_hash と
    necessity.published の event_hash（無ければ None）。null と空文字を区別する。"""
    try:
        from subject_ledger import latest_profile_content_hash
        from necessities import latest_published_event_hash
        return {"profile_snapshot_hash": latest_profile_content_hash(subject, db_path=DB),
                "necessity_hash": latest_published_event_hash(subject, db_path=DB)}
    except Exception:  # noqa: BLE001
        return {"profile_snapshot_hash": None, "necessity_hash": None}


_NEC_NUM_KEYS = ("gate_s", "gate_u", "gamma", "p_sharpness", "alpha", "beta")


def _timeline_is_partner(user_id, viewer, vessels):
    """viewer が user_id と成立済み接続を持つ当事者の相手か（指示書12改訂 §4-3）。"""
    if not viewer or viewer == user_id:
        return False
    for v in vessels:
        join = (v.get("joins") or [{}])[0]
        parties = {v.get("founder"), join.get("joiner")}
        if parties == {user_id, viewer} and join.get("established_at"):
            return True
    return False


@app.get("/api/timeline/<user_id>")
def api_timeline(user_id):
    """
    軌跡（指示書12改訂 §4-3）: user_snapshots と接続の履歴事実を時系列マージ。
    閲覧者3層で絞る:
      - 本人      : 全部（will/state/necessity_text/evidence_span/数値）
      - 当事者の相手: will/state/necessity_text（evidence_span・数値なし。vulnerable_hidden でも中身表示）
      - 第三者    : necessity_text のみ。vulnerable_hidden の時点は中身を出さない（存在の事実は残す）
    接続の事実（成立・離脱）は全層に公開。**理由は一切含めない**（原則3）。
    viewer は簡易（クエリ id）。認証が無いため厳密でない（§7-5 の限界）。
    """
    viewer = request.args.get("viewer")
    is_owner = bool(viewer) and viewer == user_id

    try:
        vessels = load_all_vessels(db_path=DB)
    except Exception:  # noqa: BLE001
        vessels = []
    is_partner = _timeline_is_partner(user_id, viewer, vessels)
    viewer_role = "owner" if is_owner else ("partner" if is_partner else "third")

    items = []
    try:
        from snapshots import get_snapshots
        snaps = get_snapshots(user_id, db_path=DB)
    except Exception:  # noqa: BLE001
        snaps = []
    for s in snaps:
        nec = s.get("necessity") or {}
        hidden = bool(s.get("vulnerable_hidden"))
        item = {"kind": "snapshot", "at": s.get("created_at"),
                "snapshot_id": s.get("snapshot_id"),
                "schema_version": s.get("schema_version", "")}
        if viewer_role == "third" and hidden:
            item["hidden"] = True            # 存在の事実のみ・中身は出さない
        elif viewer_role in ("owner", "partner"):
            item["will_text"] = s.get("will_text", "")
            item["state"] = s.get("state") or {}
            item["necessity_text"] = nec.get("necessity_text", "")
            item["vulnerable_hidden"] = hidden   # 本人UIのトグル表示用
            if viewer_role == "owner":           # 根拠・数値は本人のみ
                item["evidence_span"] = nec.get("evidence_span", "")
                item["numbers"] = {k: nec.get(k) for k in _NEC_NUM_KEYS}
        else:                                # 第三者・非hidden: necessity_text のみ
            item["necessity_text"] = nec.get("necessity_text", "")
        items.append(item)

    # 接続の履歴事実（全層公開・理由なし）。既存の snapshots キー無し vessel でも壊れない。
    for v in vessels:
        join = (v.get("joins") or [{}])[0]
        founder, joiner = v.get("founder"), join.get("joiner")
        if user_id not in (founder, joiner):
            continue
        other = joiner if user_id == founder else founder
        est = join.get("established_at")
        if est:
            items.append({"kind": "connection", "event": "established", "at": est,
                          "other": other, "vessel_id": v.get("vessel_id")})
        ts = join.get("terminal_state")   # 離脱・解消は「事実」として（理由は持たない）
        if ts and ts not in ("active", None) and join.get("closed_at"):
            items.append({"kind": "connection", "event": "ended", "at": join.get("closed_at"),
                          "other": other, "vessel_id": v.get("vessel_id")})

    items.sort(key=lambda x: x.get("at") or "")
    return jsonify({"user_id": user_id, "is_owner": is_owner,
                    "viewer_role": viewer_role, "items": items})


@app.post("/api/snapshot/<snapshot_id>/visibility")
@login_required
def api_snapshot_visibility(snapshot_id):
    """
    本人が時点の中身を第三者に伏せる/戻す（指示書12改訂 §4-4）。所有者一致のときのみ。
    消すのではなく第三者表示を止めるだけ。接続の結末は対象外（伏せられない）。
    §4-2 の指摘: hidden=false で「公開方向」に戻せる（＝第三者へ再露出）ため、
    id を知る第三者による書き換えを防ぐ必要がある → セッション本人限定にゲート（指示書22 第1群）。
    """
    body = request.get_json(force=True, silent=True) or {}
    owner_id = require_self(body.get("id"))
    if not owner_id:
        return jsonify({"error": "id が必要です"}), 400
    hidden = bool(body.get("hidden"))
    from snapshots import set_snapshot_hidden
    ok = set_snapshot_hidden(snapshot_id, owner_id, hidden, db_path=DB)
    if not ok:
        return jsonify({"error": "対象のスナップショットが見つかりません（所有者のみ変更できます）"}), 404
    return jsonify({"ok": True, "snapshot_id": snapshot_id, "vulnerable_hidden": hidden})


_VISIBILITY_SCOPES = ("public", "private")


@app.post("/api/profile/<user_id>/visibility")
@login_required
def api_profile_visibility(user_id):
    """本人がプロフィールの公開範囲を変更（指示書17 §5: visibility.changed）。

    セッション本人限定（指示書22 第1群）。path/body の id はセッションの subject_id と
    一致必須（不一致は 403・未ログインは 401）。scope は public|private。
    profiles.visibility を更新し、visibility.changed を台帳へ（churn は関数側で防止）。
    """
    body = request.get_json(force=True, silent=True) or {}
    require_self(user_id)  # path が本人（セッション）と一致しなければ 403
    require_self((body.get("id") or "").strip() or None)  # body の id も一致必須（明示指定時）
    scope = (body.get("scope") or "").strip()
    if scope not in _VISIBILITY_SCOPES:
        return jsonify({"error": f"scope は {'/'.join(_VISIBILITY_SCOPES)}"}), 400
    if not set_profile_visibility(user_id, scope, db_path=DB):
        return jsonify({"error": "プロフィールが見つかりません"}), 404
    try:
        from subject_ledger import publish_visibility_changed
        publish_visibility_changed(user_id, scope, actor=user_id, db_path=DB)
    except Exception as e:  # noqa: BLE001
        app.logger.warning(f"[visibility.changed] 記録skip（変更は成功）: {e}")
    return jsonify({"ok": True, "id": user_id, "scope": scope}), 200


@app.get("/api/my/vessels")
@login_required
def api_my_vessels():
    """
    当事者本人が関わる vessel のみ返す（指示書09 §3-4）。全台帳の無認証公開を廃止し、
    mypage/inbox がクライアント側で行っていた絞り込み（founder==id または joins[0].joiner==id）
    をサーバー側に移す。返す vessel 構造は現状のまま（当事者には全情報が見えてよい）。
    本人限定の接続情報のため、セッション本人限定にゲート（指示書22 第2群）。
    """
    my_id = require_self(request.args.get("id"))
    if not my_id:
        return jsonify({"error": "id が必要です"}), 400
    mine = [
        v for v in load_all_vessels(db_path=DB)
        if v.get("founder") == my_id
        or ((v.get("joins") or [{}])[0].get("joiner") == my_id)
    ]
    return jsonify(mine)


@app.get("/ledger")
def get_ledger():
    """全 vessel（開発コンソール台帳用）。POX_DEBUG=1 のときのみ（本番は 404）。
    当事者向けは /api/my/vessels を使う。"""
    if not _debug_enabled():
        abort(404)
    return jsonify(load_all_vessels(db_path=DB))


# 旧 GET /seekers/<id>（単体 seeker 原文・消費者ゼロのオーファン）は指示書09 §3-1 で削除。


@app.get("/api/profile/<user_id>")
def api_profile(user_id):
    """profile_view（view_overrides 適用済み）のみ返す。seeker は絶対に返さない。
    公開条件を満たす necessity があれば necessity_text だけをマージ（数値・evidence_span は出さない・指示書11）。"""
    pv = get_profile_view(user_id, db_path=DB)
    if pv is None:
        return jsonify({"error": "プロフィールが見つかりません"}), 404
    pub_nec = get_public_necessity(user_id)   # None なら足さない（既存分・未生成は出ない）
    if pub_nec:
        pv["necessity_text"] = pub_nec["necessity_text"]
    return jsonify(pv)


@app.get("/api/my/necessity")
@login_required
def api_my_necessity():
    """本人向け: 自分の必要像（necessity_text＋evidence_span）を返す。数値は出さない（§4-4）。
    evidence_span は本人限定情報のため、セッション本人限定にゲート（指示書22 第1群）。
    公開条件に関わらず本人は自分の必要像を見られる。"""
    my_id = require_self(request.args.get("id"))
    if not my_id:
        return jsonify({"error": "id が必要です"}), 400
    nec = get_owner_necessity(my_id)
    return jsonify(nec or {})


@app.get("/api/profile/<user_id>/edit")
@login_required
def api_profile_edit(user_id):
    """「見せ方を編集」用。base profile_view と現在の view_overrides を返す（seeker原文は返さない）。
    編集前の view_overrides は本人限定情報のため、セッション本人限定にゲート（指示書22 第2群）。"""
    require_self(user_id)  # path が本人（セッション）と一致しなければ 403 / 未ログインは 401
    data = get_profile_edit_data(user_id, db_path=DB)
    if data is None:
        return jsonify({"error": "プロフィールが見つかりません"}), 404
    return jsonify(data)


def _revectorize_after_edit(profile_id):
    """
    「中身を編集」で意志/現状が変わったとき、登録と同じ経路で v4 ベクトルを作り直す。
    必要像は各自AIの所有物のため **サーバー生成しない**（is_fallback=False）。
    既存の（古い）必要像テキストで再ベクトル化し、status=needs_regeneration を維持する。
    derived_necessity は一切触らない。指示書08 §3-2/§3-3。
    v4 未登録・必要像未保存・非Postgres なら何もしない（v3 編集は既に保存済み）。
    """
    if not is_postgres():
        return
    from db_v4 import receive_profile_v4, GEN_NEEDS_REGEN, MODEL_TAG
    store = _v4_store()
    existing = store.get_profile(profile_id)
    if existing is None:
        return  # v4 に未登録 → 再ベクトル化対象なし
    nec = store.get_necessity(profile_id, MODEL_TAG)
    if not nec or not (nec.get("necessity_text") or "").strip():
        return  # 既存必要像が無い → ② 生成はしない方針のため再ベクトル化しない

    seeker = get_seeker(profile_id, db_path=DB) or {}
    jotai = seeker.get("現状") if isinstance(seeker.get("現状"), dict) else {}
    profile_input = {
        "will_text":      str(seeker.get("意志") or ""),
        "state_have":     str(jotai.get("持っているもの") or ""),
        "state_can_type": str(jotai.get("できること_型") or ""),
        "state_bound":    str(jotai.get("縛られているもの") or ""),
        "state_unsorted": str(jotai.get("未分類") or ""),
        "supporting_raw": existing.get("supporting_raw") or {},  # 既存 v4 素材を保持
    }
    # profiles_v4 の行を更新（will/state・necessity=None で derived_necessity 不変）→ needs_regeneration
    receive_profile_v4(store, profile_id, profile_input, necessity=None,
                       generation_status=GEN_NEEDS_REGEN)
    # ベクトルのみ再計算（②生成なし）。最終 status は needs_regeneration を維持。
    _spawn_v4_job(profile_id, profile_input, nec, is_fallback=False,
                  final_status=GEN_NEEDS_REGEN)


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
    out = {}
    if not update_seeker_core(user_id, fields, db_path=DB, out=out):
        return jsonify({"error": "プロフィールが見つかりません"}), 404

    # 意志/現状が変わったら v4 を再ベクトル化＋必要像を needs_regeneration に（ベストエフォート）。
    changed = bool(out.get("changed_core"))
    if changed:
        try:
            _revectorize_after_edit(user_id)
        except Exception as e:  # noqa: BLE001
            app.logger.warning(f"[edit-revectorize] skip（v3編集は保存済み）: {e}")
    return jsonify({"ok": True, "changed_core": changed})


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


@app.get("/connect")
def connect():
    """「つながる」入口（指示書10）。v4 照合のおすすめ＋登録者一覧を出す表示ページ。"""
    return render_template("connect.html")


@app.get("/privacy")
def privacy():
    """プライバシーポリシー（指示書13）。認証不要・誰でも閲覧可。"""
    return render_template("privacy.html")


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
@login_required
def post_message():
    """DM 送信。送信者(from_id)はセッション本人に束縛（指示書26）。未ログインは 401・なりすましは 403。"""
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    from_id = require_self(body.get("from_id"))
    to_id   = body.get("to_id")
    msg_body = (body.get("body") or "").strip()
    attachment_url = body.get("attachment_url")
    if not from_id or not to_id or (not msg_body and not attachment_url):
        return jsonify({"error": "from_id, to_id, body か attachment_url が必要です"}), 400
    msg = send_message(from_id, to_id, msg_body, attachment_url=attachment_url, db_path=DB)
    return jsonify(msg), 201


@app.get("/api/conversation")
@login_required
def api_conversation():
    """本人が当事者の会話のみ返す。me はセッション本人限定にゲート（指示書22 第2群）。"""
    me    = require_self(request.args.get("me"))
    other = request.args.get("with")
    if not me or not other:
        return jsonify({"error": "me と with が必要です"}), 400
    msgs = get_conversation(me, other, db_path=DB)
    return jsonify(msgs)


# ── ファイルアップロード API ──────────────────────────────────
@app.post("/api/upload")
@login_required
def api_upload():
    """添付アップロード。メッセージ送信の一部のためログイン必須（指示書26）。未ログインは 401。"""
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
@login_required
def api_inbox():
    """本人の受信箱。id はセッション本人限定にゲート（指示書22 第2群）。"""
    my_id = require_self(request.args.get("id"))
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
@login_required
def api_create_community():
    """コミュニティ作成（創設者の member.joined を台帳へ）。台帳に書くため本人セッション限定に
    ゲート（指示書25 §3・指示書23 §4 の記述誤り訂正）。founder_id はセッションと一致必須。"""
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    name        = body.get("name", "").strip()
    description = body.get("description", "")
    founder_id  = require_self((body.get("founder_id") or "").strip() or None) or ""
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
    # 宣言と実績（指示書23 §3-1）。第三者（未ログイン）にも見える。数値は出さない。
    from intent_ledger import list_intent_details, latest_policy_declaration
    declaration = latest_policy_declaration(community_id, db_path=DB)
    intents = list_intent_details(community_id, db_path=DB)
    return jsonify({**c, "members": members, "pending": pending, "messages": messages,
                    "declaration": declaration, "intents": intents})


@app.post("/api/community/<community_id>/join")
@login_required
def api_join_community(community_id):
    """参加申請（承認で member.joined を台帳へ）。申請する本人セッション限定にゲート（指示書23 §4）。"""
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    member_id = require_self((body.get("member_id") or "").strip() or None) or ""
    if not member_id:
        return jsonify({"error": "member_id が必要です"}), 400
    result = request_join(community_id, member_id, db_path=DB)
    return jsonify(result), 200


@app.post("/api/community/<community_id>/approve")
@login_required
def api_approve_member(community_id):
    """承認（member.joined を台帳へ）。承認者(approver)本人セッション限定にゲート（指示書23 §4）。
    member_id は承認される相手なのでゲート対象外（approver が自分であることのみ確認）。"""
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    member_id   = (body.get("member_id") or "").strip()
    approver_id = require_self((body.get("approver_id") or "").strip() or None) or ""
    if not member_id or not approver_id:
        return jsonify({"error": "member_id と approver_id が必要です"}), 400
    if not is_founder(community_id, approver_id, db_path=DB):
        return jsonify({"error": "承認権限がありません"}), 403
    result = approve_member(community_id, member_id, db_path=DB)
    return jsonify(result), 200


@app.post("/api/community/<community_id>/intent/propose")
@login_required
def api_intent_propose(community_id):
    """意志形成を提起（§5-3）。提起者は ctx の active メンバーであること。AI 非依存。
    台帳に intent.proposed を書くため、本人セッション限定にゲート（指示書23 §4）。"""
    body = request.get_json(force=True, silent=True) or {}
    proposer = require_self((body.get("proposer") or "").strip() or None) or ""
    if not proposer:
        return jsonify({"error": "proposer が必要です"}), 400
    from member_ledger import active_members_from_events
    if proposer not in active_members_from_events(community_id, db_path=DB):
        return jsonify({"error": "提起はコミュニティのメンバーのみ"}), 403
    from intent_ledger import propose_intent
    return jsonify(propose_intent(
        community_id, proposer, body=body.get("body") or "",
        declaration=body.get("declaration") or "",
        ruleset_version=body.get("ruleset_version") or "r1", db_path=DB)), 200


@app.post("/api/intent/<intent_id>/agree")
@login_required
def api_intent_agree(intent_id):
    """合意（intent.agreed を台帳へ）。本人セッション限定にゲート（指示書23 §4）。
    偽の承認が台帳に永久に残るのを防ぐ（追記専用で訂正できないため）。"""
    body = request.get_json(force=True, silent=True) or {}
    subject = require_self((body.get("subject") or "").strip() or None) or ""
    if not subject:
        return jsonify({"error": "subject が必要です"}), 400
    from intent_ledger import agree_intent
    r = agree_intent(intent_id, subject, db_path=DB)
    if r.get("error"):
        return jsonify(r), 404
    return jsonify(r), 200


@app.post("/api/intent/<intent_id>/complete")
@login_required
def api_intent_complete(intent_id):
    """完了（intent.completed を台帳へ）。本人セッション限定にゲート（指示書23 §4）。"""
    body = request.get_json(force=True, silent=True) or {}
    by = require_self((body.get("by") or "").strip() or None) or ""
    if not by:
        return jsonify({"error": "by が必要です"}), 400
    from intent_ledger import complete_intent
    r = complete_intent(intent_id, by, result=body.get("result") or "", db_path=DB)
    if r.get("error") == "not_found":
        return jsonify(r), 404
    if r.get("error"):
        return jsonify(r), 409       # not_agreed / cancelled
    return jsonify(r), 200


@app.post("/api/intent/<intent_id>/cancel")
@login_required
def api_intent_cancel(intent_id):
    """取消（intent.cancelled を台帳へ）。本人セッション限定にゲート（指示書23 §4）。"""
    body = request.get_json(force=True, silent=True) or {}
    by = require_self((body.get("by") or "").strip() or None) or ""
    if not by:
        return jsonify({"error": "by が必要です"}), 400
    from intent_ledger import cancel_intent
    r = cancel_intent(intent_id, by, reason=body.get("reason") or "", db_path=DB)
    if r.get("error") == "not_found":
        return jsonify(r), 404
    return jsonify(r), 200


@app.post("/api/intent/<intent_id>/participant/join")
@login_required
def api_intent_participant_join(intent_id):
    """目的別参加（intent.participant.joined を台帳へ）。参加する本人セッション限定にゲート
    （指示書23 §4）。introduced_by は本人の参加イベント内にしか書けない（§1-6）。"""
    body = request.get_json(force=True, silent=True) or {}
    participant = require_self((body.get("participant") or "").strip() or None) or ""
    if not participant:
        return jsonify({"error": "participant が必要です"}), 400
    from intent_ledger import join_participant
    r = join_participant(intent_id, participant,
                         introduced_by=body.get("introduced_by"),
                         approved_by=body.get("approved_by"), db_path=DB)
    if r.get("error"):
        return jsonify(r), 404
    return jsonify(r), 200


@app.get("/api/intent/<intent_id>")
def api_intent_get(intent_id):
    from intent_ledger import get_intent
    r = get_intent(intent_id, db_path=DB)
    if not r:
        return jsonify({"error": "not_found"}), 404
    return jsonify(r), 200


@app.get("/api/community/<community_id>/intents")
def api_community_intents(community_id):
    from intent_ledger import list_intents
    return jsonify({"ctx": community_id, "intents": list_intents(community_id, db_path=DB)}), 200


@app.post("/api/community/<community_id>/leave")
@login_required
def api_leave_community(community_id):
    """自主離脱（member.left を台帳へ）。自分自身のみ（追い出しは不可・§2-1）。
    台帳に書くため本人セッション限定にゲート（指示書25 §3）。member_id はセッションと一致必須。"""
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    member_id = require_self((body.get("member_id") or "").strip() or None) or ""
    if not member_id:
        return jsonify({"error": "member_id が必要です"}), 400
    from community import leave_community
    return jsonify(leave_community(community_id, member_id, db_path=DB)), 200


@app.post("/api/community/<community_id>/message")
@login_required
def api_community_message(community_id):
    """コミュニティチャット投稿。送信者はセッション本人に束縛＋メンバー限定（指示書26）。
    未ログインは 401・なりすましは 403・非メンバーは 403。"""
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    from_id  = require_self((body.get("from_id") or "").strip() or None) or ""
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
