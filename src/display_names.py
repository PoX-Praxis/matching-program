#!/usr/bin/env python3
"""
PoX 表示名レイヤー（指示書30）。

同一性の根拠は subject_id のみ。表示名は「通常DBの可変値」で、いつでも変更でき、
履歴は残さない（上書き）。台帳・content_hash・接続の三つ組には一切入れない。

設計上の制約:
  - 一意性は課さない（同一性は subject_id が担保する）。
  - 上限 30 字。空は許さず未設定（行を持たない）＝ resolver が subject_id にフォールバック。
  - redact_text を通す（メール等の PII 混入を防ぐ）。
  - 本人のみ変更可（呼び出し側で login_required + require_self）。

コミュニティ名は communities.name が既に「作成者が変更できる可変名」なので、ここには
**二重に持たない**（食い違い防止）。resolver（app 側）が subject が community なら
communities.name を、そうでなければ本テーブルを、無ければ subject_id を返す。
"""
from datetime import datetime, timezone

from db_connect import get_connection, is_postgres
from pii_redaction import redact_text

MAX_LEN = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str = "pox.db"):
    con = get_connection(db_path)
    if not is_postgres():
        con.execute(
            "CREATE TABLE IF NOT EXISTS display_names ("
            "subject_id TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        con.commit()
    return con


def _clean(name: str) -> str:
    """redact→trim→30字。空文字なら "" を返す（未設定＝フォールバック）。"""
    cleaned = redact_text(name or "").strip()
    if len(cleaned) > MAX_LEN:
        cleaned = cleaned[:MAX_LEN].strip()
    return cleaned


def set_display_name(subject_id: str, name: str, db_path: str = "pox.db"):
    """表示名を設定/変更（上書き・履歴なし）。空（redact 後含む）なら未設定にする。

    戻り値: 保存した表示名（空にした＝未設定なら None）。
    """
    cleaned = _clean(name)
    with _connect(db_path) as con:
        if not cleaned:
            con.execute("DELETE FROM display_names WHERE subject_id=%s", (subject_id,))
            return None
        con.execute(
            "INSERT INTO display_names (subject_id, name, updated_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (subject_id) DO UPDATE SET name=EXCLUDED.name, updated_at=EXCLUDED.updated_at",
            (subject_id, cleaned, _now()),
        )
    return cleaned


def get_display_name(subject_id: str, db_path: str = "pox.db"):
    """設定されていれば表示名、無ければ None。"""
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT name FROM display_names WHERE subject_id=%s", (subject_id,)
        ).fetchone()
    return row[0] if row else None


def get_many(subject_ids, db_path: str = "pox.db") -> dict:
    """複数 subject_id の表示名をまとめて返す（設定済みのみ）。{subject_id: name}。"""
    ids = [s for s in {s for s in subject_ids if s}]
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    with _connect(db_path) as con:
        rows = con.execute(
            f"SELECT subject_id, name FROM display_names WHERE subject_id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
    return {r[0]: r[1] for r in rows}
