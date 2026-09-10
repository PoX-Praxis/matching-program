"""指示書17 段階1+2: マジックリンク認証 と 追記専用イベント台帳の基盤。

- canon: 正準化の決定性
- ledger_events: append_event の seq/prev_hash 連結・event_hash 再計算・verify_chain
- auth: email ハッシュ/可逆暗号・単回トークン・期限切れ・同一性の冪等
- app: 初回ログインで subject.created / terms.accepted が台帳に載る。再ログインで重複しない
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import canon
import ledger_events as le
import auth
import db as dbmod
import app as appmod


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


# ── canon ────────────────────────────────────────────────────────────────────
def test_canonicalize_is_key_order_independent():
    a = canon.canonicalize({"b": 1, "a": 2})
    b = canon.canonicalize({"a": 2, "b": 1})
    assert a == b == b'{"a":2,"b":1}'
    assert canon.sha256_hex(a) == canon.sha256_hex(b)


# ── ledger_events ────────────────────────────────────────────────────────────
def test_append_event_chains_and_verifies():
    db = _db()
    e1 = le.append_event("u1", "subject.created", {"subject_id": "u1", "kind": "individual"}, db_path=db)
    e2 = le.append_event("u1", "terms.accepted", {"subject_id": "u1", "terms_version": "2026-08"}, db_path=db)
    assert e1["seq"] == 1 and e1["prev_hash"] is None
    assert e2["seq"] == 2 and e2["prev_hash"] == e1["event_hash"]
    v = le.verify_chain(db_path=db)
    assert v["ok"] is True and v["count"] == 2


def test_event_hash_recomputes_from_stored_payload():
    db = _db()
    r = le.append_event("sys", "anchor.published", {"root": "abc", "date": "2026-08-07"}, db_path=db)
    ev = le.get_events(db_path=db)[0]
    body = {"seq": ev["seq"], "at": ev["at"], "actor": ev["actor"], "type": ev["type"],
            "prev_hash": ev["prev_hash"], "canon_version": ev["canon_version"], "payload": ev["payload"]}
    assert canon.sha256_hex(canon.canonicalize(body)) == r["event_hash"]


def test_next_n_counts_per_owner():
    db = _db()
    le.append_event("u1", "necessity.published", {"owner_ref": "u1", "n": 1}, db_path=db)
    le.append_event("u2", "necessity.published", {"owner_ref": "u2", "n": 1}, db_path=db)
    assert le.next_n("owner_ref", "u1", "necessity.published", db_path=db) == 2
    assert le.next_n("owner_ref", "u3", "necessity.published", db_path=db) == 1


# ── auth ─────────────────────────────────────────────────────────────────────
def test_email_hash_deterministic_and_normalized():
    assert auth.email_hash("A@B.com") == auth.email_hash("a@b.com")   # 大文字小文字正規化
    assert auth.email_hash("a@b.com") != auth.email_hash("x@y.com")   # 別アドレスは別ハッシュ
    assert len(auth.email_hash("a@b.com")) == 64                      # SHA-256 hex


def test_email_salt_is_independent_of_secret_key(monkeypatch):
    """指示書26 §1-3: email_hash のソルトは POX_EMAIL_SALT。POX_SECRET_KEY を
    変えても email_hash は不変（＝鍵漏洩で POX_SECRET_KEY を差し替えてもアカウント喪失しない）。"""
    monkeypatch.setenv("POX_EMAIL_SALT", "salt-A")
    monkeypatch.setenv("POX_SECRET_KEY", "secret-1")
    h1 = auth.email_hash("kaoru@example.com")
    monkeypatch.setenv("POX_SECRET_KEY", "secret-2")   # セッション鍵だけ差し替え
    h2 = auth.email_hash("kaoru@example.com")
    assert h1 == h2                                     # email_hash は不変
    monkeypatch.setenv("POX_EMAIL_SALT", "salt-B")      # ソルトを変えると別ハッシュ
    assert auth.email_hash("kaoru@example.com") != h1


def test_token_single_use_and_expiry():
    db = _db()
    tok = auth.issue_token("a@b.co", db_path=db)
    first = auth.consume_token(tok, db_path=db)
    assert first == auth.email_hash("a@b.co")                    # email_hash を返す（平文は保持しない）
    assert auth.consume_token(tok, db_path=db) is None          # 単回使用
    expired = auth.issue_token("c@d.co", db_path=db, ttl_minutes=-1)
    assert auth.consume_token(expired, db_path=db) is None      # 期限切れ
    assert auth.consume_token("not-a-real-token", db_path=db) is None


def test_identity_idempotent():
    db = _db()
    sid1, created1 = auth.get_or_create_identity("x@y.co", db_path=db)
    sid2, created2 = auth.get_or_create_identity("X@Y.CO", db_path=db)
    assert created1 is True and created2 is False and sid1 == sid2


# ── app: 初回ログインで台帳に載る ──────────────────────────────────────────────
def test_first_login_writes_subject_and_terms_once():
    db = _db()
    appmod.DB = db
    c = appmod.app.test_client()

    tok = auth.issue_token("kaoru@example.com", db_path=db)
    r = c.get(f"/auth/verify?token={tok}", follow_redirects=False)
    assert r.status_code == 302 and "/mypage" in r.headers["Location"]

    types = [e["type"] for e in le.get_events(db_path=db)]
    assert types == ["subject.created", "terms.accepted"]
    terms = le.get_events(type_="terms.accepted", db_path=db)[0]
    assert terms["payload"]["terms_version"] == appmod.TERMS_VERSION
    assert len(terms["payload"]["terms_hash"]) == 64        # SHA-256 hex

    # 2回目のログイン（同じメール）は identity 既存 → 台帳に重複を作らない
    tok2 = auth.issue_token("kaoru@example.com", db_path=db)
    c.get(f"/auth/verify?token={tok2}", follow_redirects=False)
    assert [e["type"] for e in le.get_events(db_path=db)] == ["subject.created", "terms.accepted"]
    assert le.verify_chain(db_path=db)["ok"] is True


def test_relogin_same_address_returns_same_subject_and_profile():
    """指示書26 §2: ログアウト後に同一アドレスで再ログイン → 同じ subject_id が返り、
    その id に紐づくプロフィールが引き継がれている。"""
    db = _db()
    appmod.DB = db
    c = appmod.app.test_client()

    # 1) アドレス A で初回ログイン → subject_id を取得
    tok1 = auth.issue_token("kaoru@example.com", db_path=db)
    r1 = c.get(f"/auth/verify?token={tok1}", follow_redirects=False)
    assert r1.status_code == 302
    sid1 = r1.headers["Location"].split("id=")[-1]
    assert sid1.startswith("u_")

    # 2) その subject_id で本人のプロフィールを登録
    dbmod.save_profile(sid1, {"意志": "再ログイン検証", "現状": {}}, db_path=db)

    # 3) ログアウト
    c.post("/auth/logout")

    # 4) 同じアドレス A で再ログイン → 同じ subject_id に戻る（新規発行しない）
    tok2 = auth.issue_token("kaoru@example.com", db_path=db)
    r2 = c.get(f"/auth/verify?token={tok2}", follow_redirects=False)
    assert r2.status_code == 302
    sid2 = r2.headers["Location"].split("id=")[-1]
    assert sid2 == sid1

    # 5) プロフィールが引き継がれている
    assert dbmod.get_profile_view(sid2, db_path=db) is not None


def test_auth_request_rejects_bad_email():
    db = _db()
    appmod.DB = db
    c = appmod.app.test_client()
    assert c.post("/auth/request", json={"email": "nope"}).status_code == 400


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nauth+ledger テスト: {len(tests)} 件 全 PASS")
