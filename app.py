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
import sys, os, uuid, secrets, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from flask import Flask, request, jsonify, render_template, abort, redirect, url_for, session, g, Response
from werkzeug.utils import secure_filename
from db_connect import is_postgres
import auth, mailer, anchor, drafts
from ledger_events import append_event
from canon import sha256_hex
from db import (save_seeker, load_all_seekers, save_profile, get_profile_view,
                get_seeker, list_candidate_pool, get_profile_edit_data,
                save_view_overrides, update_seeker_core, list_directory_ids,
                record_policy_consent, set_profile_visibility, get_profile_visibility,
                get_view_overrides)
from profile_view import parse_registration_text, normalize_to_seeker
from connection_layer import run_matching
from ledger import approve, load_all_vessels
from messages import send_message, get_conversation, get_inbox_summary, get_unread_count
from community import (create_community, get_community, get_all_communities,
                       request_join, approve_member, get_members, is_member,
                       is_founder, get_pending_requests, update_community,
                       has_founder_rights)
from messages import get_community_messages

app = Flask(__name__)
DB = os.environ.get("POX_DB", "pox.db")


# ── 公開衛生（指示書45 A-3）─────────────────────────────────────────────────
# メンバー限定面（加入トーク・チャット・参加申請・本人面）のページと API には
# X-Robots-Tag: noindex, nofollow を付ける。/talk/<id> は公開トークと URL を共有するため
# robots.txt では分けられず、レスポンス単位のヘッダーが唯一の手段になる。
NOINDEX = "noindex, nofollow"
_NOINDEX_PREFIXES = ("/api/my/",)


def _mark_noindex():
    g.pox_noindex = True


@app.after_request
def _apply_noindex(resp):
    if getattr(g, "pox_noindex", False) or request.path.startswith(_NOINDEX_PREFIXES):
        resp.headers["X-Robots-Tag"] = NOINDEX
    return resp


ROBOTS_TXT = """User-agent: *
# 本人・運用向けの面（公開の対象ではない）
Disallow: /api/my/
Disallow: /mypage
Disallow: /edit
Disallow: /inbox
Disallow: /conversation
Disallow: /v4/drafts/
Disallow: /dev
Disallow: /ledger
# 加入トーク・チャットは公開トークと同じ /talk/<id> を使うため、ここでは分けられない。
# それらはレスポンスの X-Robots-Tag: noindex と、第三者への 404 で扱う（指示書45 A-3）。
"""


@app.get("/robots.txt")
def robots_txt():
    return Response(ROBOTS_TXT, mimetype="text/plain")

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
# POX_LEGACY_BOUNDARY_SEQ は削除の検証（redaction）の legacy 境界。未設定のまま動かすと
# 改ざん検出が誤検出（legacy なし）か空振り（全件 legacy）になるため、本番では起動を止める
# （指示書45C §1-1。フォールバックしない）。値は docs/ledger_limits.md の監査で確定する。
if _PROD:
    try:
        import redaction as _redaction
        _redaction.legacy_boundary_seq()
    except _redaction.BoundaryNotConfigured as _e:
        raise SystemExit(f"[FATAL] {_e} Render の環境変数に設定してください。")
# 埋め込みのバックエンド（指示書55-2 B-1）。stub は意味を持たない擬似ベクトルなので、本番では
# 起動しない（POX_DEBUG=1 のときだけ許す）。解決結果は起動ログに出す（どのモデルで動いているかを
# ログで確認できるように。値そのものは設定名とタグだけで、URL や鍵は出さない）。
import embedding_config as _ec
# POX_TEST_ALLOW_STUB はテスト（conftest）専用。Render（環境変数 RENDER が自動で立つ）で立っていたら、
# 設定ミスで本番が stub になる経路なので起動しない（指示書55-3 §3-1。LEGACY_BOUNDARY_SEQ と同じ扱い）。
if os.environ.get("RENDER") and os.environ.get("POX_TEST_ALLOW_STUB"):
    raise SystemExit(
        "[FATAL] POX_TEST_ALLOW_STUB はテスト専用です。本番（Render）で設定されているため起動しません。"
        "Render の環境変数から削除してください。"
    )
if _PROD and _ec.BACKEND == "stub" and os.environ.get("POX_TEST_ALLOW_STUB") != "1":
    raise SystemExit(
        "[FATAL] POX_EMBED_BACKEND が stub（未設定時の既定）です。本番では意味の無い擬似ベクトルで"
        "照合することになるため起動しません。Render の環境変数に実バックエンド（nomic 等）を設定してください"
        "（開発時のみ POX_DEBUG=1 で stub を使えます）。"
    )
print(f"[embedding] backend={_ec.BACKEND} backend_env_set={_ec.BACKEND_ENV_SET} "
      f"model_tag={_ec.MODEL_TAG} model_tag_env_set={_ec.MODEL_TAG_ENV_SET} dim={_ec.FULL_DIM}")
if _ec.BACKEND == "stub":
    print("[embedding] 警告: stub で動いています。照合の結果は出しません（他の画面は動きます）。")
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
    nxt = _safe_next(request.args.get("next"))
    # 既にログイン済みなら、ログインフォームを見せず next かマイページへ（同一性はセッション）。
    sid = current_subject_id()
    if sid:
        return redirect(nxt or f"/mypage?id={sid}")
    # 401 で送られてきた元画面へ、ログイン後に戻れるように next を控える（指示書25 §4-2）。
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


@app.post("/ledger/admin/purge-accounts")
def ledger_admin_purge_accounts():
    """アカウント整理（指示書55-3 §2。scripts/purge_accounts.py と同じ）。**対象は固定の 4 id のみ**。

    認証は X-Anchor-Token（一致しなければ 404）。**既定は dry-run**（消える行数と、消せない理由を返す）。
    body {"apply": true} のときだけ削除する（1 トランザクション）。実行時に台帳参照を再確認し、0 でない id は
    消さない。**実行前に pg_dump を取る**（docs/account_purge.md）。**使用後はルートを閉じる**。
    """
    if not (_debug_enabled() or _anchor_token_ok()):
        abort(404)
    _root = os.path.dirname(os.path.abspath(__file__))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from scripts.purge_accounts import run_purge
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(run_purge(apply=body.get("apply") is True, db_path=DB)), 200


@app.get("/ledger/audit/legacy-boundary")
def ledger_audit_legacy_boundary():
    """削除の検証の legacy 境界を決める監査（scripts/audit_legacy_boundary.py と同じ処理）。

    Render の Shell が使えないため HTTP から実行できるようにしたもの。読み取りのみ（DB に書かない）。
    認証は /ledger/anchor と同じ: X-Anchor-Token ヘッダが POX_ANCHOR_TOKEN と一致しなければ 404。
    ?boundary_at=<UTC ISO8601>（省略時は #101 の main 反映時刻）。
    返すのは件数・推奨 seq・境界時刻と、不一致の合意の id（event_hash）と seq まで。本文は返さない。
    """
    if not (_debug_enabled() or _anchor_token_ok()):
        abort(404)
    _root = os.path.dirname(os.path.abspath(__file__))
    if _root not in sys.path:
        sys.path.insert(0, _root)          # scripts/ をリポジトリ直下から読む（起動ディレクトリに依らない）
    from scripts.audit_legacy_boundary import run_audit
    r = run_audit(request.args.get("boundary_at") or None, db_path=DB)

    def ids(rows):
        return [{"event_hash": m.get("event_hash") or _agreement_hash_at(m["seq"]), "seq": m["seq"]}
                for m in rows]
    return jsonify({
        "boundary_at": r["boundary_at"],
        "boundary_seq": r["boundary_seq"],
        "checked": r["checked"],
        "mismatch_count": len(r["mismatches"]),
        "recommended_seq": r["boundary_seq"] if not r["mismatches"] else None,
        "mismatches": ids(r["mismatches"]),
        "unresolved_count": len(r["unresolved"]),
        "unresolved": ids(r["unresolved"]),
        "verdict": r["verdict"],
    }), 200


@app.get("/ledger/audit/inventory")
def ledger_audit_inventory():
    """埋め込みとアカウントの棚卸し（指示書55 段階0-2 E-1〜E-3・B-2・B-5。scripts/audit_inventory.py と同じ）。

    読み取りのみ。認証は /ledger/audit/legacy-boundary と同じ（X-Anchor-Token が一致しなければ 404）。
    返すのは設定値・件数・id ごとの所在と表示名まで。本文・スコア・照合の結果は返さない。
    """
    if not (_debug_enabled() or _anchor_token_ok()):
        abort(404)
    _root = os.path.dirname(os.path.abspath(__file__))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from scripts.audit_inventory import run_inventory
    return app.response_class(json.dumps(run_inventory(db_path=DB), ensure_ascii=False, default=str),
                              mimetype="application/json"), 200


@app.get("/ledger/audit/match")
def ledger_audit_match():
    """指定ペアの照合の内部値（指示書55-4 §2。閾値の校正の証拠として取る）。

    認証は inventory と同じ（X-Anchor-Token が一致しなければ 404）。**指定した 2 人だけ**を返し、
    一覧・横断はしない。**本文は返さない**（類似度・ゲート・寄与・総合と入口の判定だけ）。画面と
    /v4/match には引き続き数値を出さない。inventory と同時に閉じる。記録は校正の証拠で、指標にしない。
    ?pair=<id_a>,<id_b> → a_to_b（a が照合したときの b）と b_to_a（その逆）。
    """
    if not (_debug_enabled() or _anchor_token_ok()):
        abort(404)
    ids = [x.strip() for x in (request.args.get("pair") or "").split(",") if x.strip()]
    if len(ids) != 2 or ids[0] == ids[1]:
        return jsonify({"error": "pair=<id_a>,<id_b>（異なる 2 人）が必要です"}), 400
    if not is_postgres():
        return jsonify({"error": "v4 は Postgres（DATABASE_URL）が必要です"}), 503
    from embedding_config import MODEL_TAG
    from match_config import MATCH_ENTRY_THRESHOLD
    store = _v4_store()
    a, b = ids
    return jsonify({
        "pair": ids, "model_tag": MODEL_TAG,
        "entry_threshold": MATCH_ENTRY_THRESHOLD,
        "threshold_scale": "g(cos) = (1 + cos) / 2 の加重べき乗平均（総合）に対する閾値。cos そのものではない",
        "a_to_b": _pair_detail(store, a, b, MODEL_TAG),
        "b_to_a": _pair_detail(store, b, a, MODEL_TAG),
        "v5": _pair_detail_v5(store, a, b, MODEL_TAG),
    }), 200


def _pair_detail_v5(store, a, b, model_tag):
    """段3（目的ごと・文単位）と共鳴の門の内部値（指示書61 §2-5）。本文は返さない。
    a_to_b: a の各目的が b を求める方向（補完A）／b_to_a: b の各目的が a を求める方向。
    各方向: purpose_id・resonance（向かう先どうしの g(cos) の最大）・gate_strength（gate_s×(1−gate_u)）・
    gate（門を立てたか）・gate_passed（門を通ったか）・complement（必須の文の充足。門で落ちたら null）。"""
    from matcher_v5 import audit_pair
    from match_config import RES_GATE_MIN, RES_THRESHOLD
    from matcher_v5 import SENTENCE_JUDGE_THRESHOLD
    sa, sb = _side_of(store, a, model_tag), _side_of(store, b, model_tag)
    return {"res_gate_min": RES_GATE_MIN, "res_threshold": RES_THRESHOLD,
            "sentence_threshold": SENTENCE_JUDGE_THRESHOLD,
            "has_offer": {"a": sa["has_offer"], "b": sb["has_offer"]},
            **audit_pair(sa, sb)}


def _pair_detail(store, seeker, other, model_tag):
    """seeker が照合したときの other の内部値（/v4/match と同じ経路・同じ式。台帳・ledger_v4 に書かない）。"""
    from db_v4 import match_v4
    from matcher_v4 import effective_axis, passes_entry, public_axis
    from ledger import engaged_counterparts
    nec_id = _seeker_live_necessity_id(seeker, db_path=DB)
    out, numbers = None, {}
    if nec_id:
        try:
            out = _match_by_necessity(store, nec_id, model_tag=model_tag, write_ledger=False)
            from necessities import get_necessity
            n = get_necessity(nec_id, db_path=DB) or {}
            numbers = {k: n.get(k) for k in ("gate_s", "gate_u", "p_sharpness", "alpha", "beta")}
        except LookupError:
            out = None
    if out is None:
        try:
            out = match_v4(store, seeker, model_tag=model_tag, write_ledger=False)
            out["query_unit"] = "person"
            n = (store.get_bundle(seeker, model_tag) or {}).get("necessity") or {}
            numbers = {k: n.get(k) for k in ("gate_s", "gate_u", "gamma", "p_sharpness", "alpha", "beta")}
        except ValueError:
            return {"error": "照合する側の v4 ベクトルがありません"}
    r = next((x for x in out.get("results", []) if x["candidate_id"] == other), None)
    if r is None:
        return {"query_unit": out.get("query_unit"), "numbers": numbers,
                "error": "相手が照合の母集団にいません（同じ model_tag の有効なベクトルが無い）"}
    at = r["attribution"]
    keys = ("a_sim", "b_sim", "d_sim", "ga", "gb", "gd",
            "a_log_contrib", "b_log_contrib", "limiting_axis")   # c・γ は廃止（指示書56）
    # 「照合の結果」から外れる理由（入口以外）。値は bool だけ。
    bundle = (store.get_bundles([other], model_tag) or {}).get(other) or {}
    return {
        "query_unit": out.get("query_unit"),
        "numbers": numbers,
        "channels": {k: at.get(k) for k in keys},
        "score_A": r["score"],
        "score_B": r.get("score_b"),
        "passes_entry": passes_entry(r),
        "effective_axis": effective_axis(at),
        "public_axis": public_axis(at),
        "excluded": {
            "engaged": other in engaged_counterparts(seeker, db_path=DB),
            "not_linked": other not in _linked_ids([other]),
            "no_necessity": not ((bundle.get("necessity") or {}).get("necessity_text") or "").strip(),
        },
    }


def _agreement_hash_at(seq):
    """seq の台帳イベントの event_hash（監査の不一致行は talk_id を持つため、id は台帳から引く）。"""
    import ledger_events as le
    for e in le.get_events(db_path=DB):
        if e["seq"] == seq:
            return e["event_hash"]
    return None


@app.post("/seekers")
def post_seeker():
    """【閉鎖】旧 v3 登録の入口（指示書18 作業C）。

    表示の正を profiles_v4 に切り替えたため（作業B）、profiles/seekers にだけ書く
    この経路を新規登録の入口としては閉じた。ここから登録すると v4 と食い違い、
    「記録した内容と表示した内容の不一致」を再発させるため。

    登録は ①→ `/v4/drafts`（下書き）→ `/v4/drafts/<id>/confirm`（確定）を使う。
    dual-write 関数 `_dual_write_v4` は**削除せず保持**する（既存データの整合性と
    将来の移行判断のため）。
    """
    return jsonify({
        "error": "この登録経路は終了しました。登録ページから①→下書き→確定でご登録ください。",
        "moved_to": "/register",
    }), 410


@app.get("/seekers")
def get_seekers():
    """登録者一覧（公開）。**母集団は v4（profiles_v4）**で、本人と紐づいている人だけ（指示書55 §4-4）。

    返すのは id・表示名・一行紹介・意志・公開条件を満たす必要像の本文だけ（seeker 原文・数値は返さない）。
    - **本文をサーバーで切り詰めない**（以前の 40 文字切断をやめた。見た目の丸めは CSS 側）
    - **generation_status を返さない**（非本人に生成状態を出さない。指示書27）
    - ログイン中なら**自分を除く**（照合の結果と同じく、一覧も他の人の面）
    - 並びは登録順（中立）。件数は出さない（画面側）
    """
    me = current_subject_id()
    ids = [i for i in list_directory_ids(db_path=DB) if i != me]
    names = _resolve_names(ids, fallback=UNNAMED_LABEL)
    import handles
    hs = handles.get_many(ids, db_path=DB)        # リンクは /u/<ハンドル>（アドレス欄に生 id を出さない）
    rows = []
    for i in ids:
        pv = get_profile_view(i, db_path=DB) or {}
        pub = get_public_necessity(i)          # 数値なし・日時閾値ゲート済み
        rows.append({
            "id": i,
            "handle": hs.get(i),
            "name": names.get(i) or UNNAMED_LABEL,
            "one_liner": _text_of(pv.get("headline")),
            "will": _text_of(pv.get("pursuing")),
            "necessity": (pub or {}).get("necessity_text") or "",
        })
    return jsonify(rows)


@app.post("/match")
@login_required
def post_match():
    """
    （旧 v3）本人の seeker で照合する。**本人のみ**（指示書55 PR-A: 以前は未認証で body の
    seeker_id を信じ、誰の照合でも実行できた）。**応答に数値・順位・理由文を入れない**
    （候補の id を中立の順で返すだけ。スコアは内部でのみ使う）。
    """
    body = request.get_json(force=True, silent=True)
    if body is None:
        abort(400, "JSON が読めません")

    seeker_id = require_self((body.get("seeker_id") or "").strip() or None)
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

    # 数値・順位・理由文（数値を含む）は返さない。候補の id だけを中立の順（id 順）で返す。
    ids = sorted(r["id"] for r in result.get("ranking", []))
    return jsonify({"match_run_id": f"run_{uuid.uuid4().hex[:8]}", "demo_mode": demo_mode,
                    "ranking": [{"id": i} for i in ids]})


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
        **({"v5": body["v5"]} if body.get("v5") else {}),   # ①v5 の本文（宣言のハッシュ p2 に使う）
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
                  final_status=None, publish_ledger=True):
    """
    非同期ジョブ（daemon スレッド）: 4ベクトル生成（＝照合の準備。外部依存＝埋め込みモデル）。
    生成状態は generation_status（preparing→ready / error）で追跡する。
    - user-supplied 経路: necessity は受付時に検証・保存済み → ベクトル化のみ。
    - final_status を渡すと、ベクトル化成功後に status を ready ではなくその値へ上書きする
      （編集再ベクトル化: 新ベクトル＋旧必要像＝needs_regeneration を維持。指示書08 §3-3）。

    スナップショット（本文の記録）は**このジョブでは作らない**（指示書35）。確定の事実として
    _ingest_v4_from_flat が同期・必須で保存済み。ベクトル化の失敗は本文の記録に影響しない。
    """
    from db_v4 import vectorize_profile_v4, GEN_ERROR
    store = _v4_store()
    try:
        if is_fallback or necessity is None:
            # fallback B は廃止（指示書28 §6-1）。サーバー側の必要像生成はしない。
            # 必要像は①が必ず出す前提。無い入力は受理側で弾くので、ここに来たら異常。
            store.set_generation_status(
                profile_id, GEN_ERROR,
                error="必要像がありません。①をやり直して必要像を含めて再送してください。")
            return
        _run_with_retry(
            lambda: vectorize_profile_v4(
                store, profile_id, profile_input, necessity["necessity_text"]))
        _state_slot_job(profile_id)      # 現状の欄ごと（表示で該当する欄を選ぶ。指示書61 §3-1）
        if final_status is not None:  # 編集経路: ready を上書きして needs_regeneration を維持
            store.set_generation_status(profile_id, final_status, error=None)
        # 下書き→確定の経路では台帳は confirm が同期で書き済み（生成元ピン留め等の付帯情報つき）。
        # その場合 publish_ledger=False で二重書きを避ける。旧 /v4/seekers 経路は従来どおり best-effort。
        if publish_ledger:
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


def _save_snapshot(profile_id, profile_input, necessity):
    """再構造化の時点スナップショット（本文の記録）を**同期・必須**で保存する（指示書35 §2-2）。

    これは「照合の準備」ではなく「本文の記録」であり、確定の事実の側に属する。台帳の書き込みと
    同じ扱いにする＝**best-effort にしない**。DB 障害等で失敗したら例外を送出し、確定自体を
    失敗させる（本文なきハッシュを作らないため）。churn（前回と content_hash 同一）は
    save_snapshot が None を返すだけで例外ではない（呼び出し側は成功として扱う）。

    保存範囲（指示書35 §4）: 意志・現状4スロット・supporting_raw・必要像・各ハッシュに加えて
    view_overrides（「本人より」）も含める。ただし churn 判定（content_hash）の範囲は変えない
    ＝「本人より」だけの変更は新スナップショットを作らない（非対称・§4 で報告）。
    戻り値: 保存した snapshot_id（churn スキップなら None）。
    """
    from snapshots import save_snapshot
    from subject_ledger import profile_content_hash
    nec = necessity or {}
    doc5 = profile_input.get("v5")
    if doc5:
        # ①v5（指示書57）: 版のノードに目的ごとの必要像と与え像が並ぶように、本文の記録に載せる。
        nec = {**nec,
               "purposes": [{"purpose_id": p.get("purpose_id"), "向かう先": p.get("向かう先"),
                             "手段": p.get("手段"), "必要像": p.get("必要像") or []}
                            for p in (doc5.get("purposes") or [])],
               "与え像": doc5.get("与え像") or []}
    # churn 判定は content_hash に統一（指示書18 §2）。台帳 publish_profile_structured と
    # 同一の profile_content_hash を同じ profile_input から算出 → 計算範囲が完全に一致する。
    content_hash = profile_content_hash(profile_input)
    view_overrides = get_view_overrides(profile_id, db_path=DB)
    return save_snapshot(
        profile_id,
        will_text=profile_input.get("will_text", ""),
        state={k: profile_input.get(k, "") for k in
               ("state_have", "state_can_type", "state_bound", "state_unsorted")},
        supporting=profile_input.get("supporting_raw") or {},
        necessity=nec,
        content_hash=content_hash,
        src_input_hash=nec.get("src_input_hash"),   # 保持のみ（判定には使わない）
        view_overrides=view_overrides,
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        db_path=DB,
    )


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
                  final_status=None, publish_ledger=True):
    t = threading.Thread(
        target=_v4_async_job, args=(profile_id, profile_input, necessity),
        kwargs={"is_fallback": is_fallback, "final_status": final_status,
                "publish_ledger": publish_ledger}, daemon=True)
    t.start()


def _ingest_v4_from_flat(body, *, profile_id=None, publish_ledger=True):
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

    # 1) profiles_v4 に保存（同期）。
    receive_profile_v4(store, pid, profile_input, necessity,
                       generation_status=GEN_PREPARING)
    # 2) 時点スナップショット（本文の記録）を**同期・必須**で保存する（指示書35）。
    #    確定の事実であり、埋め込みモデル（外部依存）の稼働とは無関係。ベクトル化の前・かつ
    #    追記専用の台帳に content_hash を刻む前に本文を確定させる（本文なきハッシュを作らない）。
    #    失敗したら例外を送出＝確定自体を失敗させる（best-effort にしない）。churn（同一内容）は
    #    None を返すだけで例外ではない（下書き→確定・登録経路のみ。編集/retry は snapshot=False）。
    _save_snapshot(pid, profile_input, necessity)
    # 3) ベクトル化は非同期（失敗してよい。1〜2 は既に残っている）。snapshot はもう作らない。
    _spawn_v4_job(pid, profile_input, necessity, is_fallback=is_fallback,
                  publish_ledger=publish_ledger)
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
    # fallback B 廃止（§6-1）: 必要像が無ければ v4 化しない（サーバー生成しない）。
    if not (flat.get("necessity_text") or "").strip():
        return False
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
    # fallback B 廃止（指示書28 §6-1）: 必要像フィールドが無い JSON は受理しない。
    if not (body.get("necessity_text") or "").strip():
        return jsonify({"error": "必要像がありません。①をやり直して必要像を含めて再送してください。"}), 400

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
@login_required
def get_v4_seeker_status(profile_id):
    """非同期生成の進捗（preparing / ready / error / needs_regeneration）を返す。

    本人限定（指示書22 / 27 §2-4）: セッション本人のみ自分の状態を見られる。
    generation_error は技術文字列なので利用者には返さない（§2-3。POX_DEBUG=1 のみ）。
    """
    require_self(profile_id)   # セッション本人と不一致は 403（401 は login_required が担保）
    if not is_postgres():
        return jsonify({"error": "v4 は Postgres（DATABASE_URL）が必要です"}), 503
    st = _v4_store().get_profile_status(profile_id)
    if st is None:
        return jsonify({"error": "プロフィールが見つかりません"}), 404
    out = {"id": profile_id, "generation_status": st.get("generation_status")}
    if _debug_enabled() and st.get("generation_error"):
        out["generation_error"] = st["generation_error"]
    return jsonify(out)


@app.post("/v4/seekers/<profile_id>/retry")
@login_required
def retry_v4_seeker(profile_id):
    """
    失敗した非同期生成を再試行する。保存済み necessity があれば再ベクトル化のみ、
    無ければフォールバック生成からやり直す。status=preparing に戻して再ジョブ。

    本人限定（指示書22 / 27 §2-4）: 他人の id で再試行を起動できないようゲートする。
    """
    require_self(profile_id)   # セッション本人と不一致は 403（401 は login_required が担保）
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


# ── 下書きと承認の動線（指示書28 段階1）─────────────────────────────────────────
# ①の出力JSONは貼った瞬間には台帳に載せず、まず下書き（通常DB・第三者非公開・削除自由）へ。
# 「確定」で初めて台帳へ profile.structured + necessity.published を書き、その後にベクトル化。
# 台帳が先・外部依存（埋め込み）が後（§1-2）。

def _draft_preview(draft):
    """下書きの表示用サマリ（本人が確認する材料）。生の payload も返す（本人限定）。"""
    return {
        "draft_id": draft["draft_id"],
        "subject_id": draft["subject_id"],
        "owner_kind": draft["owner_kind"],
        "target_intent_id": draft.get("target_intent_id"),
        "attempt_n": draft["attempt_n"],
        "status": draft["status"],
        "payload": draft["payload"],
        "rejections": draft.get("rejections", []),
        "updated_at": draft["updated_at"],
        **_handle_hint(draft),
        **_purpose_hint(draft),
    }


def _clamp_numbers(nums):
    """①v5 の目的ごとの数値を検証・クランプ（無検証で信頼しない）。gate は [0,1]。
    alpha・beta は使わない（指示書61: 共鳴を門にした。c4 の数値は {gate_s, gate_u}）。"""
    def f(x, lo, hi, default):
        # 常に float で返す（max(0, 0.0) は int の 0 を返し、内容ハッシュの正準形が 0 と 0.0 で変わるため）
        try:
            return float(max(lo, min(hi, float(x))))
        except (TypeError, ValueError):
            return float(default)
    nums = nums or {}
    return {"gate_s": f(nums.get("gate_s"), 0, 1, 0.0), "gate_u": f(nums.get("gate_u"), 0, 1, 0.5)}


def _confirm_v5_ledger(pid, doc, assigned, prof, draft):
    """①v5 の確定（指示書57 段2）: 与え像の版 → 目的ごとの necessity.published（c3・purpose_id・offer_hash）
    → 消えた目的の必要像を necessity.retired → 本文の保存 → 文単位ベクトル（非同期）。"""
    import v5
    import necessities as _nec
    offer = v5.save_offer(pid, doc.get("与え像") or [], db_path=DB)
    # 生成元の記録（穴A・指示書61 §4-3）: generator はモデルの系統に丸めた値、generator_tag は <source>/<系統>。
    # どちらも payload の値で、内容ハッシュの対象ではない。
    family = _nec.normalize_generator(str(doc.get("generator") or ""))
    generator_tag = f"{v5.source_of(doc)}/{family}" if family else v5.source_of(doc)
    published = []
    for purpose_id, p in assigned:
        nec_in = {**_clamp_numbers(p.get("数値")), "will_text": str(p.get("向かう先") or ""),
                  "evidence_span": str(p.get("根拠") or "")}
        r = _nec.publish_necessity(
            pid, "subject", nec_in, origin="generated", generator=family,
            source_snapshot_hash=prof.get("content_hash"), generator_tag=generator_tag,
            attempt_n=draft["attempt_n"], actor=pid, db_path=DB,
            purpose_id=purpose_id, sentences=p.get("必要像") or [], offer_hash=offer["offer_hash"])
        published.append(r)
    keep = {purpose_id for purpose_id, _ in assigned}
    for n in v5.live_necessities_v5(pid, db_path=DB):
        if n["purpose_id"] not in keep:                     # 目的が消えた（対応が取れなかった）
            _nec.retire_necessity(n["necessity_id"], actor=pid, db_path=DB)
    v5.save_doc(pid, doc, db_path=DB)
    threading.Thread(target=_v5_sentence_job, args=(pid, offer), daemon=True).start()
    return published


def _v5_sentence_job(pid, offer):
    """文単位ベクトル（必要像の文＝query／与え像の文＝passage）を作る。失敗しても確定は成立済み。"""
    try:
        import v5
        import necessities as _nec
        from embedding_config import MODEL_TAG
        for n in v5.live_necessities_v5(pid, db_path=DB):
            if not v5.get_sentence_vectors("necessity", n["necessity_id"], MODEL_TAG, db_path=DB):
                v5.save_sentence_vectors("necessity", n["necessity_id"], [x["文"] for x in n["sentences"]],
                                         MODEL_TAG, _nec._default_embed, db_path=DB)
        if not v5.get_sentence_vectors("offer", offer["offer_id"], MODEL_TAG, db_path=DB):
            o = v5.get_offer(offer["offer_id"], db_path=DB) or {"sentences": []}
            v5.save_sentence_vectors("offer", offer["offer_id"], [x["文"] for x in o["sentences"]],
                                     MODEL_TAG, _nec._default_embed, db_path=DB)
        # 共鳴の門（指示書61）: 目的ごとの向かう先。与え像が 0 文なら現状の欄も（表示で該当する欄を選ぶ）。
        v5.save_dest_vectors(pid, MODEL_TAG, _nec._default_embed, db_path=DB)
        _state_slot_job(pid)
    except Exception as e:  # noqa: BLE001
        app.logger.warning(f"[v5-sentences] 文単位ベクトルの作成に失敗（確定は成立済み）: {e}")


def _state_slot_job(sid):
    """現状の欄ごとのベクトル（表示用。判定には使わない）。公開プロフィールの欄の文で作る。失敗しても何も壊さない。"""
    try:
        import v5
        import necessities as _nec
        from embedding_config import MODEL_TAG
        pv = get_profile_view(sid, db_path=DB) or {}
        v5.save_state_slot_vectors(sid, [_text_of(pv.get(k)) for k, _ in STATE_SLOTS], MODEL_TAG,
                                   _nec._default_embed, db_path=DB)
    except Exception as e:  # noqa: BLE001
        app.logger.warning(f"[state-slots] 現状の欄のベクトルの作成に失敗（照合には影響しない）: {e}")


_STATE_SLOT_PENDING = set()


def _backfill_state_slots(sid):
    """欄ごとのベクトルがまだ無い v4 の人の分を、裏で一度だけ作る（照合の応答は待たせない）。
    テスト（TESTING）では動かさない（合成ベクトルを上書きしないため）。"""
    if app.config.get("TESTING") or sid in _STATE_SLOT_PENDING:
        return
    _STATE_SLOT_PENDING.add(sid)

    def run():
        try:
            _state_slot_job(sid)
        finally:
            _STATE_SLOT_PENDING.discard(sid)
    threading.Thread(target=run, daemon=True).start()


def _purpose_hint(draft):
    """①v5 の再構造化: 前の目的との対応の提案（本人が確認する。指示書57 §1-2）。"""
    import v5
    if draft.get("owner_kind") != "subject" or not v5.is_v5(draft.get("payload")):
        return {}
    sid = draft["subject_id"]
    prev = v5.get_doc(sid, db_path=DB) or {}
    labels = {p.get("purpose_id"): p.get("向かう先") for p in (prev.get("purposes") or [])}
    existing = [{"purpose_id": p, "label": labels.get(p) or ""}
                for p in v5.list_purposes(sid, db_path=DB) if v5.is_live_purpose(sid, p, db_path=DB)]
    return {"v5": True, "existing_purposes": existing,
            "purpose_mapping": v5.suggest_mapping(sid, draft["payload"], db_path=DB)}


def _handle_hint(draft):
    """確定の前に見せるハンドルの材料（指示書55-2 PR-D）。個人の下書きで、まだハンドルが無い人だけ。
    初期値は①の出力の "id"（英数字のハンドルネーム。指示書30 で宙に浮いていた値）を使う。"""
    if draft.get("owner_kind") != "subject":
        return {}
    import handles
    if handles.get_handle(draft["subject_id"], db_path=DB):
        return {"needs_handle": False}
    raw = str((draft.get("payload") or {}).get("id") or "")
    try:
        suggestion = handles.normalize(raw)
    except handles.HandleError:
        suggestion = ""
    return {"needs_handle": True, "handle_suggestion": suggestion}


@app.post("/v4/drafts")
@login_required
def post_draft():
    """①の出力JSON（または raw_text）を下書きに保存する（貼るたびに attempt_n +1・§3-2）。
    台帳には書かない。raw_text は ``` 除去・スマートクォート正規化・JSON パースをサーバ側で行う。"""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON が読めません"}), 400
    raw_text = body.get("raw_text")
    if raw_text is not None:
        try:
            parsed = parse_registration_text(raw_text)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    else:
        parsed = {k: v for k, v in body.items() if k != "user_id"}
    import v5
    if v5.is_v5(parsed):
        # ①v5: 受信の検証規則（改訂2 §3・指示書60）。通らなければ保存せず、理由を 1 行で返す。
        # 根拠の検査は JSON の supporting_material.生テキストで行う（「本人の語り」の別欄は廃止）。
        ok, why = v5.validate(parsed)
        if not ok:
            return jsonify({"error": why}), 400
    else:
        flat = _normalize_v4_body(parsed)
        if not (flat.get("will_text") or "").strip():
            return jsonify({"error": "will_text が必要です（意志が空です）"}), 400
    subject_id = require_self(body.get("user_id"))
    if not subject_id:
        return jsonify({"error": "ログインが必要です"}), 401
    draft = drafts.save_draft(subject_id, parsed, owner_kind="subject", db_path=DB)
    return jsonify(_draft_preview(draft)), 201


@app.get("/v4/drafts/mine")
@login_required
def get_my_drafts():
    """本人の下書き一覧（確定前も確定済みも含む・本人限定）。"""
    subject_id = current_subject_id() or require_self(request.args.get("id"))
    return jsonify({"drafts": [_draft_preview(d) for d in drafts.list_drafts(subject_id, db_path=DB)]})


@app.get("/v4/drafts/<draft_id>")
@login_required
def get_draft_by_id(draft_id):
    d = drafts.get_draft(draft_id, db_path=DB)
    if d is None:
        return jsonify({"error": "下書きが見つかりません"}), 404
    require_self(d["subject_id"])   # 他人の下書きは 403
    return jsonify(_draft_preview(d))


@app.delete("/v4/drafts/<draft_id>")
@login_required
def delete_draft_by_id(draft_id):
    d = drafts.get_draft(draft_id, db_path=DB)
    if d is None:
        return jsonify({"deleted": True}), 200   # 冪等
    require_self(d["subject_id"])
    drafts.delete_draft(draft_id, db_path=DB)
    return jsonify({"deleted": True}), 200


def _can_touch_draft(d):
    """下書きを操作できるか（本人 or コミュニティメンバー）。403 のとき False。"""
    if d["owner_kind"] == "intent":
        from member_ledger import active_members_from_events
        me = current_subject_id()
        if _debug_enabled():
            return True
        return me is not None and me in active_members_from_events(d["subject_id"], db_path=DB)
    # 個人の下書きは本人のみ（require_self は不一致で 403 abort する）
    require_self(d["subject_id"])
    return True


@app.post("/v4/drafts/<draft_id>/reject")
@login_required
def reject_draft(draft_id):
    """拒否理由を下書きに記録する（台帳には載せない・§5-2）。
    種別: fact_error（事実誤認→再生成の入力）/ discomfort（違和感→gate_u 引き上げ）。
    個人の下書きは本人、コミュニティの下書きはメンバーが記録できる。"""
    d = drafts.get_draft(draft_id, db_path=DB)
    if d is None:
        return jsonify({"error": "下書きが見つかりません"}), 404
    if not _can_touch_draft(d):
        return jsonify({"error": "この下書きを操作する権限がありません"}), 403
    body = request.get_json(force=True, silent=True) or {}
    kind = (body.get("kind") or "").strip()
    if kind not in ("fact_error", "discomfort"):
        return jsonify({"error": "kind は fact_error または discomfort"}), 400
    try:
        drafts.add_rejection(draft_id, kind, body.get("note") or "",
                             by=current_subject_id() or "", db_path=DB)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    d2 = drafts.get_draft(draft_id, db_path=DB)
    return jsonify(_draft_preview(d2)), 200


@app.post("/v4/drafts/<draft_id>/confirm")
@login_required
def confirm_draft(draft_id):
    """個人の「確定」（§2-2）。台帳に profile.structured + necessity.published を **同期で**
    書き（台帳が先）、その後にベクトル化を非同期で回す（外部依存が後・§1-2）。
    ベクトル化の成否は台帳に影響しない。"""
    d = drafts.get_draft(draft_id, db_path=DB)
    if d is None:
        return jsonify({"error": "下書きが見つかりません"}), 404
    require_self(d["subject_id"])
    if d["owner_kind"] != "subject":
        return jsonify({"error": "この下書きは個人の確定対象ではありません"}), 400
    if not is_postgres():
        return jsonify({"error": "v4 は Postgres（DATABASE_URL）が必要です"}), 503

    import v5
    cbody = request.get_json(force=True, silent=True) or {}
    v5_doc = None
    if v5.is_v5(d["payload"]):
        v5_doc = {k: v for k, v in d["payload"].items() if k != "_narrative"}
        # 与え像の確認（指示書60 §3）: 本人が外した文を除く（外すだけ。足す・書き換えは受け付けない）。
        # 外すのは再生成ではないので attempt_n は変えない（下書きはそのまま）。0 文でも確定できる。
        keep = cbody.get("offer_keep")
        v5_doc = v5.keep_offers(v5_doc, keep if isinstance(keep, list) else None)
        ok, why = v5.validate(v5_doc)
        if not ok:
            return jsonify({"error": why}), 400
        flat = v5.to_flat(v5_doc)
    else:
        flat = _normalize_v4_body(d["payload"])
    if not (flat.get("will_text") or "").strip():
        return jsonify({"error": "will_text が空です。①をやり直してください"}), 400
    # fallback B 廃止（§6-1）: 必要像が無ければ確定させない（①をやり直す）。
    if not (flat.get("necessity_text") or "").strip():
        return jsonify({"error": "必要像がありません。①をやり直して必要像を含めて再送してください。"}), 400

    # ハンドル（指示書55-2 PR-D）: 登録時に確定（必須）。未設定の人は確定の本文で受け取り、書式と
    # 重複をここで確かめる（書き込みは確定の最後）。設定済みの人には求めない（不変）。
    import handles
    new_handle = None
    if handles.get_handle(d["subject_id"], db_path=DB) is None:
        try:
            new_handle = handles.check(cbody.get("handle") or "", d["subject_id"], db_path=DB)
        except handles.HandleError as e:
            return jsonify({"error": f"ハンドル: {e}", "field": "handle"}), 400
        except handles.HandleTaken as e:
            return jsonify({"error": f"ハンドル: {e}", "field": "handle"}), 409

    # 違和感(discomfort)の拒否があれば gate_u を引き上げる（§5-1）。ingest より前に反映し、
    # 照合に使う v4 ストア側の必要像にも同じ値が入るようにする（fact_error はここでは動かさない）。
    n_discomfort = drafts.count_rejections(d, "discomfort")
    if n_discomfort and flat.get("gate_u") is not None:
        flat["gate_u"] = drafts.gate_u_after_discomfort(flat["gate_u"], n_discomfort)

    # ①v5: 目的にサーバーの不変 id を振る（本人が確かめた対応 purpose_map。無ければ提案どおり）。
    assigned = []
    if v5_doc is not None:
        mapping = cbody.get("purpose_map")
        if not isinstance(mapping, dict):
            mapping = v5.suggest_mapping(d["subject_id"], v5_doc, db_path=DB)
        assigned = v5.assign_purposes(d["subject_id"], v5_doc, mapping, db_path=DB)
        flat["v5"] = {**v5_doc, "purposes": [{**p, "purpose_id": pid} for pid, p in assigned]}

    # 受付（profiles_v4 / derived_necessity へ）＋ベクトル化ジョブ起動（非同期）。
    # 台帳は confirm が同期で書くので、ジョブ側の台帳書き込みは抑止（publish_ledger=False）。
    try:
        pid, necessity, is_fallback = _ingest_v4_from_flat(
            flat, profile_id=d["subject_id"], publish_ledger=False)
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"必要像フィールド不正: {e}"}), 400

    # ── 台帳が先（同期・確定イベント）──────────────────────────────
    from subject_ledger import publish_profile_structured
    import necessities as _nec
    profile_input = _v4_profile_input(flat)
    # profile.structured（生成元）を書き、その content_hash を必要像のピン留めに使う（§3-1）。
    prof = publish_profile_structured(pid, profile_input, actor=pid, db_path=DB)
    if v5_doc is not None:
        _confirm_v5_ledger(pid, flat["v5"], assigned, prof, d)
    elif necessity is not None:
        sm = profile_input.get("supporting_raw") or {}
        seeking = sm.get("求めている") or ""                       # §3-3: content_hash に含める
        prompt_ver = ((d["payload"].get("_meta") or {}).get("source")) or ""
        model_fam = _nec.normalize_generator(necessity.get("generator_name") or "")
        generator_tag = f"{prompt_ver}/{model_fam}" if prompt_ver else model_fam
        nec_in = {**necessity, "will_text": profile_input.get("will_text", ""), "seeking": seeking}
        _nec.publish_necessity(
            pid, "subject", nec_in, origin="generated",
            generator=necessity.get("generator_name") or "",
            seeking=seeking, source_snapshot_hash=prof.get("content_hash"),
            generator_tag=generator_tag, attempt_n=d["attempt_n"],
            actor=pid, db_path=DB)

    # プライバシーポリシー同意の証跡（確定時に記録・best-effort。従来 /seekers で記録していた分）。
    if cbody.get("privacy_policy_agreed"):
        try:
            record_policy_consent(pid, str(cbody.get("privacy_policy_version") or ""), db_path=DB)
        except Exception as e:  # noqa: BLE001
            app.logger.warning(f"[policy-consent] 記録skip（確定は成功）: {e}")

    if new_handle:
        try:
            handles.set_handle(d["subject_id"], new_handle, db_path=DB)
        except (handles.HandleTaken, handles.HandleImmutable) as e:   # 確認と書き込みの間に取られた
            app.logger.warning(f"[handle] 設定できませんでした（確定は成功・後から設定可）: {e}")
    drafts.set_status(draft_id, "confirmed", db_path=DB)
    # churn（指示書35 §3）: 内容が前回と同一なら台帳もスナップショットも記録されない（仕様どおり）。
    # 「何も起きなかった」ように見えないよう、確定時に利用者へ伝える材料を返す。
    unchanged = bool(prof.get("skipped"))
    return jsonify({
        "id": pid, "draft_id": draft_id, "confirmed": True,
        "handle": handles.get_handle(pid, db_path=DB),
        "attempt_n": d["attempt_n"],
        "route": "fallback" if is_fallback else "user-supplied",
        "unchanged": unchanged,
        "history_added": not unchanged,
        "status_url": f"/v4/seekers/{pid}/status",
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
    owner_sp = owner_bundle["vectors"].get("state_passage")      # 方向 B（指示書55 §4-3）

    cand_ids = store.candidate_ids(owner, model_tag)
    cand_bundles = store.get_bundles(cand_ids, model_tag)
    cand_list = [(cid, b["vectors"]) for cid, b in cand_bundles.items()]

    numbers = {k: nec.get(k) for k in ("gate_s", "gate_u", "p_sharpness", "alpha", "beta")}
    results = rank_for_necessity(numbers, qv, owner_wp, cand_list, top_k=top_k,
                                 owner_state_passage=owner_sp)

    if write_ledger:
        for r in results:
            attr = r["attribution"]
            try:
                store.write_ledger(owner, r["candidate_id"], "match_ranked", {
                    "score": r["score"], "limiting_axis": attr["limiting_axis"],
                    "a_sim": attr["a_sim"], "b_sim": attr["b_sim"], "d_sim": attr.get("d_sim"),
                    "model_tag": model_tag, "necessity_id": necessity_id,
                    "query_unit": "necessity",
                })
            except Exception:  # noqa: BLE001
                pass

    return {"seeker_id": owner, "necessity_id": necessity_id, "query_unit": "necessity",
            "model_tag": model_tag, "results": results, "pool_size": len(cand_list),
            "numbers": numbers}


@app.post("/v4/match")
@login_required
def post_v4_match():
    """
    本人を起点に v4 エンジンで照合する（E章）。ledger_v4 に監査記録。
    **本人のみ**（指示書55 PR-A: 以前は未認証で body の seeker_id を信じていた）。
    **応答に `score`・`attribution`（チャネル別の値）を入れない**。返すのは候補の id と
    「最も効いた軸」の名前だけで、**並びは中立（id 順）**。数値は内部でのみ使う。
    """
    if not is_postgres():
        return jsonify({"error": "v4 は Postgres（DATABASE_URL）が必要です"}), 503
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON が読めません"}), 400
    seeker_id = require_self((body.get("seeker_id") or "").strip() or None)
    if not seeker_id:
        return jsonify({"error": "seeker_id が必要です"}), 400
    top_k = body.get("top_k")
    # 旧 v3 利用者の一括 v4 化は運用の操作なので、開発時（POX_DEBUG=1）だけ受け付ける。
    migrate_pool = bool(body.get("migrate_pool")) and _debug_enabled()

    from db_v4 import match_v4
    from embedding_config import MODEL_TAG
    from migrate_v4 import ensure_migrated
    store = _v4_store()
    loader = lambda uid: get_seeker(uid, db_path=DB)

    # §7-5: query 単位を必要像へ。明示 necessity_id か、seeker 本人の生きた必要像があれば
    # 必要像起点で照合し、無い/未ベクトル化なら人起点へフォールバック（個人は同一結果）。
    nec_id = body.get("necessity_id") or _seeker_live_necessity_id(seeker_id, db_path=DB)
    if body.get("necessity_id") and not _can_use_necessity(body["necessity_id"], seeker_id):
        return jsonify({"error": "この必要像で照合する権限がありません"}), 403
    # stub（意味を持たない擬似ベクトル）の間は照合の結果を出さない（指示書55-2 B-1-2）。
    if not _matching_available():
        return jsonify({"status": "unavailable", "results": [],
                        "reason": "照合の準備中です（埋め込みのモデルが設定されていません）"}), 200
    # F-5 遅延移行: seeker が v3.1 のみなら、ここで一度だけ v4 へ移行してから照合。
    try:
        ensure_migrated(store, seeker_id, loader)
        # 移行期のブリッジ: 明示要求時のみ既存 v3.1 全員も v4 化（既定は真の遅延）。
        if migrate_pool:
            for row in load_all_seekers(db_path=DB):
                ensure_migrated(store, row["id"], loader)
    except Exception as e:
        return jsonify({"error": f"移行失敗: {e}"}), 500

    # モデルの切替が済んでいない（現行タグを持たないベクトル化済みの人がいる）間は照合しない（185）。
    if store.count_missing_tag(MODEL_TAG):
        return jsonify({"status": "unavailable", "results": [],
                        "reason": "照合の準備中です（埋め込みのモデルを切り替えています）"}), 200
    # 必要像が無い人は照合しない（言語化への導線を出す。指示書55 §2-2）。
    if not nec_id and not ((store.get_necessity(seeker_id, MODEL_TAG) or {}).get("necessity_text") or "").strip():
        return jsonify({"status": "no_necessity", "results": []}), 200
    try:
        return jsonify(_v5_match_response(store, seeker_id, MODEL_TAG))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"照合失敗: {e}"}), 500


def _matching_available():
    """照合の結果を出せるか。stub のときは出さない（POX_DEBUG=1 の開発時だけ出す）。"""
    import embedding_config
    return embedding_config.BACKEND != "stub" or _debug_enabled()


def _linked_ids(ids):
    """ids のうち auth_identities に行がある（＝本人と紐づいている）もの（指示書55 §0-3）。"""
    ids = list(ids)
    if not ids:
        return set()
    from db_connect import get_connection
    ph = ",".join(["%s"] * len(ids))
    try:
        with auth._connect(DB) as con:
            rows = con.execute(f"SELECT subject_id FROM auth_identities WHERE subject_id IN ({ph})",
                               tuple(ids)).fetchall()
    except Exception:  # noqa: BLE001
        return set()
    return {r[0] for r in rows}


def _text_of(v):
    if isinstance(v, (list, tuple)):
        return " / ".join(str(x) for x in v if str(x).strip())
    return str(v or "").strip()


def _state_text(pv):
    """公開プロフィールの現状（4 スロット）を 1 つの本文にする。切り詰めない。"""
    pv = pv or {}
    return " / ".join(t for t in (_text_of(pv.get(k)) for k in
                                  ("state_have", "state_can_type", "state_bound", "state_unsorted")) if t)


@app.get("/api/connections/reason")
@login_required
def connection_reason():
    """承認の場面で見せる「なぜこの人か」（指示書55-5 §1・指示書57 §2-3）。照合の結果と同じ根拠
    （軸の名前＋引用の対。数値なし）。

    - **申し出に保存した版の参照から再計算**する（根拠のコピーは保存しない）。申し出た側の必要像・
      与え像は申し出た時点の版（necessity_ref・offer_ref）、受けた側は現在の版を使う。
    - 申し出・承認待ち・接続中の相手に限る（任意の人の照合を引けると除外や横断禁止を迂回できる）。
    - 判定の入口では切らない（説明のため）。照合できない（stub・切替中・ベクトルなし）ときは reason なし。
    """
    me = require_self(request.args.get("me"))
    other = (request.args.get("with") or "").strip()
    if not other or other == me:
        return jsonify({"error": "with が必要です"}), 400
    from ledger import connection_state, request_refs
    if connection_state(me, other, db_path=DB) == "none":
        return jsonify({"error": "申し出・接続の相手についてだけ見られます"}), 403
    if not is_postgres() or not _matching_available():
        return jsonify({"reason": None}), 200
    from embedding_config import MODEL_TAG
    store = _v4_store()
    if store.count_missing_tag(MODEL_TAG):
        return jsonify({"reason": None}), 200
    mine = _side_of(store, me, MODEL_TAG)
    theirs = _side_of(store, other, MODEL_TAG)
    # 申し出た側の版の参照（その目的・その時点の与え像）で上書きする
    for who, side in ((me, mine), (other, theirs)):
        ref = request_refs(who, me if who == other else other, db_path=DB)
        if ref:
            _apply_refs(side, ref, MODEL_TAG)
    # 照合と同じ判定（共鳴の門 → 必須の文の充足）。成立していない方向は出さない（指示書61 §3-2）。
    from matcher_v5 import judge_direction
    a_pairs = []
    for p in mine["purposes"]:
        ok, prs, _ = judge_direction(p, theirs)
        if ok:
            a_pairs = [{"kind": "fill_mine", "mine": x["need"], "theirs": x["offer"]} for x in prs]
            break
    b_pairs = []
    for q in theirs["purposes"]:
        ok, prs, _ = judge_direction(q, mine)
        if ok:
            b_pairs = [{"kind": "fill_theirs", "mine": x["offer"], "theirs": x["need"]} for x in prs]
            break
    if not a_pairs and not b_pairs:
        return jsonify({"reason": None}), 200
    axis = "mutual" if (a_pairs and b_pairs) else "fill"
    # 承認は自分の引き受けの判断なので、補完B（相手があなたに求めていること）を先に出す。
    chosen = (b_pairs[:1] + a_pairs[:1]) if axis == "mutual" else (b_pairs or a_pairs)
    items = _reason_items(chosen, mine, theirs, context="approval")
    connected = connection_state(me, other, db_path=DB) == "connected"
    _mark_noindex()
    # 接続中の相手は申し出の版の参照が無いので、現在のプロフィールで計算している（その旨を添える）。
    return jsonify({"reason": {"axis": axis, "reasons": items, "current_profile": connected}}), 200


def _apply_refs(side, ref, model_tag):
    """申し出に保存した版の参照で、片側の目的と与え像を差し替える（その時点の版で再計算するため）。
    必須の印と門の値（gate_s・gate_u）はその版の必要像の行から読む（指示書61。以前は必須を落としていた）。"""
    import v5
    if ref.get("necessity_ref"):
        vecs = v5.get_sentence_vectors("necessity", ref["necessity_ref"], model_tag, db_path=DB)
        if vecs:
            row = v5.necessity_body(ref["necessity_ref"], db_path=DB) or {}
            req = [bool((x or {}).get("必須")) for x in (row.get("sentences") or [])]
            pid = ref.get("purpose_id")
            dest = v5.get_sentence_vectors("dest", pid, model_tag, db_path=DB) if pid else []
            side["purposes"] = [{
                "purpose_id": pid, "label": "", "necessity_id": ref["necessity_ref"],
                "needs": [{"text": t, "vec": v, "required": req[i] if i < len(req) else False}
                          for i, (t, v) in enumerate(vecs)],
                "dest_vec": dest[0][1] if dest else side.get("will_vec"),
                "gate_s": row.get("gate_s"), "gate_u": row.get("gate_u")}]
    if ref.get("offer_ref"):
        vecs = v5.get_sentence_vectors("offer", ref["offer_ref"], model_tag, db_path=DB)
        if vecs:
            side["offers"], side["has_offer"] = [{"text": t, "vec": v} for t, v in vecs], True


# ── 照合の段3（指示書57・61）: 目的ごと・文単位・共鳴の門 ───────────────────────────
STATE_SLOTS = (("state_have", "持っているもの"), ("state_can_type", "できること（型）"),
               ("state_bound", "縛られているもの"), ("state_unsorted", "未分類"))


def _side_of(store, sid, model_tag, bundle=None):
    """照合の片側: 目的ごとの必要像の文と、与え像の文（ベクトル付き）、向かう先と門の値。

    v5 の人は文単位ベクトル。v4 の人（または文単位ベクトルがまだ無い人）は「目的 1 つ・与え像なし」と
    して読み、必要像の全文ベクトルと、与え像の代わりに現状（state_passage）を使う（v4 互換）。
    共鳴の門（指示書61）: 目的ごとの向かう先のベクトル（"dest"）。無ければ意志の全文（will_symmetric）で代える。
    v4 の人は意志の全文を向かう先とする。現状の欄ごとのベクトル（"state"）があれば、表示で該当する欄を選ぶのに使う。
    """
    import v5
    if bundle is None:
        bundle = store.get_bundle(sid, model_tag)
    vecs4 = (bundle or {}).get("vectors") or {}
    will_vec = vecs4.get("will_symmetric")
    doc = v5.get_doc(sid, db_path=DB) or {}
    labels = {p.get("purpose_id"): str(p.get("向かう先") or "") for p in (doc.get("purposes") or [])}
    purposes = []
    for n in v5.live_necessities_v5(sid, db_path=DB):
        vecs = v5.get_sentence_vectors("necessity", n["necessity_id"], model_tag, db_path=DB)
        if not vecs:
            continue
        req = [bool((x or {}).get("必須")) for x in n["sentences"]]
        dest = v5.get_sentence_vectors("dest", n["purpose_id"], model_tag, db_path=DB)
        purposes.append({"purpose_id": n["purpose_id"], "label": labels.get(n["purpose_id"], ""),
                         "necessity_id": n["necessity_id"],
                         "needs": [{"text": t, "vec": v, "required": req[i] if i < len(req) else False}
                                   for i, (t, v) in enumerate(vecs)],
                         "dest_vec": dest[0][1] if dest else will_vec,
                         "gate_s": n.get("gate_s"), "gate_u": n.get("gate_u")})
    offers, offer_id = [], None
    off = v5.latest_offer(sid, db_path=DB)
    if off:
        vecs = v5.get_sentence_vectors("offer", off["offer_id"], model_tag, db_path=DB)
        if vecs:
            offers, offer_id = [{"text": t, "vec": v} for t, v in vecs], off["offer_id"]
    has_offer = bool(offers)
    if not purposes and bundle:
        nec = bundle.get("necessity") or {}
        text = (nec.get("necessity_text") or "").strip()
        if text and vecs4.get("necessity_query"):
            purposes = [{"purpose_id": None, "label": "", "necessity_id": None,
                         "needs": [{"text": text, "vec": vecs4["necessity_query"], "required": False}],
                         "dest_vec": will_vec, "gate_s": nec.get("gate_s"), "gate_u": nec.get("gate_u")}]
    state_slots = []
    if not offers and vecs4.get("state_passage"):
        pv = get_profile_view(sid, db_path=DB) or {}
        offers = [{"text": _state_text(pv), "vec": vecs4["state_passage"]}]
        slot_vecs = dict(v5.get_sentence_vectors_indexed("state", sid, model_tag, db_path=DB))
        for i, (key, label) in enumerate(STATE_SLOTS):
            t = _text_of(pv.get(key))
            if t and i in slot_vecs and slot_vecs[i][0] == t:      # 現在の欄の文と同じものだけ使う
                state_slots.append({"label": label, "text": t, "vec": slot_vecs[i][1]})
        if not state_slots and any(_text_of(pv.get(k)) for k, _ in STATE_SLOTS):
            _backfill_state_slots(sid)
    dests = [p["dest_vec"] for p in purposes if p.get("dest_vec")] or ([will_vec] if will_vec else [])
    return {"purposes": purposes, "offers": offers, "has_offer": has_offer, "offer_id": offer_id,
            "dests": dests, "will_vec": will_vec, "state_slots": state_slots}


# 根拠の見出し（指示書61 §3）。並べるのは常に「必要像の文 ↔ 与え像の文（無ければ現状の欄）」。
_LABELS = {
    "card": {"fill_mine": ("あなたが必要としていること", "相手が力になれること", "相手の現状"),
             "fill_theirs": ("相手が必要としていること", "あなたが力になれること", "あなたの現状")},
    "approval": {"fill_mine": ("あなたが求めていること", "相手が力になれること", "相手の現状"),
                 "fill_theirs": ("相手があなたに求めていること", "あなたが力になれること", "あなたの現状")},
}


def _need_vec(side, text):
    for p in side.get("purposes") or []:
        for n in p.get("needs") or []:
            if n.get("text") == text:
                return n.get("vec")
    return None


def _state_slot_for(need_vec, side):
    """与え像が無い側の現状から、必要像の文に最も近い欄を選ぶ（表示用。判定は現状の全文で済んでいる）。"""
    from embedding_service import cosine
    slots = side.get("state_slots") or []
    if not slots or not need_vec:
        return None
    return max(slots, key=lambda x: cosine(need_vec, x["vec"]))


def _reason_items(pairs, mine_side, their_side, context="card"):
    """引用の対を表示用にする（数値なし）。与え像が無い側は現状の該当する欄（欄のベクトルが無ければ現状の全文）。"""
    out = []
    for p in pairs:
        kind = p["kind"]
        need_label, offer_label, state_label = _LABELS[context][kind]
        if kind == "fill_mine":
            need, offer, need_side, give_side = p["mine"], p["theirs"], mine_side, their_side
        else:
            need, offer, need_side, give_side = p["theirs"], p["mine"], their_side, mine_side
        item = {"kind": kind, "need_label": need_label, "need": need, "offer_label": offer_label, "offer": offer}
        if not give_side.get("has_offer"):
            slot = _state_slot_for(_need_vec(need_side, need), give_side)
            item["offer_label"] = f"{state_label}（{slot['label']}）" if slot else state_label
            if slot:
                item["offer"] = slot["text"]
        if kind == "fill_theirs" and context == "approval":
            item["takes_on"] = True               # 承認すると、これを引き受ける（自分の引き受け）
        out.append(item)
    return out


def _v5_match_response(store, me, model_tag):
    """照合の結果（目的ごとにグループ化）。数値・順位・件数は出さない。並びは中立（id 順）。

    除外: 必要像が無い人／本人と紐づいていない id／既に接続済みの相手／相手から申し出が来ている相手／
    **その目的で**申し出中の相手（別の目的のグループには出る）。相手ごとの理由は出さない。
    """
    from matcher_v5 import match_pair
    from ledger import engaged_by_purpose
    mine = _side_of(store, me, model_tag)
    if not mine["purposes"]:
        return {"status": "no_necessity", "groups": [], "results": []}
    ids = [i for i in store.candidate_ids(me, model_tag)]
    linked = _linked_ids(ids)
    blocked, offered = engaged_by_purpose(me, db_path=DB)
    ids = sorted(i for i in ids if i in linked and i not in blocked)
    bundles = store.get_bundles(ids, model_tag) if ids else {}
    names = _resolve_names(ids, fallback=UNNAMED_LABEL)
    import handles
    hs = handles.get_many(ids, db_path=DB)
    groups = {p["purpose_id"]: {"purpose_id": p["purpose_id"], "label": p["label"], "results": []}
              for p in mine["purposes"]}
    them_need_me = {"purpose_id": "_theirs", "label": "", "results": []}
    for cid in ids:
        theirs = _side_of(store, cid, model_tag, bundle=bundles.get(cid))
        if not theirs["purposes"]:
            continue                                     # 必要像の無い人は出さない
        r = match_pair(mine, theirs)
        pv = None
        done = offered.get(cid, set())
        for pid, res in r["by_purpose"].items():
            if pid in done:
                continue                                 # その目的では申し出中
            pv = pv or (get_profile_view(cid, db_path=DB) or {})
            groups[pid]["results"].append(_card(cid, names, hs, pv, res, mine, theirs, pid))
        if r["theirs_need_me"] and not done:
            pv = pv or (get_profile_view(cid, db_path=DB) or {})
            them_need_me["results"].append(_card(cid, names, hs, pv, r["theirs_need_me"], mine, theirs, None))
    out_groups = [g for g in groups.values() if g["results"]]
    if them_need_me["results"]:
        out_groups.append(them_need_me)
    flat = [c for g in out_groups for c in g["results"]]
    return {"status": "ok" if flat else "none", "groups": out_groups, "results": flat,
            "has_offer": mine["has_offer"], "match_run_id": f"run_{uuid.uuid4().hex[:8]}"}


def _card(cid, names, hs, pv, res, mine, theirs, purpose_id):
    return {"candidate_id": cid, "handle": hs.get(cid), "name": names.get(cid) or UNNAMED_LABEL,
            "one_liner": _text_of(pv.get("headline")), "purpose_id": purpose_id,
            "axis": res["axis"], "reasons": _reason_items(res["pairs"], mine, theirs)}


def _can_use_necessity(necessity_id, sid):
    """sid がこの必要像で照合してよいか（本人の必要像、または所属コミュニティの目的別必要像）。"""
    try:
        from necessities import get_necessity
        nec = get_necessity(necessity_id, db_path=DB)
    except Exception:  # noqa: BLE001
        return False
    if not nec:
        return False
    owner = nec.get("owner_ref")
    return owner == sid or _is_ctx_member(owner, sid)


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
    # 申し出の文（指示書55 §3-5）: 任意・上限あり。受けた本人だけが読む（通常DB。台帳に書かない）。
    message = (body.get("message") or "").strip()
    if len(message) > OFFER_MESSAGE_MAX:
        return jsonify({"error": f"申し出の文は {OFFER_MESSAGE_MAX} 字までです"}), 400
    # 承認（相手から申し出が来ている）は状態の遷移だけ。文は申し出る側の意思表示なので、承認では受け取らない
    # （DM は成立後だけ・テスト 177 との整合。指示書57 受理時の訂正）。
    from ledger import connection_state
    if message and connection_state(from_id, to_id, db_path=DB) == "pending_in":
        message = ""
    # 指示書57: どの目的の申し出か（自分の目的に限る）と、申し出た時点の自分の版の参照。
    import v5
    purpose_id = (body.get("purpose_id") or "").strip() or None
    necessity_ref = offer_ref = None
    if purpose_id:
        live = {n["purpose_id"]: n["necessity_id"] for n in v5.live_necessities_v5(from_id, db_path=DB)}
        if purpose_id not in live:
            return jsonify({"error": "この目的では申し出られません（自分の生きている目的を選んでください）"}), 400
        necessity_ref = live[purpose_id]
    off = v5.latest_offer(from_id, db_path=DB)
    offer_ref = off["offer_id"] if off else None

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
        message=message or None,
        purpose_id=purpose_id, necessity_ref=necessity_ref, offer_ref=offer_ref,
    )
    return jsonify(result), 200


OFFER_MESSAGE_MAX = 400


def _other_id(body):
    other = (body.get("to_id") or body.get("with") or "").strip()
    if not other:
        abort(400, "相手（to_id）が必要です")
    return other


@app.post("/api/connections/withdraw")
@login_required
def withdraw_connection_request():
    """自分の申し出（未成立）を取り下げる（指示書55 §3-4）。台帳に書かない。申し出の文も消える。
    申し出が無ければ withdrawn=false（冪等・200）。"""
    body = request.get_json(force=True, silent=True) or {}
    me = require_self(body.get("from_id"))
    from ledger import withdraw_request
    return jsonify({"withdrawn": withdraw_request(me, _other_id(body), db_path=DB)}), 200


@app.get("/api/my/purposes")
@login_required
def my_purposes():
    """本人の目的（サーバーの不変 id と向かう先）と、与え像の有無（指示書57）。本人のみ。
    与え像が無い間は「現状で照合しています」をマイページに常設する（v4 互換）。"""
    me = require_self(request.args.get("id"))
    import v5
    doc = v5.get_doc(me, db_path=DB) or {}
    labels = {p.get("purpose_id"): str(p.get("向かう先") or "") for p in (doc.get("purposes") or [])}
    live = [n["purpose_id"] for n in v5.live_necessities_v5(me, db_path=DB)]
    _mark_noindex()
    return jsonify({"purposes": [{"purpose_id": p, "label": labels.get(p, "")} for p in live],
                    "has_offer": bool((v5.latest_offer(me, db_path=DB) or {}).get("sentences"))}), 200


@app.get("/api/connections/state")
@login_required
def connection_state_route():
    """本人から見た相手との状態（connected / pending_out / pending_in / none）。画面の出し分け用。"""
    me = require_self(request.args.get("me"))
    other = (request.args.get("with") or "").strip()
    if not other:
        return jsonify({"error": "with が必要です"}), 400
    from ledger import connection_state
    return jsonify({"state": connection_state(me, other, db_path=DB)}), 200


@app.get("/api/my/offers")
@login_required
def my_received_offers():
    """自分宛ての申し出と、その文（**受けた本人だけ**。第三者・公開 API には出さない）。"""
    me = require_self(request.args.get("id"))
    from ledger import received_offers
    offers = received_offers(me, db_path=DB)
    names = _resolve_names([o["from"] for o in offers], fallback=UNNAMED_LABEL)
    _mark_noindex()
    return jsonify([{**o, "from_name": names.get(o["from"], UNNAMED_LABEL)} for o in offers]), 200


@app.post("/api/connections/offer-message/retract")
@login_required
def retract_offer_message_route():
    """送った本人が、自分の申し出の文を取り消す（成立後の会話の冒頭からも消える）。"""
    body = request.get_json(force=True, silent=True) or {}
    me = require_self(body.get("from_id"))
    from ledger import retract_offer_message
    return jsonify({"retracted": retract_offer_message(me, _other_id(body), db_path=DB)}), 200


def _snapshot_pair_resolver(founder, joiner):
    """成立時: 両者の最新 snapshot_id を返す（未再構造化は None）。指示書12 §4-2。"""
    try:
        from snapshots import latest_snapshot_id
        return {founder: latest_snapshot_id(founder, db_path=DB),
                joiner:  latest_snapshot_id(joiner, db_path=DB)}
    except Exception:  # noqa: BLE001
        return None


def _conn_ref_resolver(subject, purpose_id=None):
    """接続の根拠（指示書18 §1-2）: profile.structured の content_hash と
    necessity.published の event_hash（無ければ None）。null と空文字を区別する。
    指示書57: 目的のある申し出は、**その目的の最新の**必要像の event_hash（別の目的を根拠にしない）。"""
    try:
        from subject_ledger import latest_profile_content_hash
        from necessities import latest_published_event_hash
        import v5
        nh = (v5.latest_event_hash_for_purpose(subject, purpose_id, db_path=DB) if purpose_id
              else latest_published_event_hash(subject, db_path=DB))
        return {"profile_snapshot_hash": latest_profile_content_hash(subject, db_path=DB),
                "necessity_hash": nh}
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

    閲覧者の同一性は **セッション（subject_id）が唯一の根拠**（指示書36・25 §2-2）。
    `?viewer=` は後方互換で受け取るが**認可には使わない**（セッションと一致しなければ第三者扱い）。
    未ログインは 401 にせず**第三者**として応答する（第三者にも見せる情報があるため・§2-2）。
    """
    # ?viewer= の自己申告は使わない。本人/相手はセッションの subject_id だけで判定する。
    viewer = current_subject_id()   # 未ログインは None＝第三者
    is_owner = bool(viewer) and viewer == user_id

    try:
        vessels = load_all_vessels(db_path=DB)
    except Exception:  # noqa: BLE001
        vessels = []
    is_partner = _timeline_is_partner(user_id, viewer, vessels)
    viewer_role = "owner" if is_owner else ("partner" if is_partner else "third")

    # 第三者向け necessity_text の公開閾値（指示書11・§2-3）。/api/profile と同じ線を timeline にも
    # 適用する（閾値より前に作られた時点の必要像は第三者に出さない）。本人・相手は対象外。
    public_since = _necessity_public_since()

    def _necessity_public_for_third(created_at):
        from datetime import datetime, timezone
        if not created_at:
            return False
        try:
            dt = datetime.fromisoformat(str(created_at))
        except ValueError:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= public_since

    import snapshots as _snapshots
    import trajectory as _traj

    def _snaps_of(uid):
        try:
            return _snapshots.get_snapshots(uid, db_path=DB)
        except Exception:  # noqa: BLE001
            return []
    snaps = _snaps_of(user_id)

    # 接続の相手の表示名（生 id を表示に出さない・項目114／50 v3 136）
    others = set()
    for v in vessels:
        join = (v.get("joins") or [{}])[0]
        if user_id in (v.get("founder"), join.get("joiner")):
            others.add(join.get("joiner") if user_id == v.get("founder") else v.get("founder"))
    names = _resolve_names([o for o in others if o], fallback=UNNAMED_LABEL)

    # 平坦な items（後方互換）。可視性は指示書50 v3 §1: 第三者にも本文（伏せた時点を除く）、
    # 根拠・内部数値・raw は本人のみ。
    items = []
    for s in snaps:
        c = _traj._content(s, viewer_role, _necessity_public_for_third(s.get("created_at"))
                           if viewer_role == "third" else True)
        item = {"kind": "snapshot", "at": s.get("created_at"),
                "snapshot_id": s.get("snapshot_id"),
                "schema_version": s.get("schema_version", "")}
        if c is None:
            item["hidden"] = True            # 存在の事実のみ・中身は出さない
        else:
            item.update({k: v for k, v in c.items() if k != "numbers"})
            if viewer_role == "owner":
                item["numbers"] = c["numbers"]
            if viewer_role in ("owner", "partner"):
                item["vulnerable_hidden"] = bool(s.get("vulnerable_hidden"))
        items.append(item)

    # 接続の履歴事実（全層公開・理由なし）。表示は表示名（other は URL 用の識別子）。
    for v in vessels:
        join = (v.get("joins") or [{}])[0]
        founder, joiner = v.get("founder"), join.get("joiner")
        if user_id not in (founder, joiner):
            continue
        other = joiner if user_id == founder else founder
        est = join.get("established_at")
        if est:
            items.append({"kind": "connection", "event": "established", "at": est,
                          "other": other, "other_name": names.get(other) or UNNAMED_LABEL,
                          "vessel_id": v.get("vessel_id")})
        ts = join.get("terminal_state")   # 離脱・解消は「事実」として（理由は持たない）
        if ts and ts not in ("active", None) and join.get("closed_at"):
            items.append({"kind": "connection", "event": "ended", "at": join.get("closed_at"),
                          "other": other, "other_name": names.get(other) or UNNAMED_LABEL,
                          "vessel_id": v.get("vessel_id")})

    items.sort(key=lambda x: x.get("at") or "")
    tree = _traj.build(user_id, viewer_role, snaps, vessels, names=names,
                       necessity_public_for=(_necessity_public_for_third if viewer_role == "third"
                                             else (lambda _at: True)),
                       other_snaps_of=_snaps_of, db_path=DB)
    _mark_noindex()     # 軌跡は公開だが検索の対象にしない（切り取られた引用を招かない・50 v3 §3-5）
    return jsonify({"user_id": user_id, "is_owner": is_owner,
                    "viewer_role": viewer_role, "items": items, **tree})


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
    # 表示名を各 vessel に付ける（指示書30）。生 id は画面に出さない（指示書55 172）。
    ids = [v.get("founder") for v in mine]
    for v in mine:
        for j in (v.get("joins") or []):
            ids.append(j.get("joiner"))
    names = _resolve_names(ids, fallback=UNNAMED_LABEL)
    for v in mine:
        v["founder_name"] = names.get(v.get("founder"), UNNAMED_LABEL)
        for j in (v.get("joins") or []):
            j["joiner_name"] = names.get(j.get("joiner"), UNNAMED_LABEL)
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
    # 表示名（指示書30）。生 id は画面に出さない（指示書55 172）。未設定なら「表示名未設定」。
    # subject_id は画面の遷移用（URL）に返すだけで、表示には使わない。
    pv["subject_id"] = user_id
    import handles
    raw = _resolve_names([user_id], fallback=UNNAMED_LABEL, with_handle=False).get(user_id) or UNNAMED_LABEL
    pv["display_name_set"] = raw != UNNAMED_LABEL
    pv["display_name"] = raw                                   # 表示名だけ（編集欄の初期値）
    pv["handle"] = handles.get_handle(user_id, db_path=DB)     # 未設定なら None（本人に導線を出す）
    pv["display_label"] = _label(raw if pv["display_name_set"] else "", pv["handle"]) or UNNAMED_LABEL
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


_CORE_STATE_KEYS = ("state_have", "state_can_type", "state_bound", "state_unsorted")


def _edit_core_v4(profile_id, fields):
    """「中身を編集」を **profiles_v4 に直接反映**する（指示書18 作業B/C）。

    表示の正が profiles_v4 のため、編集を必ず表示へ通す。v3 seeker の有無に依存せず、
    リクエストで来た項目（fields: 意志 / state_*）を既存 v4 行へ上書きする。v4 未登録の
    ユーザー（＝表示が v3 フォールバックの人）は対象外で False を返す。

    既存必要像があるときだけ登録と同じ経路で v4 ベクトルを作り直す（必要像は各自AIの
    所有物のため **サーバー生成しない**・is_fallback=False。古い必要像テキストで再ベクトル化し
    status=ready・needs_regeneration は廃止・指示書28 §6-2）。derived_necessity は触らない。

    戻り値: profiles_v4 を更新したか（v4 未登録・非Postgres・変化なしは False）。
    """
    if not is_postgres():
        return False
    from db_v4 import receive_profile_v4, GEN_READY, MODEL_TAG
    store = _v4_store()
    existing = store.get_profile(profile_id)
    if existing is None:
        return False  # v4 未登録 → 表示は v3 フォールバック。対象なし

    def _pick(key_v4, key_field):
        return str(fields[key_field]) if key_field in fields else (existing.get(key_v4) or "")

    # 意志の編集を表示にも通す（指示書18 追補）。プロフィール表示の主要行
    # 「いま目指していること」は will_where = supporting_raw['意志_どこへ'] を優先し、
    # 無いときだけ will_text にフォールバックする。①由来の意志_どこへ があると、意志欄
    # （will_text）だけ編集しても表示が変わらない。そこで意志を編集したときは、照合用の
    # will_text と、表示用の意志_どこへ の**両方**を編集値に揃える（なぜ/経験の段は①の
    # ままにして触らない）。
    supporting = dict(existing.get("supporting_raw") or {})
    if "意志" in fields and str(fields["意志"]) != (existing.get("will_text") or ""):
        # 意志を実際に変更したときだけ、表示の主要行（意志_どこへ）も追従させる。
        # 変更が無い再保存では①由来の意志_どこへ を上書きしない（churn 防止）。
        supporting["意志_どこへ"] = str(fields["意志"])

    profile_input = {
        "will_text":      _pick("will_text", "意志"),
        "state_have":     _pick("state_have", "state_have"),
        "state_can_type": _pick("state_can_type", "state_can_type"),
        "state_bound":    _pick("state_bound", "state_bound"),
        "state_unsorted": _pick("state_unsorted", "state_unsorted"),
        "supporting_raw": supporting,   # 既存 v4 素材を保持しつつ意志_どこへ のみ追従
    }
    # 実変化が無ければ何もしない（churn 防止・不用意な状態遷移を避ける）。
    unchanged = (profile_input["will_text"] == (existing.get("will_text") or "")
                 and supporting == (existing.get("supporting_raw") or {})
                 and all(profile_input[k] == (existing.get(k) or "") for k in _CORE_STATE_KEYS))
    if unchanged:
        return False

    nec = store.get_necessity(profile_id, MODEL_TAG)
    will_vectorize = bool(nec and (nec.get("necessity_text") or "").strip())
    keep_status = (store.get_profile_status(profile_id) or {}).get("generation_status") or GEN_READY
    # 表示を必ず更新（necessity=None で derived_necessity 不変）。再ベクトル化時のみ READY。
    receive_profile_v4(store, profile_id, profile_input, necessity=None,
                       generation_status=GEN_READY if will_vectorize else keep_status)
    if will_vectorize:
        _spawn_v4_job(profile_id, profile_input, nec, is_fallback=False)
    return True


@app.post("/api/profile/<user_id>/core")
@login_required
def api_profile_core(user_id):
    """「中身を編集」。v4（意志/現状4スロット）対応。profile_view を再生成する。
    **本人のみ**（指示書55 PR-A: 以前は未認証で誰のプロフィールでも書き換えられた）。"""
    user_id = require_self(user_id)
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
    # v3（profiles/seekers）を更新（行があれば）。v4 のみのユーザーは False になる。
    out = {}
    v3_ok = update_seeker_core(user_id, fields, db_path=DB, out=out)
    # v4（profiles_v4・表示の正）へ直接反映。v4 未登録なら False。
    v4_changed = False
    try:
        v4_changed = _edit_core_v4(user_id, fields)
    except Exception as e:  # noqa: BLE001
        app.logger.warning(f"[edit-core-v4] skip（v3編集は保存済み）: {e}")
    if not v3_ok and not v4_changed:
        return jsonify({"error": "プロフィールが見つかりません"}), 404
    changed = bool(out.get("changed_core")) or v4_changed
    return jsonify({"ok": True, "changed_core": changed})


@app.put("/api/profile/<user_id>/overrides")
@login_required
def api_profile_overrides(user_id):
    """「見せ方を編集」の保存。view_overrides のみ更新（再生成なし §5.1）。
    **本人のみ**（指示書55 PR-A: 以前は未認証で誰の見せ方でも書き換えられた）。"""
    user_id = require_self(user_id)
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


# 構造化プロンプトの唯一の正（指示書60 §1）。画面はここから読み込み、本文をテンプレートに直書きしない。
PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "prompts")
PROMPT_FILES = {"prompt_a": "v5_A_dialogue.txt", "prompt_b": "v5_B_selfwrite.txt"}


def _read_prompts():
    out = {}
    for key, name in PROMPT_FILES.items():
        with open(os.path.join(PROMPT_DIR, name), encoding="utf-8") as f:
            out[key] = f.read()
    return out


@app.get("/register")
def register():
    return render_template("register.html", **_read_prompts())


@app.get("/profile/<seeker_id>")
def profile(seeker_id):
    """旧 URL（生 id）。ハンドルがあれば /u/<ハンドル> へ移す（アドレス欄に生 id を出さない。188）。
    ハンドル未設定の人はこの URL のまま（設定するまで表示名のみ）。"""
    import handles
    h = handles.get_handle(seeker_id, db_path=DB)
    if h:
        return redirect(f"/u/{h}", code=301)
    return render_template("profile.html", profile_subject=seeker_id)


@app.get("/u/<handle>")
def profile_by_handle(handle):
    """プロフィールの URL（指示書55 §3-7）。退役したハンドルは誰にも解決しない（404）。"""
    import handles
    sid = handles.subject_for(handle, db_path=DB)
    if sid is None:
        abort(404)
    return render_template("profile.html", profile_subject=sid)


@app.post("/api/my/handle")
@login_required
def set_my_handle():
    """ハンドルの初回設定（既存ユーザーの導線）。**一意・不変**。設定済みなら 409（変えられない）。"""
    import handles
    body = request.get_json(force=True, silent=True) or {}
    sid = require_self(body.get("id"))
    if not sid:
        return jsonify({"error": "ログインが必要です"}), 401
    try:
        h = handles.set_handle(sid, body.get("handle") or "", db_path=DB)
    except handles.HandleError as e:
        return jsonify({"error": str(e)}), 400
    except (handles.HandleTaken, handles.HandleImmutable) as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"handle": h}), 201


@app.get("/api/handle/check")
@login_required
def check_handle():
    """ハンドルが使えるか（書式・重複）。書き込まない。"""
    import handles
    try:
        h = handles.check(request.args.get("h") or "", current_subject_id(), db_path=DB)
    except handles.HandleError as e:
        return jsonify({"available": False, "error": str(e)}), 200
    except handles.HandleTaken as e:
        return jsonify({"available": False, "error": str(e)}), 200
    return jsonify({"available": True, "handle": h}), 200


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


@app.get("/trajectory/<user_id>")
def trajectory_page(user_id):
    """軌跡のページ（指示書50 v3 §3-5）。過去版は単独ページにせず #vN のアンカーで指す。
    公開（誰でも読める）だが検索の対象にしない（X-Robots-Tag: noindex）。"""
    _mark_noindex()
    return render_template("trajectory.html", user_id=user_id)


@app.get("/talk/<talk_id>")
def talk_page(talk_id):
    """トーク詳細ページ（指示書43 §2-1: モーダルを廃止し独立ページに）。

    行為の導線（参加の申し出・達成の提案・発言欄・票）と参加者の一覧は、閲覧者ごとに
    サーバー側でレンダリングする（指示書48 §4-2: 表示の可否をレンダリング結果で検証できる）。
    メンバー限定のトークを第三者が開いた場合は view=None（API と同じく存在を出さない）。"""
    import talks
    view = None
    talk = talks.get_talk(talk_id, db_path=DB)
    if talk is not None and talk["kind"] in (talks.CHAT, talks.ADMISSION):
        _mark_noindex()                                  # 加入トーク・チャットは索引させない（45 A-3）
    if talk is not None and not (talk["kind"] in (talks.CHAT, talks.ADMISSION)
                                 and not _is_ctx_member(talk["ctx"], current_subject_id())):
        view = _talk_public_view(talk)
    return render_template("talk.html", view=view)


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
    # DM は接続の成立後だけ（指示書55 §0-10）。接続前は「申し出」（とその文）だけを送れる。
    from ledger import is_connected
    if not is_connected(from_id, to_id, db_path=DB):
        return jsonify({"error": "メッセージは接続が成立してから送れます。まず「つながりたいと伝える」から申し出てください。"}), 403
    msg = send_message(from_id, to_id, msg_body, attachment_url=attachment_url, db_path=DB)
    return jsonify(msg), 201


# ── 表示名の解決（指示書30）─────────────────────────────────────────────────
# 同一性は subject_id。表示名は通常DBの可変値で、台帳/ハッシュ/三つ組には入れない。
# コミュニティは communities.name を正とし、display_names には二重に持たない（食い違い防止）。

UNNAMED_LABEL = "表示名未設定のアカウント"


def _resolve_names(subject_ids, fallback=None, with_handle=True):
    """複数 subject_id → {sid: 表示}。community は communities.name、個人は「表示名（@ハンドル）」
    （指示書55-2 PR-D。表示名が無ければ「@ハンドル」、どちらも無ければ fallback＝未指定なら subject_id）。
    参加・加入系の画面は fallback=UNNAMED_LABEL を渡し、生 id を表示に出さない（指示書48 114・テスト49）。"""
    import display_names, handles
    ids = [s for s in {s for s in subject_ids if s}]
    if not ids:
        return {}
    dn = display_names.get_many(ids, db_path=DB)
    hs = handles.get_many(ids, db_path=DB) if with_handle else {}
    out = {}
    for s in ids:
        c = get_community(s, db_path=DB)          # community_id は subject_id と同空間
        if c:
            out[s] = c["name"] or (fallback or s)
            continue
        out[s] = _label(dn.get(s), hs.get(s)) or (fallback or s)
    return out


def _label(name, handle):
    """表示は「表示名（@ハンドル）」（指示書55 §0-9）。片方しか無ければある方だけ。"""
    if name and handle:
        return f"{name}（@{handle}）"
    if handle:
        return f"@{handle}"
    return name or ""


def _resolve_name(subject_id):
    return _resolve_names([subject_id]).get(subject_id, subject_id) if subject_id else subject_id


@app.post("/api/my/display-name")
@login_required
def set_my_display_name():
    """本人の表示名を設定/変更（上書き・履歴なし）。空にすると未設定＝subject_id 表示に戻る。
    redact・30字上限・本人限定（require_self）。一意性は課さない。"""
    body = request.get_json(force=True, silent=True) or {}
    sid = require_self(body.get("id"))
    if not sid:
        return jsonify({"error": "ログインが必要です"}), 401
    import display_names
    saved = display_names.set_display_name(sid, body.get("name") or "", db_path=DB)
    return jsonify({"subject_id": sid, "display_name": saved or sid, "is_set": saved is not None})


@app.get("/api/conversation")
@login_required
def api_conversation():
    """本人が当事者の会話のみ返す。me はセッション本人限定にゲート（指示書22 第2群）。"""
    me    = require_self(request.args.get("me"))
    other = request.args.get("with")
    if not me or not other:
        return jsonify({"error": "me と with が必要です"}), 400
    msgs = get_conversation(me, other, db_path=DB)
    # 成立後は申し出の文を会話の冒頭に残す（指示書55 §3-5。送った本人が取り消したものは出ない）。
    from ledger import offer_messages_between, is_connected
    offers = []
    if is_connected(me, other, db_path=DB):
        offers = [{"kind": "offer", "from_id": o["from"], "to_id": o["to"], "body": o["message"],
                   "created_at": o["at"]} for o in offer_messages_between(me, other, db_path=DB)]
    msgs = offers + list(msgs)
    names = _resolve_names([m["from_id"] for m in msgs] + [me, other], fallback=UNNAMED_LABEL)
    for m in msgs:
        m["from_name"] = names.get(m["from_id"], UNNAMED_LABEL)
    return jsonify(msgs)   # 配列のまま（各要素に from_name を付与・後方互換）


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
    # メッセージ一覧は表示名のみでよい（指示書30）。
    names = _resolve_names([c.get("other_id") for c in convs], fallback=UNNAMED_LABEL)
    for c in convs:
        c["other_name"] = names.get(c.get("other_id"), UNNAMED_LABEL)
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


def _intent_public(intent):
    """指示書39（§3-1 の判断）: 合意前の提起は第三者に見せない。
      proposed（合意前）        … 非公開（メンバーのみ）
      agreed / completed        … 公開
      cancelled（合意後の取消）  … 公開（成立した事実の後の終了）
      cancelled（合意前の取消）  … 非公開（痕跡を残さない）
    判定は台帳から算出済みの status / agreed だけを使い、台帳イベントには触れない。"""
    st = (intent or {}).get("status")
    if st in ("agreed", "completed"):
        return True
    if st == "cancelled":
        return intent.get("agreed") is True     # 合意後に取り消されたものだけ公開
    return False                                # proposed（合意前）


def _is_ctx_member(ctx, viewer):
    """viewer が ctx（コミュニティ）の成立メンバーか（founder 含む）。判定はセッション基準。"""
    if not viewer or not ctx:
        return False
    return is_member(ctx, viewer, db_path=DB) or has_founder_rights(ctx, viewer, db_path=DB)


def _intent_visible(intent, is_member_viewer):
    """指示書39（§3-1）: 閲覧者ロール別の可視性。
      合意前の cancelled … 痕跡を残さない（メンバーにも第三者にも出さない。台帳だけが保持）
      proposed（合意前）  … メンバーのみ
      公開分（agreed/completed/合意後cancelled）… 誰でも
    ※台帳イベントには触れない（読み出し面の出し分けのみ）。"""
    st = (intent or {}).get("status")
    if st == "cancelled" and intent.get("agreed") is not True:
        return False                        # 合意前キャンセルは痕跡を残さない
    if is_member_viewer:
        return True                         # メンバーは proposed 含めて見える
    return _intent_public(intent)           # 第三者は公開分のみ


@app.get("/api/community/<community_id>")
def api_get_community(community_id):
    """コミュニティ詳細。公開面は API そのもの（指示書38 §1-3）なので、閲覧者の
    ロールごとに「返す項目を明示的に列挙」する allowlist にする（§2-5）。

    公開/非公開（指示書41 §3・§8-2 で更新）:
      - members（承認後＝成立した関係）・宣言・実績 … 公開
      - pending（参加申請） … 公開（加入は公開・名前付きの加入トークとして扱う・§8-2 差し戻し）
      - messages（チャット） … メンバーのみ（§2-3・§8-2 で維持）
      - intents（旧 intent.*） … 旧版の可視性ルールを維持（合意前提起はメンバーのみ・§8-2）。
        新しいトーク（proposal/project 等）は /api/community/<id>/talks で公開・名前付き（§3）。
    判定はセッションの subject_id（指示書36。クエリ引数の自己申告は使わない）。
    """
    c = get_community(community_id, db_path=DB)
    if c is None:
        return jsonify({"error": "コミュニティが見つかりません"}), 404

    viewer  = current_subject_id()                     # 未ログインは None＝第三者
    members = get_members(community_id, db_path=DB)     # status='active' のみ＝成立した関係
    member_ids = {m.get("member_id") for m in members}
    is_mem = viewer is not None and viewer in member_ids

    from intent_ledger import list_intent_details, latest_policy_declaration
    declaration = latest_policy_declaration(community_id, db_path=DB)   # agreed/completed の policy のみ＝公開安全
    intents = list_intent_details(community_id, db_path=DB)
    # 指示書39（§3-1）: 合意前の提起はメンバーのみ、合意前キャンセルは痕跡を残さない。
    intents = [it for it in intents if _intent_visible(it, is_mem)]

    # pending（参加申請）はメンバー限定（指示書43 T-8・44 §4: 加入は「人に付く棄却」であり
    # 第三者に見せない。41 §8-2 の公開を 43 が優先して非公開へ再是正。締め付け方向で安全側）。
    pending_rows = get_pending_requests(community_id, db_path=DB) if is_mem else []

    # チャット（messages）はメンバー間のやりとりで、宣言でも実績でもない → メンバーのみ（§2-3・§8-2 維持）。
    # 第三者・申請者・無関係ログインには返さない。
    messages_rows = get_community_messages(community_id, db_path=DB) if is_mem else []

    names = _resolve_names(list(member_ids)
                           + [p.get("member_id") for p in pending_rows]
                           + [m.get("from_id") for m in messages_rows]
                           + [c.get("founder")], fallback=UNNAMED_LABEL)

    # ── allowlist：返す項目を明示的に列挙（除外方式にしない・§2-5）──
    out = {
        "id":           c.get("id"),
        "name":         c.get("name"),
        "description":  c.get("description"),
        "created_at":   c.get("created_at"),
        # 成立した関係（承認後メンバー）は公開（§2-4）。表示名を主に、id を併記（指示書30）。
        "founder":      c.get("founder"),
        "founder_name": names.get(c.get("founder"), c.get("founder")),
        "members": [{"member_id":   m.get("member_id"),
                     "display_name": names.get(m.get("member_id"), m.get("member_id")),
                     "status":       m.get("status"),
                     "joined_at":    m.get("joined_at")} for m in members],
        "member_count": len(members),
        # 宣言・実績（完成条件そのもの）は公開。
        "declaration":  declaration,
        "intents":      intents,
        # 申請者自身は自分の申請状態を知れるよう viewer_role で示す（pending 本体はメンバーのみ）。
        "applied": (viewer is not None and any(p.get("member_id") == viewer
                    for p in get_pending_requests(community_id, db_path=DB))),
        "viewer_role": ("member" if is_mem
                        else "authenticated" if viewer is not None else "guest"),
    }
    # 本人面（指示書45 A-2）: 申請者本人には自分の申請の結果と要約だけを返す。審議の内容は返さない。
    if viewer is not None and not is_mem:
        import talks
        mine = talks.admission_outcome_for(community_id, viewer, db_path=DB)
        if mine:
            out["my_application"] = mine
            _mark_noindex()
    # pending（参加申請）はメンバーのみ（§43 T-8・44 §4）。キーごと出し分ける。
    if is_mem:
        _mark_noindex()                  # pending・messages を含む応答は索引させない（45 A-3）
        out["pending"] = [{"member_id":   p.get("member_id"),
                           "display_name": names.get(p.get("member_id"), p.get("member_id")),
                           "joined_at":    p.get("joined_at")} for p in pending_rows]
    # messages はメンバーのみ（§2-3・§8-2 維持）。キーごと出し分ける。
    if is_mem:
        out["messages"] = [{**m, "from_name": names.get(m.get("from_id"), m.get("from_id"))}
                           for m in messages_rows]
    return jsonify(out)


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
    if result.pop("duplicate", False):
        # 審議中の申請がある間は新規の申請を受け付けない（二重申請の防止・45A 追補2）
        return jsonify({"error": "pending",
                        "detail": "このコミュニティへの参加申請は審議中です。結果が出るまでお待ちください。"}), 409
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
    if not has_founder_rights(community_id, approver_id, db_path=DB):
        return jsonify({"error": "承認権限がありません"}), 403
    result = approve_member(community_id, member_id, db_path=DB)
    return jsonify(result), 200


# 指示書41 §4-4/§8-1: 旧 intent.* の新規書き込みは停止（凍結）。読み取り系は残す。
# 提議・合意・取消・完了・参加は、新しいトーク（/api/community/<id>/talks・投票）へ移行した。
_FROZEN_OLD_INTENT = ({
    "error": "gone",
    "detail": "この経路は凍結されました（指示書41）。提議・合意・参加・達成は "
              "/api/community/<id>/talks とその投票（/api/talks/<id>/vote）で行ってください。",
}, 410)


@app.post("/api/community/<community_id>/intent/propose")
def api_intent_propose(community_id):
    """（凍結）旧: intent.proposed を書く提議。→ 提議トーク（kind=proposal）へ（§4-4/§8-1）。"""
    return jsonify(_FROZEN_OLD_INTENT[0]), _FROZEN_OLD_INTENT[1]


def _community_declaration_from_payload(payload):
    """コミュニティ版①の出力JSON → propose 用 declaration dict（指示書28 §4）。

    2種類を判別:
      - 全体用（subject_kind="community"）→ kind="community_overall"（意志・現状＋必要像）
      - 目的別（kind="intent_necessity"）  → kind="intent_necessity"（意志・現状なし）
    第4/5条のサーバー側軽検査（§4-6）: 明らかな PII は redact_text で落とす（完全検出は目指さない）。
    """
    from pii_redaction import redact_text

    def R(x):
        return redact_text(x) if isinstance(x, str) else ""

    def _nec_block(body):
        nec = body.get("necessity") or {}
        sm = body.get("supporting_material") or {}
        seeking = sm.get("求めている") or ""
        if seeking == "未取得":
            seeking = ""
        meta = body.get("_meta") or {}
        return {
            "necessity_text": R(nec.get("necessity_text")),
            "evidence_span": R(nec.get("evidence_span")),
            "seeking": R(seeking),
            "gate_s": nec.get("gate_s"), "gate_u": nec.get("gate_u"),
            "p_sharpness": nec.get("p_sharpness"), "alpha": nec.get("alpha"), "beta": nec.get("beta"),
            "generator": str(nec.get("generator") or ""),
            "generator_tag": (str(meta.get("source") or "") + "/" if meta.get("source") else "")
                             + str(nec.get("generator") or ""),
        }

    if not isinstance(payload, dict):
        return None
    if payload.get("kind") == "intent_necessity":
        return {"kind": "intent_necessity",
                "purpose_text": R(payload.get("purpose_text")),
                "necessity": _nec_block(payload)}
    if payload.get("subject_kind") == "community":
        seeker = payload.get("seeker") or {}
        state = seeker.get("現状") or {}
        decl = {"kind": "community_overall", "will_text": R(seeker.get("意志"))}
        for eng, jp in _V4_STATE_MAP.items():
            decl[eng] = R(state.get(jp, ""))
        decl["necessity"] = _nec_block(payload)
        return decl
    return None


@app.post("/api/community/<community_id>/declare")
def api_community_declare(community_id):
    """（凍結）旧: ①JSON を提議（intent.proposed）。→ 提議トークへ（§4-4/§8-1）。"""
    return jsonify(_FROZEN_OLD_INTENT[0]), _FROZEN_OLD_INTENT[1]


@app.get("/api/community/<community_id>/completed-episodes")
@login_required
def api_community_completed_episodes(community_id):
    """コミュニティ版①（全体用）に貼る「完了した取り組み」を決定的規則で返す（§4-4）。
    0件→なし / 1〜3件→全件 / 4件以上→直近3件。提起者（メンバー）向け。"""
    from intent_ledger import completed_episodes_for_prompt
    from member_ledger import active_members_from_events
    me = current_subject_id()
    if me is not None and me not in active_members_from_events(community_id, db_path=DB) and not _debug_enabled():
        return jsonify({"error": "コミュニティのメンバーのみ"}), 403
    return jsonify(completed_episodes_for_prompt(community_id, db_path=DB))


@app.post("/api/intent/<intent_id>/agree")
def api_intent_agree(intent_id):
    """（凍結）旧: intent.agreed。→ 目的の合意は提議トークの投票（purpose.agreed）へ（§4-4）。"""
    return jsonify(_FROZEN_OLD_INTENT[0]), _FROZEN_OLD_INTENT[1]


@app.post("/api/intent/<intent_id>/complete")
def api_intent_complete(intent_id):
    """（凍結）旧: intent.completed。→ 達成は project_complete トークの投票へ（§4-4/§8-1）。"""
    return jsonify(_FROZEN_OLD_INTENT[0]), _FROZEN_OLD_INTENT[1]


@app.post("/api/intent/<intent_id>/cancel")
def api_intent_cancel(intent_id):
    """（凍結）旧: intent.cancelled。取り消しは存在しない（§3-3・§4-4）。"""
    return jsonify(_FROZEN_OLD_INTENT[0]), _FROZEN_OLD_INTENT[1]


@app.post("/api/intent/<intent_id>/participant/join")
def api_intent_participant_join(intent_id):
    """（凍結）旧: intent.participant.joined。→ 参加は project_join トークの投票へ（§4-4）。"""
    return jsonify(_FROZEN_OLD_INTENT[0]), _FROZEN_OLD_INTENT[1]


@app.get("/api/intent/<intent_id>")
def api_intent_get(intent_id):
    from intent_ledger import get_intent
    r = get_intent(intent_id, db_path=DB)
    if not r:
        return jsonify({"error": "not_found"}), 404
    # 指示書39（§3-1）: 合意前の提起はメンバー以外に、合意前キャンセルは誰にも存在を見せない
    # （not_found を返す＝痕跡を残さない）。判定はセッション基準。
    if not _intent_visible(r, _is_ctx_member(r.get("ctx"), current_subject_id())):
        return jsonify({"error": "not_found"}), 404
    return jsonify(r), 200


@app.get("/api/community/<community_id>/intents")
def api_community_intents(community_id):
    from intent_ledger import list_intents
    intents = list_intents(community_id, db_path=DB)
    is_mem = _is_ctx_member(community_id, current_subject_id())
    # 指示書39（§3-1）: 合意前の提起はメンバーのみ、合意前キャンセルは痕跡を残さない。
    intents = [it for it in intents if _intent_visible(it, is_mem)]
    return jsonify({"ctx": community_id, "intents": intents}), 200


# ══ トーク（指示書41 §3）══════════════════════════════════════════════════
def _is_participant(intent_id, sid):
    if not sid or not intent_id:
        return False
    import governance as gov
    return sid in gov.participants_at(intent_id, 10**18, db_path=DB)


def _is_join_offerer_only(talk, sid):
    """参加トークで sid が「申し出た当人」であり既存参加者ではないか。当人は分母外なので
    賛成・反対の票を持たない（指示書48 108-r）。発言（申し出の説明）はできる。"""
    import talks
    if talk["kind"] != talks.PROJECT_JOIN:
        return False
    tgt = talk.get("target") or {}
    return sid == tgt.get("participant") and not _is_participant(tgt.get("intent_id"), sid)


def _member_of_any_community(sid):
    """sid がいずれかのコミュニティのメンバー（または代表）か（指示書48 §1-2 の所属判定）。"""
    if not sid:
        return False
    from community import get_all_communities, is_member, is_founder
    return any(is_member(c["id"], sid, db_path=DB) or has_founder_rights(c["id"], sid, db_path=DB)
               for c in get_all_communities(db_path=DB))


def _project_join_offer(talk, sid):
    """実行中プロジェクトの「参加を申し出る」導線の出し分け（指示書48 §1-2）。

    返り値 None は「導線を出さない」。dict は {individual, community, affiliation}:
      - 立ち上げ者・承認済み参加者（＝当事者）: None（108-k：申し出は出さない）
      - 所属コミュニティの一般メンバー（未参加）: 個人のみ（108-p／108-m）
      - 他コミュニティメンバー・所属のある外部個人（未参加）: 個人＋コミュニティの両方
      - どのコミュニティにも所属がない（未参加）: 個人のみ＋「所属していない」旨（108-n）
      - 未ログイン: None（112：導線なし。行為は 401）
    """
    import talks
    if talk["kind"] != talks.PROJECT:
        return None
    if not sid or talks.is_closed(talk, db_path=DB):
        return None
    intent_id = (talk.get("target") or {}).get("intent_id")
    if _is_participant(intent_id, sid):
        return None                                    # 108-k: 当事者には申し出を出さない
    if _is_ctx_member(talk["ctx"], sid):
        # 108-p / 108-m: 所属コミュニティの一般メンバーは個人としてのみ
        return {"individual": True, "community": False, "affiliation": "owning_member"}
    if _member_of_any_community(sid):
        return {"individual": True, "community": True, "affiliation": "other_community"}
    # 108-n: 所属コミュニティが無い → 個人としてのみ＋「所属していない」旨
    return {"individual": True, "community": False, "affiliation": "none"}


def _talk_public_view(talk):
    """公開ビュー（allowlist）。表示名を解決して返し、生 subject id を表示の主にしない
    （指示書43 T-6/M-1・44 §2-4）。集計・順位は作らない（§3-2）が、当事者判断のため
    賛成・反対したアカウントは公開トークなので氏名で開示する（沈黙者は出さない）。"""
    import talks
    v = talks.get_votes(talk["talk_id"], db_path=DB)
    posts = talks.get_posts(talk["talk_id"], db_path=DB)
    ids = (list(v["approvals"]) + list(v["dissents"])
           + [p["author"] for p in posts] + [talk["created_by"]])
    names = _resolve_names(ids, fallback=UNNAMED_LABEL)

    def who(sid):
        return {"subject_id": sid, "display_name": names.get(sid, sid)}
    # プロジェクト（実行トーク）は現参加者を表示名つきで返す（P-1 の参加UI用）。
    participants = []
    if talk["kind"] == talks.PROJECT and (talk.get("target") or {}).get("intent_id"):
        import governance as gov
        pids = sorted(gov.participants_at(talk["target"]["intent_id"], 10**18, db_path=DB))
        pnames = _resolve_names(pids, fallback=UNNAMED_LABEL)
        participants = [{"subject_id": p, "display_name": pnames.get(p, p)} for p in pids]
    # 参加・達成の決定トークは「基準点の時点の分母（参加者頭数）」を表示名つきで返す（指示書47 §4）。
    # 分母は基準点の時点の既存参加者のみ。申し出た当人は含めない（指示書48 108-r）。
    denominator = None
    if talk["kind"] in (talks.PROJECT_JOIN, talks.PROJECT_COMPLETE):
        import governance as gov
        tgt = talk.get("target") or {}
        dset = gov.participants_at(tgt.get("intent_id"), talk["basis_seq"], db_path=DB)
        dnames = _resolve_names(sorted(dset), fallback=UNNAMED_LABEL)
        denominator = [{"subject_id": p, "display_name": dnames.get(p, p)} for p in sorted(dset)]
    # 実行中プロジェクトの参加申し出の出し分け・当事者の達成提案可否（指示書48 §1-2 / 111）。
    sid = current_subject_id()
    join_offer = _project_join_offer(talk, sid)
    closed = talks.is_closed(talk, db_path=DB)
    can_propose_complete = bool(
        talk["kind"] == talks.PROJECT and not closed
        and _is_participant((talk.get("target") or {}).get("intent_id"), sid))
    # 発言欄・投票の出し分け（指示書48 115）。権限の最終判定は API（403）。
    can_post = bool(sid and not closed and _can_participate_talk(talk, sid))
    # 離脱の導線（当事者・実行中のみ）。最後の 1 人は出すが押せない（理由を表示・51 (a)）
    leave_state = None
    if talk["kind"] == talks.PROJECT and not closed and sid:
        _iid = (talk.get("target") or {}).get("intent_id")
        if _is_participant(_iid, sid):
            import governance as _gov
            leave_state = ("last" if _gov.participants_at(_iid, 10**18, db_path=DB) == {sid}
                           else "can")
    can_vote = bool(can_post and talk["kind"] in talks.DECISION_KINDS
                    and not _is_join_offerer_only(talk, sid))
    # 加入の見送りの確定（メンバーのみ・反対が表明されているときだけ・45 A-1）
    can_decline = bool(can_post and talk["kind"] == talks.ADMISSION
                       and talks.can_decline_admission(talk, db_path=DB))
    return {**talk,
            "display_status": talks.display_status(talk, db_path=DB),
            "closed": talks.is_closed(talk, db_path=DB),
            "origin": talks.origin_of(talk, db_path=DB),            # プロジェクトの出自（親の提議）
            "child_project": talks.child_project_of(talk, db_path=DB),  # 合意済み提議の子
            "participants": participants,
            "join_offer": join_offer,            # 参加申し出の出し分け（None＝出さない・§48 §1-2）
            "can_propose_complete": can_propose_complete,   # 当事者の達成提案可否（§48 111）
            "can_post": can_post,                # 発言欄を出すか（§48 115）
            "can_vote": can_vote,
            "can_decline": can_decline,
            "leave_state": leave_state,          # 離脱の導線（None／"can"／"last"・指示書51）          # 加入の見送りを確定できるか（45 A-1）                # 賛成/反対を出すか（分母外の申し出者には出さない・108-r）
            "denominator": denominator,          # 参加・達成の分母（基準点の参加者頭数・§47 §4）
            "created_by_name": names.get(talk["created_by"], talk["created_by"]),
            "posts": [{"post_id": p["post_id"], "author": p["author"],
                       "author_name": names.get(p["author"], p["author"]),
                       "body": p["body"], "created_at": p["created_at"]} for p in posts],
            "approvals": [who(s) for s in sorted(v["approvals"])],
            "dissents": [who(s) for s in sorted(v["dissents"])]}


@app.post("/api/community/<community_id>/talks")
@login_required
def api_create_talk(community_id):
    """トークを立ち上げる（§3）。基準点と規則の版は作成時点で固定される（§5-5）。"""
    import talks
    body = request.get_json(force=True, silent=True) or {}
    sid = current_subject_id()
    kind = (body.get("kind") or "").strip()
    title = (body.get("title") or "").strip()
    target = body.get("target") or {}
    if kind not in talks.KINDS:
        return jsonify({"error": "未知の kind"}), 400
    if not title:
        return jsonify({"error": "title が必要です"}), 400
    # 認可: コミュニティ系（chat/proposal/admission/project）はメンバー。
    if kind in (talks.CHAT, talks.PROPOSAL, talks.ADMISSION, talks.PROJECT):
        if not _is_ctx_member(community_id, sid):
            return jsonify({"error": "コミュニティのメンバーのみ"}), 403
    elif kind == talks.PROJECT_JOIN:
        # 参加は本人（自分の参加）か既存参加者が起票できる。
        if not (sid == target.get("participant") or _is_participant(target.get("intent_id"), sid)):
            return jsonify({"error": "本人または参加者のみ"}), 403
    elif kind == talks.PROJECT_COMPLETE:
        if not _is_participant(target.get("intent_id"), sid):
            return jsonify({"error": "プロジェクトの参加者のみ"}), 403
    talk = talks.create_talk(community_id, kind, title, sid, target=target, db_path=DB)
    return jsonify(talk), 201


@app.post("/api/community/<community_id>/projects/launch")
@login_required
def api_launch_project(community_id):
    """合意された目的からプロジェクトを立ち上げる（§2-4）。intent.launched を台帳へ。"""
    import talks
    body = request.get_json(force=True, silent=True) or {}
    sid = current_subject_id()
    if not _is_ctx_member(community_id, sid):
        return jsonify({"error": "コミュニティのメンバーのみ"}), 403
    purpose_ref = (body.get("purpose_ref") or "").strip()
    title = (body.get("title") or "").strip()
    if not purpose_ref or not title:
        return jsonify({"error": "purpose_ref と title が必要です"}), 400
    return jsonify(talks.launch_project(community_id, sid, title, purpose_ref, db_path=DB)), 201


@app.post("/api/projects/<intent_id>/join-as-community")
@login_required
def api_join_as_community(intent_id):
    """コミュニティとしてプロジェクトに参加する（§6）。起票者はそのコミュニティのメンバー。"""
    import talks
    body = request.get_json(force=True, silent=True) or {}
    sid = current_subject_id()
    community_id = (body.get("community_id") or "").strip()
    consent_ref = (body.get("consent_ref") or "").strip()
    if not community_id or not consent_ref:
        return jsonify({"error": "community_id と consent_ref が必要です"}), 400
    if not _is_ctx_member(community_id, sid):
        return jsonify({"error": "参加するコミュニティのメンバーのみ起票できます"}), 403
    try:
        r = talks.open_community_join(intent_id, community_id, consent_ref, opener=sid, db_path=DB)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(r), 201


@app.get("/api/projects/<intent_id>/join-options")
@login_required
def api_project_join_options(intent_id):
    """コミュニティとして参加する際の選択肢（指示書47 §4）。ログイン中の主体が
    メンバーであるコミュニティを列挙し、各コミュニティが当該 intent への参加を合意済みなら
    その purpose.agreed の event_hash を consent_ref として返す（無ければ null＝先に合意が必要）。"""
    from community import get_all_communities, is_member, is_founder
    import ledger_events as le
    sid = current_subject_id()
    out = []
    for c in get_all_communities(db_path=DB):
        cid = c["id"]
        if not (is_member(cid, sid, db_path=DB) or has_founder_rights(cid, sid, db_path=DB)):
            continue
        consent = None
        for e in le.get_events(type_="purpose.agreed", db_path=DB):
            p = e["payload"]
            if p.get("ctx") == cid and p.get("target_intent_id") == intent_id:
                consent = e["event_hash"]
        out.append({"community_id": cid, "name": c["name"], "consent_ref": consent})
    return jsonify({"intent_id": intent_id, "communities": out})


@app.get("/api/talks/<talk_id>")
def api_get_talk(talk_id):
    """トーク詳細。提議・プロジェクトのトークは公開。チャットと加入の審議はメンバーのみ
    （指示書43 T-8・44 キャプション確定）。"""
    import talks
    talk = talks.get_talk(talk_id, db_path=DB)
    if talk is None:
        return jsonify({"error": "not_found"}), 404
    if talk["kind"] in (talks.CHAT, talks.ADMISSION):
        _mark_noindex()                                  # メンバー限定面は索引させない（45 A-3）
        if not _is_ctx_member(talk["ctx"], current_subject_id()):
            return jsonify({"error": "not_found"}), 404  # 第三者には存在ごと見せない（人に付く棄却）
    return jsonify(_talk_public_view(talk)), 200


@app.get("/api/community/<community_id>/talks")
def api_list_talks(community_id):
    """トーク一覧。提議・プロジェクトは公開・名前付き。チャットと加入の審議はメンバーのみ
    （指示書43 T-8・44）。各行に表示用の状態語彙と起票者の表示名を添える。"""
    import talks
    is_mem = _is_ctx_member(community_id, current_subject_id())
    if is_mem:
        _mark_noindex()              # 加入の審議・チャットを含む一覧は索引させない（45 A-3）
    ctx_names = _resolve_names([t["created_by"] for t in talks.list_talks(community_id, db_path=DB)],
                               fallback=UNNAMED_LABEL)
    rows = []
    for t in talks.list_talks(community_id, db_path=DB):
        if t["kind"] in (talks.CHAT, talks.ADMISSION) and not is_mem:
            continue                                     # 加入の審議・チャットは第三者に出さない
        row = {**t, "display_status": talks.display_status(t, db_path=DB),
               "closed": talks.is_closed(t, db_path=DB),
               "created_by_name": ctx_names.get(t["created_by"], t["created_by"])}
        # ルート一覧の3区分と、出自・子の導出（指示書43 §1-3・§2-2 / 42 §2）
        if t["kind"] == talks.PROJECT:
            row["section"] = "project"
            row["origin"] = talks.origin_of(t, db_path=DB)          # 出自: 提議「…」（常時1行表示）
        elif t["kind"] == talks.PROPOSAL:
            if t["status"] == "agreed":
                row["section"] = "proposal_agreed"                  # 下部の折りたたみに残す（削除しない）
                row["child_project"] = talks.child_project_of(t, db_path=DB)
            else:
                row["section"] = "proposal_live"                    # 審議中・休眠
        elif t["kind"] == talks.ADMISSION:
            row["section"] = "admission"
        elif t["kind"] == talks.CHAT:
            row["section"] = "chat"
        else:
            row["section"] = "other"                                # project_join / project_complete
        rows.append(row)
    return jsonify({"ctx": community_id, "talks": rows}), 200


@app.post("/api/talks/<talk_id>/posts")
@login_required
def api_talk_post(talk_id):
    """トークへの投稿（経緯）。チャットはメンバー、公開トークは当事者が書ける。"""
    import talks
    talk = talks.get_talk(talk_id, db_path=DB)
    if talk is None:
        return jsonify({"error": "not_found"}), 404
    sid = current_subject_id()
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("body") or "").strip()
    if not text:
        return jsonify({"error": "body が必要です"}), 400
    import redaction
    if text == redaction.REDACTED_BODY:
        # 伏せ字の定数は削除の記録にだけ使う（投稿に使えると削除済みと区別できない・すり合わせ §3-6）
        return jsonify({"error": f"「{redaction.REDACTED_BODY}」だけの発言は投稿できません"}), 400
    if not _can_participate_talk(talk, sid):
        return jsonify({"error": "このトークに投稿する権限がありません"}), 403
    # closure 済みトークへの追記は拒否（指示書43 §1-4・44 §2）。休眠・審議中は拒否しない。
    if talks.is_closed(talk, db_path=DB):
        return jsonify({"error": "closed",
                        "detail": "このトークは合意/完了により締め切られています。追記できません。"}), 409
    return jsonify(talks.add_post(talk_id, sid, text, db_path=DB)), 201


@app.post("/api/talks/<talk_id>/vote")
@login_required
def api_talk_vote(talk_id):
    """賛成・反対を明示する（沈黙は棄権・§5-1）。投票のたびに合意判定→成立なら台帳へ。"""
    import talks
    talk = talks.get_talk(talk_id, db_path=DB)
    if talk is None:
        return jsonify({"error": "not_found"}), 404
    if talk["kind"] not in talks.DECISION_KINDS:
        return jsonify({"error": "このトークは投票を受け付けません"}), 400
    sid = current_subject_id()
    if not _can_participate_talk(talk, sid):
        return jsonify({"error": "このトークで投票する権限がありません"}), 403
    if _is_join_offerer_only(talk, sid):
        return jsonify({"error": "申し出た本人は分母に含まれないため、票を投じられません"}), 403
    # closure 済みトークへの投票は拒否（closure の意味を保つ・指示書44 §2）。
    if talks.is_closed(talk, db_path=DB):
        return jsonify({"error": "closed",
                        "detail": "このトークは締め切られています。投票できません。"}), 409
    stance = (request.get_json(force=True, silent=True) or {}).get("stance")
    if stance not in ("approve", "dissent"):
        return jsonify({"error": "stance は approve か dissent"}), 400
    talks.set_vote(talk_id, sid, stance, db_path=DB)
    commit = talks.try_commit(talk_id, db_path=DB)
    return jsonify({"vote": {"voter": sid, "stance": stance}, "commit": commit,
                    "status": talks.get_talk(talk_id, db_path=DB)["status"]}), 200


@app.post("/api/talks/<talk_id>/decline")
@login_required
def api_talk_decline(talk_id):
    """加入の見送りを確定する（指示書45 A-1）。メンバーのみ。通常DBにのみ記録し台帳に書かない。
    反対が表明されていない審議中のトーク・決定済みのトークは確定できない（409）。"""
    import talks
    talk = talks.get_talk(talk_id, db_path=DB)
    if talk is None or talk["kind"] != talks.ADMISSION:
        return jsonify({"error": "not_found"}), 404
    _mark_noindex()
    sid = current_subject_id()
    if not _is_ctx_member(talk["ctx"], sid):
        return jsonify({"error": "not_found"}), 404          # 第三者には存在ごと見せない
    if talks.is_closed(talk, db_path=DB):
        return jsonify({"error": "closed", "detail": "この加入トークは決定済みです。"}), 409
    body = request.get_json(force=True, silent=True) or {}
    r = talks.decline_admission(talk_id, sid, summary=body.get("summary") or "", db_path=DB)
    if r is None:
        return jsonify({"error": "not_decidable",
                        "detail": "反対の表明が無いため、見送りを確定できません。"}), 409
    return jsonify(_talk_public_view(r)), 200


@app.get("/api/my/applications")
@login_required
def api_my_applications():
    """本人面（指示書45 A-2）: 自分のコミュニティ参加申請の状態（審議中／承認／見送り）と
    要約だけを返す。審議の内容（発言・票・誰が反対したか）は本人にも返さない。"""
    import talks
    from community import get_my_communities
    sid = current_subject_id()
    out = []
    for c in get_my_communities(sid, db_path=DB):
        o = talks.admission_outcome_for(c["id"], sid, db_path=DB)
        if o and not is_founder(c["id"], sid, db_path=DB):
            out.append({"community_id": c["id"], "community_name": c["name"], **o})
    return jsonify({"applications": out})


def _can_participate_talk(talk, sid):
    """トークの起票・投稿・投票ができる当事者か。"""
    import talks
    if not sid:
        return False
    kind = talk["kind"]
    # 提議・加入の審議・チャットの書き込みはメンバー（指示書48 §1-4 案A: 閲覧と書き込みを分ける）
    if kind in (talks.CHAT, talks.PROPOSAL, talks.ADMISSION):
        return _is_ctx_member(talk["ctx"], sid)
    # プロジェクト（実行トーク）の発言・達成提案は当事者（参加者）のみ（指示書48 §1-1 / 108-o）
    if kind == talks.PROJECT:
        return _is_participant(talk["target"].get("intent_id"), sid)
    if kind == talks.PROJECT_JOIN:
        return sid == talk["target"].get("participant") or _is_participant(talk["target"].get("intent_id"), sid)
    if kind == talks.PROJECT_COMPLETE:
        return _is_participant(talk["target"].get("intent_id"), sid)
    return False


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
    r = leave_community(community_id, member_id, db_path=DB)
    reasons = {
        "not_member": "このコミュニティのメンバーではありません（既に離脱済みの場合を含む）。",
        "last_member": "最後のメンバーは離脱できません（誰も何も合意できなくなるため）。",
        "founder_bootstrap": "コミュニティで最初のプロジェクトが完了するまでは、創設者は離脱できません。",
    }
    if r["status"] in reasons:
        return jsonify({"error": r["status"], "detail": reasons[r["status"]]}), 409
    return jsonify(r), 200


@app.post("/api/projects/<intent_id>/leave")
@login_required
def api_project_leave(intent_id):
    """プロジェクトからの離脱（指示書51）。本人のみ（除名は作らない）。個人の離脱だけを扱う
    （コミュニティとしての参加の離脱は未決・51 (b)）。発言は残り、当事者の集合から外れるだけ。
    実績・持ち分・将来の発行の権利は本人に帰属したまま（41 §7-1）。

      401 未ログイン／403 当事者でない／409 既に離脱済み・完了済み・最後の 1 人／404 不明なプロジェクト
    """
    import governance as gov
    import talks
    sid = current_subject_id()
    ctx = gov.intent_ctx(intent_id, db_path=DB)
    if ctx is None:
        return jsonify({"error": "not_found"}), 404
    if gov.has_left_project(intent_id, sid, db_path=DB):
        return jsonify({"error": "already_left", "detail": "このプロジェクトからは既に離脱しています。"}), 409
    if not _is_participant(intent_id, sid):
        return jsonify({"error": "このプロジェクトの当事者ではありません"}), 403
    proj = next((t for t in talks.list_talks(ctx, db_path=DB)
                 if t["kind"] == talks.PROJECT and (t["target"] or {}).get("intent_id") == intent_id), None)
    if proj is not None and talks.is_closed(proj, db_path=DB):
        return jsonify({"error": "completed",
                        "detail": "完了したプロジェクトからは離脱できません（離脱は実行中のみ）。"}), 409
    if gov.participants_at(intent_id, 10**18, db_path=DB) == {sid}:
        return jsonify({"error": "last_participant",
                        "detail": "最後の当事者は離脱できません（誰も何も合意できなくなるため）。"}), 409
    ref = gov.participation_ref(intent_id, sid, db_path=DB)
    ev = gov.publish_participant_left(intent_id, sid, participant_kind="individual",
                                      joined_ref=ref, db_path=DB)
    return jsonify({"intent_id": intent_id, "status": "left", "event_hash": ev["event_hash"],
                    "joined_ref": ref}), 200


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
    if not is_member(community_id, from_id, db_path=DB) and not has_founder_rights(community_id, from_id, db_path=DB):
        return jsonify({"error": "メンバーではありません"}), 403
    msg = send_message(from_id, community_id, msg_body, attachment_url=attachment_url, db_path=DB)
    return jsonify(msg), 201


@app.patch("/api/community/<community_id>")
@login_required
def api_community_update(community_id):
    """コミュニティ名・説明の編集。創設者の権限（在籍中のみ・指示書51 (c)）。
    requester_id はセッション本人と一致必須（なりすまし防止。以前はログイン不要で body の自己申告を信じていた）。"""
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "JSON が読めません"}), 400
    requester_id = require_self((body.get("requester_id") or "").strip() or None)
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
