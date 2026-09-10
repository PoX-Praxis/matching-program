#!/usr/bin/env python3
"""
PoX メール送信（指示書17 §3-1 / 指示書26）。

バックエンドを POX_MAIL_BACKEND で切り替える:
  - "resend_api"（既定）… Resend の HTTP API（POST https://api.resend.com/emails）。
      Render がアウトバウンド SMTP（25/587 等）を塞いでおり TCP 接続が TimeoutError に
      なるため、HTTPS(443) で送れる HTTP API を既定にした（経緯は docs 参照）。
  - "smtp" … 現行の SMTP 実装（将来の別サービス用に残す）。ポートで SSL/STARTTLS 自動切替。

いずれのバックエンドも「設定が揃っていなければ開発モード」に落ちる。開発モードは送らず、
POX_DEBUG=1 のときだけリンクを標準出力に出す（本番の開発モードは設定漏れ＝異常）。

ログ出力の原則（指示書26 §5・§8）:
  - **メールアドレスの平文をログに書かない**（識別はハッシュ先頭数文字 addr:xxxxxxxx）。
  - **有効なログインリンクをログに書かない**（POX_DEBUG=1 のときだけ出す）。
  - 送信失敗は握りつぶさずログに残すが、上位には例外を投げない（呼び出し側は
    列挙攻撃対策で常に {"sent": true} を返すため）。API 失敗時は status code と
    エラー本文を残す（アドレスは本文からも除去する）。

環境変数:
  POX_MAIL_BACKEND    "resend_api"（既定）/ "smtp"
  POX_RESEND_API_KEY  Resend の API キー（re_...）           … resend_api で必須
  POX_MAIL_FROM       差出人（例 noreply@pox-praxis.com）      … resend_api で必須
  POX_SMTP_HOST / POX_SMTP_USER / POX_SMTP_PASS               … smtp で必須3点
  POX_SMTP_PORT（既定 587。465/2465 は暗黙TLS）/ POX_SMTP_FROM（既定は USER）
"""
import os
import ssl
import json
import hashlib
import smtplib
import urllib.request
import urllib.error
from email.message import EmailMessage

_RESEND_ENDPOINT = "https://api.resend.com/emails"


def _backend() -> str:
    return (os.environ.get("POX_MAIL_BACKEND") or "resend_api").strip().lower()


def _smtp_configured() -> bool:
    return all(os.environ.get(k) for k in ("POX_SMTP_HOST", "POX_SMTP_USER", "POX_SMTP_PASS"))


def _resend_configured() -> bool:
    return bool(os.environ.get("POX_RESEND_API_KEY") and os.environ.get("POX_MAIL_FROM"))


def is_configured() -> bool:
    """現在のバックエンドで送信できる設定が揃っているか（起動時警告・app.py が使う）。"""
    return _smtp_configured() if _backend() == "smtp" else _resend_configured()


def _debug() -> bool:
    return os.environ.get("POX_DEBUG", "0") == "1"


def _addr_id(email: str) -> str:
    """ログ用の非可逆識別子（アドレス平文は出さない・§5-2）。"""
    return "addr:" + hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()[:8]


def _scrub(text: str, email: str) -> str:
    """ログに出す外部文字列から、万一含まれうるアドレスを除去する。"""
    if not text:
        return ""
    out = text
    for form in {email, (email or "").strip(), (email or "").strip().lower()}:
        if form:
            out = out.replace(form, "<addr>")
    return out


_SUBJECT = "PoX ログインリンク"


def _body(link: str) -> str:
    return (
        "以下のリンクを開くとログインできます（15分間有効・一度きり）。\n\n"
        f"{link}\n\n"
        "心当たりがない場合は、このメールは無視してください。"
    )


def send_magic_link(to_email: str, link: str) -> dict:
    """マジックリンクを送る。戻り値 {sent, dev, dev_link?, error?}。例外は上に投げない。"""
    backend = _backend()

    if not is_configured():
        # 開発モード: 送らない。
        if _debug():
            # 開発時のみ、送信設定前に自分でログインできるようリンクを出す。
            print(f"[mailer:dev] backend={backend} {_addr_id(to_email)} link={link}")
        else:
            # 本番でここに来るのは設定漏れ＝異常。アドレスもリンクも出さない。
            need = ("POX_SMTP_HOST/USER/PASS" if backend == "smtp"
                    else "POX_RESEND_API_KEY / POX_MAIL_FROM")
            print(f"[mailer] WARNING: メール未送信（backend={backend} 未設定）。"
                  f"本番では異常。{need} を設定してください。")
        return {"sent": False, "dev": True, "dev_link": link}

    if backend == "smtp":
        return _send_smtp(to_email, _SUBJECT, _body(link))
    return _send_resend_api(to_email, _SUBJECT, _body(link))


# ── Resend HTTP API（既定・urllib）────────────────────────────────

def _send_resend_api(to_email: str, subject: str, body: str) -> dict:
    payload = json.dumps({
        "from": os.environ["POX_MAIL_FROM"],
        "to": [to_email],
        "subject": subject,
        "text": body,
    }).encode("utf-8")
    req = urllib.request.Request(
        _RESEND_ENDPOINT, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['POX_RESEND_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()  # 応答本文は読み捨て（2xx=成功）。
        return {"sent": True, "dev": False}
    except urllib.error.HTTPError as e:
        # Resend がエラー status を返した。status と本文を残す（アドレスは除去）。
        try:
            err_body = _scrub(e.read().decode("utf-8", "replace")[:500], to_email)
        except Exception:  # noqa: BLE001
            err_body = ""
        print(f"[mailer] ERROR: Resend API 送信失敗 {_addr_id(to_email)} "
              f"status={e.code} body={err_body}")
        return {"sent": False, "dev": False, "error": f"http_{e.code}"}
    except Exception as e:  # noqa: BLE001
        # DNS/TLS/timeout など接続系。エンドポイントは HTTPS(443)。
        print(f"[mailer] ERROR: Resend API 接続失敗 {_addr_id(to_email)} "
              f"endpoint={_RESEND_ENDPOINT} type={type(e).__name__}: {_scrub(str(e), to_email)}")
        return {"sent": False, "dev": False, "error": type(e).__name__}


# ── SMTP（将来の別サービス用に維持）───────────────────────────────

def _send_smtp(to_email: str, subject: str, body: str) -> dict:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("POX_SMTP_FROM", os.environ["POX_SMTP_USER"])
    msg["To"] = to_email
    msg.set_content(body)

    host = os.environ["POX_SMTP_HOST"]
    port = int(os.environ.get("POX_SMTP_PORT", "587"))
    # 465/2465 は暗黙 TLS（SMTP_SSL・STARTTLS を呼ばない）。それ以外は STARTTLS で昇格。
    use_ssl = port in (465, 2465)
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=30,
                                  context=ssl.create_default_context()) as s:
                s.login(os.environ["POX_SMTP_USER"], os.environ["POX_SMTP_PASS"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(os.environ["POX_SMTP_USER"], os.environ["POX_SMTP_PASS"])
                s.send_message(msg)
    except Exception as e:  # noqa: BLE001
        mode = "SSL" if use_ssl else "STARTTLS"
        print(f"[mailer] ERROR: SMTP 送信失敗 {_addr_id(to_email)} host={host} port={port} "
              f"mode={mode} type={type(e).__name__}: {e}")
        return {"sent": False, "dev": False, "error": type(e).__name__}
    return {"sent": True, "dev": False}
