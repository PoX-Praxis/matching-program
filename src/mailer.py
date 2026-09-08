#!/usr/bin/env python3
"""
PoX メール送信（指示書17 §3-1）。

SMTP 環境変数が揃っていれば送信、無ければ開発モードとしてリンクをログに出す。
本番では POX_SMTP_* を設定すること。開発モードのリンクを HTTP レスポンスに
返すかは呼び出し側が POX_DEBUG で制御する（本番では返さない）。

環境変数:
  POX_SMTP_HOST / POX_SMTP_USER / POX_SMTP_PASS （必須3点）
  POX_SMTP_PORT （既定 587・STARTTLS） / POX_SMTP_FROM （既定は USER）
"""
import os
import ssl
import smtplib
from email.message import EmailMessage


def _smtp_configured() -> bool:
    return all(os.environ.get(k) for k in ("POX_SMTP_HOST", "POX_SMTP_USER", "POX_SMTP_PASS"))


def send_magic_link(to_email: str, link: str) -> dict:
    """マジックリンクを送る。戻り値 {sent, dev, dev_link?}。"""
    subject = "PoX ログインリンク"
    body = (
        "以下のリンクを開くとログインできます（15分間有効・一度きり）。\n\n"
        f"{link}\n\n"
        "心当たりがない場合は、このメールは無視してください。"
    )
    if not _smtp_configured():
        # 開発モード: 送らずにリンクをログへ。
        print(f"[mailer:dev] to={to_email} link={link}")
        return {"sent": False, "dev": True, "dev_link": link}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("POX_SMTP_FROM", os.environ["POX_SMTP_USER"])
    msg["To"] = to_email
    msg.set_content(body)

    host = os.environ["POX_SMTP_HOST"]
    port = int(os.environ.get("POX_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=15) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(os.environ["POX_SMTP_USER"], os.environ["POX_SMTP_PASS"])
        s.send_message(msg)
    return {"sent": True, "dev": False}
