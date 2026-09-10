"""スキーマ収束の冪等移行（指示書26 の NotNullViolation 対処）。

CREATE TABLE IF NOT EXISTS は既存テーブルに DDL 変更を反映しない。schema.init() の
_migrate_columns が、既存テーブルの email_enc を落とし、後付け列を補完することを検証する。
SQLite でのふるまいを確認する（Postgres も同じ収束ロジックを通る）。
"""
import os, sys, tempfile, sqlite3
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import schema
import auth


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _cols(db, table):
    con = sqlite3.connect(db)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        con.close()


def test_drop_email_enc_and_issue_token_and_idempotent():
    db = _db()
    # 旧スキーマを再現: email_enc NOT NULL 付きの auth テーブル（本番の状態）。
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE auth_tokens (token_hash TEXT PRIMARY KEY, "
                "email_hash TEXT NOT NULL, email_enc TEXT NOT NULL, "
                "expires_at TEXT NOT NULL, used_at TEXT)")
    con.execute("CREATE TABLE auth_identities (subject_id TEXT PRIMARY KEY, "
                "email_hash TEXT NOT NULL UNIQUE, email_enc TEXT NOT NULL, "
                "created_at TEXT NOT NULL)")
    con.commit()
    con.close()
    assert "email_enc" in _cols(db, "auth_tokens")

    schema.init(db)   # CREATE IF NOT EXISTS（既存はそのまま）＋ _migrate_columns で email_enc を落とす

    # email_enc が両テーブルから消えている。
    assert "email_enc" not in _cols(db, "auth_tokens")
    assert "email_enc" not in _cols(db, "auth_identities")
    # email_hash は残っている。
    assert "email_hash" in _cols(db, "auth_tokens")

    # issue_token / consume_token が通る（NotNullViolation の再発防止）。
    tok = auth.issue_token("a@b.co", db_path=db)
    assert auth.consume_token(tok, db_path=db) == auth.email_hash("a@b.co")

    # 冪等: もう一度 init しても壊れない。
    schema.init(db)
    assert "email_enc" not in _cols(db, "auth_tokens")


def test_connection_requests_columns_backfilled():
    """schema DDL に無い後付け列（predicted_role/channel/match_run_id）が補完される。"""
    db = _db()
    schema.init(db)
    cols = _cols(db, "connection_requests")
    for c in ("predicted_role", "channel", "match_run_id"):
        assert c in cols


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nschema migration テスト: {len(tests)} 件 全 PASS")
