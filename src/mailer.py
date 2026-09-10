#!/usr/bin/env python3
"""
PoX メール送信（指示書17 §3-1 / 指示書26 §4・§5）。

SMTP 環境変数が揃っていれば送信、無ければ開発モードとしてリンクを扱う。
本番では POX_SMTP_* を設定すること。

ログ出力の原則（指示書26 §5・§8）:
  - **メールアドレスの平文をログに書かない**（識別はハッシュ先頭数文字）。
  - **有効なログインリンクをログに書かない**。踏めば 15 分間そのアカウントに入れるため、
    本番のログに残すのは重大なリスク。リンクを出すのは POX_DEBUG=1 のときだけ
    （SMTP 設定前に自分でログインする手段として）。
  - 本番（POX_DEBUG!=1）で開発モードに入っているのは設定漏れ＝異常。その旨だけ記録する。

接続方式はポートで自動切替する:
  - 465 / 2465 … 暗黙 TLS（SMTP_SSL・接続直後から暗号化。STARTTLS は呼ばない）
  - 25 / 587 / 2587 … 平文接続後に STARTTLS で昇格
timeout は 30 秒。接続失敗時は host / port / mode をログに残す（経路切り分け用）。

環境変数:
  POX_SMTP_HOST / POX_SMTP_USER / POX_SMTP_PASS （必須3点）
  POX_SMTP_PORT （既定 587＝STARTTLS。465/2465 なら暗黙TLS） / POX_SMTP_FROM （既定は USER）
"""
import os
import ssl
import hashlib
import smtplib
from email.message import EmailMessage


def _smtp_configured() -> bool:
    return all(os.environ.get(k) for k in ("POX_SMTP_HOST", "POX_SMTP_USER", "POX_SMTP_PASS"))


def _debug() -> bool:
    return os.environ.get("POX_DEBUG", "0") == "1"


def _addr_id(email: str) -> str:
    """ログ用の非可逆識別子（アドレス平文は出さない・§5-2）。"""
    return "addr:" + hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()[:8]


def send_magic_link(to_email: str, link: str) -> dict:
    """マジックリンクを送る。戻り値 {sent, dev, dev_link?, error?}。

    送信失敗でも例外を上に投げない（呼び出し側は列挙攻撃対策で常に成功表示のため・§5-2）。
    失敗はここでログに残す（アドレス平文・リンクは出さない）。
    """
    subject = "PoX ログインリンク"
    body = (
        "以下のリンクを開くとログインできます（15分間有効・一度きり）。\n\n"
        f"{link}\n\n"
        "心当たりがない場合は、このメールは無視してください。"
    )

    if not _smtp_configured():
        # 開発モード: 送らない。
        if _debug():
            # 開発時のみ、SMTP 設定前に自分でログインできるようリンクを出す。
            print(f"[mailer:dev] {_addr_id(to_email)} link={link}")
        else:
            # 本番でここに来るのは設定漏れ＝異常。アドレスもリンクも出さない。
            print("[mailer] WARNING: SMTP 未設定のため送信していません"
                  "（本番では異常。POX_SMTP_HOST/USER/PASS を設定してください）")
        return {"sent": False, "dev": True, "dev_link": link}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("POX_SMTP_FROM", os.environ["POX_SMTP_USER"])
    msg["To"] = to_email
    msg.set_content(body)

    host = os.environ["POX_SMTP_HOST"]
    port = int(os.environ.get("POX_SMTP_PORT", "587"))
    # 465 / 2465 は暗黙 TLS（接続直後から SSL）＝ SMTP_SSL を使い STARTTLS は呼ばない。
    # それ以外（25 / 587 / 2587）は平文接続後に STARTTLS で昇格する。
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
        # 500 を返さない（利用者には成功に見せる＝列挙攻撃対策）。失敗はログに残す。
        # アドレス平文は書かない（識別はハッシュ先頭）。host:port を含め、経路の切り分けを可能にする。
        mode = "SSL" if use_ssl else "STARTTLS"
        print(f"[mailer] ERROR: 送信失敗 {_addr_id(to_email)} host={host} port={port} "
              f"mode={mode} type={type(e).__name__}: {e}")
        return {"sent": False, "dev": False, "error": type(e).__name__}
    return {"sent": True, "dev": False}
