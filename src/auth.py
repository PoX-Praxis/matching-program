#!/usr/bin/env python3
"""
PoX マジックリンク認証（指示書17 §3 / 指示書26）。

台帳に書く subject_id の意味を保証するための最小認証。id をメールアドレスに
固める。トークンは平文で保存しない（token_hash のみ）。

同一性の唯一の根拠は email_hash（キー付き SHA-256）である。
  - ソルトは **POX_EMAIL_SALT**。POX_SECRET_KEY とは独立させる（指示書26 §1-3）。
    POX_SECRET_KEY は漏洩時に差し替えてよい（副作用はセッション失効のみ）が、
    POX_EMAIL_SALT を差し替えると全 email_hash が変わり全アカウントが到達不能に
    なる。両者を同じ鍵にすると「漏洩時に差し替え」で全アカウントを失う。だから分離する。
  - POX_EMAIL_SALT 未設定時、本番（POX_DEBUG!=1）では既定値へフォールバックせず
    例外を送出する。既定値で起動してしまうと、後から正しい値を入れた瞬間に全
    アカウントを失うため（§1-3・警告では足りない）。

メールアドレスの平文・可逆暗号（旧 email_enc）は保持しない（指示書26 §3）。
アドレスの用途はマジックリンクの送信先だけで、送信先は入力値そのもの。ログイン時は
入力アドレスを email_hash で照合すれば足り、保存値を復号する必要がない。将来「運営から
利用者へ連絡する」機能を足すなら、そのとき AEAD で設計し直す。
"""
import os
import re
import hmac
import uuid
import hashlib
import secrets
from datetime import datetime, timezone, timedelta

from db_connect import get_connection, is_postgres

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 開発時のみ許す既定ソルト（本番では使わせない・下記 _email_salt 参照）。
_DEV_SALT = "dev-insecure-email-salt-change-me"


def _debug() -> bool:
    return os.environ.get("POX_DEBUG", "0") == "1"


def _email_salt() -> str:
    """email_hash のソルト（POX_EMAIL_SALT）。本番で未設定なら起動を止める。

    既定値へのフォールバックを本番で許すと、後から正しい値を設定した瞬間に
    全 email_hash が変わりアカウントが全喪失する。だから開発（POX_DEBUG=1）
    以外では例外にする（指示書26 §1-3）。
    """
    salt = os.environ.get("POX_EMAIL_SALT")
    if salt:
        return salt
    if _debug():
        return _DEV_SALT
    raise RuntimeError(
        "POX_EMAIL_SALT が未設定です。これは email_hash のソルトであり、"
        "既定値での起動は全アカウント喪失につながるため本番では許可されません。"
        "（開発時のみ POX_DEBUG=1 で既定ソルトにフォールバックします）"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_valid_email(email: str) -> bool:
    return bool(email) and bool(_EMAIL_RE.match(email.strip()))


# ── メールアドレスのハッシュ（UNIQUE 突合用の決定的ハッシュ）─────────────

def email_hash(email: str) -> str:
    """キー付き SHA-256。ソルトは POX_EMAIL_SALT（POX_SECRET_KEY とは独立）。"""
    norm = (email or "").strip().lower().encode("utf-8")
    return hmac.new(_email_salt().encode("utf-8"), norm, hashlib.sha256).hexdigest()


# ── テーブル（sqlite は遅延作成・Postgres は schema.init） ───────────

def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS auth_identities ("
            "subject_id TEXT PRIMARY KEY, email_hash TEXT NOT NULL UNIQUE, "
            "created_at TEXT NOT NULL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS auth_tokens ("
            "token_hash TEXT PRIMARY KEY, email_hash TEXT NOT NULL, "
            "expires_at TEXT NOT NULL, used_at TEXT)"
        )
        con.commit()
    return con


# ── トークン（単回使用・15分） ────────────────────────────────────

def issue_token(email: str, db_path: str = "pox.db", ttl_minutes: int = 15) -> str:
    """署名付き単回トークンを発行し、その平文を返す（リンク用・保存はしない）。

    保存するのは token_hash と email_hash のみ。アドレスの平文/暗号は保存しない。
    """
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO auth_tokens (token_hash, email_hash, expires_at, used_at) "
            "VALUES (%s, %s, %s, NULL)",
            (token_hash, email_hash(email), expires),
        )
    return token


def consume_token(token: str, db_path: str = "pox.db"):
    """検証＋単回消費。成功なら email_hash を、失敗なら None を返す。

    アドレスの平文は保持していないので email_hash のみを返す。identity の
    採番・照合は email_hash だけで完結する（get_or_create_identity_by_hash）。
    """
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = _now()
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT email_hash, expires_at, used_at FROM auth_tokens "
            "WHERE token_hash = %s", (token_hash,),
        ).fetchone()
        if not row:
            return None
        eh, expires_at, used_at = row
        if used_at is not None:
            return None
        if expires_at < now:            # ISO 8601 UTC 同形式の辞書順＝時系列順
            return None
        res = con.execute(
            "UPDATE auth_tokens SET used_at = %s WHERE token_hash = %s AND used_at IS NULL",
            (now, token_hash),
        )
        if getattr(res, "rowcount", 1) == 0:   # 競合時の単回保証
            return None
    return eh


# ── 同一性 ───────────────────────────────────────────────────────

def get_or_create_identity_by_hash(eh: str, db_path: str = "pox.db"):
    """(subject_id, created: bool) を返す。email_hash で既存を突合、無ければ新規発行。

    再ログインの担保: 同じアドレス→同じ email_hash→既存 subject_id を返す（§2）。
    """
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT subject_id FROM auth_identities WHERE email_hash = %s", (eh,)
        ).fetchone()
        if row:
            return row[0], False
        subject_id = f"u_{uuid.uuid4().hex[:8]}"
        con.execute(
            "INSERT INTO auth_identities (subject_id, email_hash, created_at) "
            "VALUES (%s, %s, %s)",
            (subject_id, eh, _now()),
        )
    return subject_id, True


def get_or_create_identity(email: str, db_path: str = "pox.db"):
    """アドレスから email_hash を計算して同一性を得る薄いラッパー。"""
    return get_or_create_identity_by_hash(email_hash(email), db_path=db_path)
