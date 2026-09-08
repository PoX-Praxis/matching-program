#!/usr/bin/env python3
"""
PoX マジックリンク認証（指示書17 §3）。

台帳に書く subject_id の意味を保証するための最小認証。id をメールアドレスに
固める。トークンは平文で保存しない（token_hash のみ）。生アドレスは email_enc
として可逆保存し、参照用の一意キーは email_hash（キー付き SHA-256）。

暗号方式について（プロトタイプ・§9 で報告）:
  email_enc は POX_SECRET_KEY 由来の HMAC-SHA256 CTR キーストリームによる
  可逆暗号（stdlib のみ・機密性のみ／認証タグなし）。運営の連絡・移行のための
  復元用であり、本番強化時に AEAD（例: cryptography Fernet）へ差し替える前提。
"""
import os
import re
import hmac
import uuid
import base64
import hashlib
import secrets
from datetime import datetime, timezone, timedelta

from db_connect import get_connection, is_postgres

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _secret() -> str:
    return os.environ.get("POX_SECRET_KEY", "dev-insecure-key-change-me")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_valid_email(email: str) -> bool:
    return bool(email) and bool(_EMAIL_RE.match(email.strip()))


# ── メールアドレスのハッシュ / 可逆暗号 ─────────────────────────────

def email_hash(email: str) -> str:
    """キー付き SHA-256。UNIQUE 突合用の決定的ハッシュ。"""
    norm = (email or "").strip().lower().encode("utf-8")
    return hmac.new(_secret().encode("utf-8"), norm, hashlib.sha256).hexdigest()


def _keystream(nonce: bytes, length: int) -> bytes:
    key = _secret().encode("utf-8")
    out = b""
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return out[:length]


def encrypt_email(email: str) -> str:
    nonce = secrets.token_bytes(16)
    data = email.encode("utf-8")
    ks = _keystream(nonce, len(data))
    ct = bytes(a ^ b for a, b in zip(data, ks))
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_email(enc: str) -> str:
    raw = base64.b64decode(enc)
    nonce, ct = raw[:16], raw[16:]
    ks = _keystream(nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, ks)).decode("utf-8")


# ── テーブル（sqlite は遅延作成・Postgres は schema.init） ───────────

def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS auth_identities ("
            "subject_id TEXT PRIMARY KEY, email_hash TEXT NOT NULL UNIQUE, "
            "email_enc TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS auth_tokens ("
            "token_hash TEXT PRIMARY KEY, email_hash TEXT NOT NULL, "
            "email_enc TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT)"
        )
        con.commit()
    return con


# ── トークン（単回使用・15分） ────────────────────────────────────

def issue_token(email: str, db_path: str = "pox.db", ttl_minutes: int = 15) -> str:
    """署名付き単回トークンを発行し、その平文を返す（リンク用・保存はしない）。"""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO auth_tokens (token_hash, email_hash, email_enc, expires_at, used_at) "
            "VALUES (%s, %s, %s, %s, NULL)",
            (token_hash, email_hash(email), encrypt_email(email), expires),
        )
    return token


def consume_token(token: str, db_path: str = "pox.db"):
    """検証＋単回消費。成功なら (email_hash, email) を、失敗なら None を返す。"""
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = _now()
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT email_hash, email_enc, expires_at, used_at FROM auth_tokens "
            "WHERE token_hash = %s", (token_hash,),
        ).fetchone()
        if not row:
            return None
        eh, email_enc, expires_at, used_at = row
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
    return (eh, decrypt_email(email_enc))


# ── 同一性 ───────────────────────────────────────────────────────

def get_or_create_identity(email: str, db_path: str = "pox.db"):
    """(subject_id, created: bool) を返す。email_hash で既存を突合、無ければ新規発行。"""
    eh = email_hash(email)
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT subject_id FROM auth_identities WHERE email_hash = %s", (eh,)
        ).fetchone()
        if row:
            return row[0], False
        subject_id = f"u_{uuid.uuid4().hex[:8]}"
        con.execute(
            "INSERT INTO auth_identities (subject_id, email_hash, email_enc, created_at) "
            "VALUES (%s, %s, %s, %s)",
            (subject_id, eh, encrypt_email(email), _now()),
        )
    return subject_id, True
