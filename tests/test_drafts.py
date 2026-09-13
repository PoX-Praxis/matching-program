"""指示書28 段階1: 宣言の下書きと確定動線。

- drafts ストア: attempt_n の数え方（貼り直しで +1・確定後は新規で 1・拒否をまたいで持続）。
- エンドポイント: 本人限定（未ログイン→401・他人→403）、確定は PG 必須（SQLite→503）。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import drafts
import auth
import app as appmod


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _login(c, email, db):
    tok = auth.issue_token(email, db_path=db)
    r = c.get(f"/auth/verify?token={tok}", follow_redirects=False)
    return r.headers["Location"].split("id=")[-1]


# ── ストア: attempt_n の数え方（§9-3）─────────────────────────────────────────
def test_attempt_n_increments_on_repaste():
    db = _db()
    d1 = drafts.save_draft("u1", {"will_text": "A"}, db_path=db)
    assert d1["attempt_n"] == 1 and d1["status"] == "editing"
    d2 = drafts.save_draft("u1", {"will_text": "A2"}, db_path=db)
    assert d2["attempt_n"] == 2 and d2["draft_id"] == d1["draft_id"]  # 同じ下書きに加算
    assert d2["payload"]["will_text"] == "A2"                          # 内容は最新で上書き


def test_attempt_n_persists_across_rejection():
    db = _db()
    d1 = drafts.save_draft("u1", {"will_text": "A"}, db_path=db)
    drafts.add_rejection(d1["draft_id"], "fact_error", "言っていない", db_path=db)
    d = drafts.get_draft(d1["draft_id"], db_path=db)
    assert d["status"] == "rejected" and len(d["rejections"]) == 1
    # 拒否後に貼り直す → 同じ下書きに attempt_n 加算（通算・§3-2）
    d2 = drafts.save_draft("u1", {"will_text": "A3"}, db_path=db)
    assert d2["attempt_n"] == 2 and d2["draft_id"] == d1["draft_id"]


def test_confirmed_starts_new_draft():
    db = _db()
    d1 = drafts.save_draft("u1", {"will_text": "A"}, db_path=db)
    drafts.set_status(d1["draft_id"], "confirmed", db_path=db)
    # 確定後の貼り直しは新しい下書き（attempt_n=1）から
    d2 = drafts.save_draft("u1", {"will_text": "B"}, db_path=db)
    assert d2["draft_id"] != d1["draft_id"] and d2["attempt_n"] == 1


def test_active_draft_and_delete():
    db = _db()
    d = drafts.save_draft("u1", {"will_text": "A"}, db_path=db)
    assert drafts.get_active_draft("u1", db_path=db)["draft_id"] == d["draft_id"]
    drafts.delete_draft(d["draft_id"], db_path=db)
    assert drafts.get_draft(d["draft_id"], db_path=db) is None
    assert drafts.get_active_draft("u1", db_path=db) is None


def test_reject_kind_validated():
    db = _db()
    d = drafts.save_draft("u1", {"will_text": "A"}, db_path=db)
    try:
        drafts.add_rejection(d["draft_id"], "bogus", db_path=db)
        assert False, "未知種別は弾くべき"
    except ValueError:
        pass


# ── エンドポイント認証 ─────────────────────────────────────────────────────────
def test_draft_endpoints_require_login():
    db = _db(); appmod.DB = db
    c = appmod.app.test_client()
    assert c.post("/v4/drafts", json={"will_text": "A"}).status_code == 401
    assert c.get("/v4/drafts/mine").status_code == 401
    assert c.post("/v4/drafts/draft_x/confirm").status_code == 401


def test_draft_save_and_repaste_via_http():
    db = _db(); appmod.DB = db
    c = appmod.app.test_client()
    _login(c, "a@example.com", db)
    r1 = c.post("/v4/drafts", json={"will_text": "地域の居場所をつくる"})
    assert r1.status_code == 201 and r1.get_json()["attempt_n"] == 1
    r2 = c.post("/v4/drafts", json={"will_text": "地域の居場所をつくる（改）"})
    assert r2.get_json()["attempt_n"] == 2                      # 貼り直しで加算


def test_draft_other_user_forbidden():
    db = _db(); appmod.DB = db
    c = appmod.app.test_client()
    _login(c, "a@example.com", db)
    did = c.post("/v4/drafts", json={"will_text": "A"}).get_json()["draft_id"]
    # 別ユーザーでログインし直す
    c2 = appmod.app.test_client()
    _login(c2, "b@example.com", db)
    assert c2.get(f"/v4/drafts/{did}").status_code == 403


def test_confirm_requires_postgres_after_auth():
    db = _db(); appmod.DB = db
    c = appmod.app.test_client()
    _login(c, "a@example.com", db)
    did = c.post("/v4/drafts", json={"will_text": "A"}).get_json()["draft_id"]
    # 認証は通過し、SQLite では 503（PG 必須）
    assert c.post(f"/v4/drafts/{did}/confirm").status_code == 503


# ── 段階4: 拒否理由（§5）──────────────────────────────────────────────────────
def test_gate_u_after_discomfort_rule():
    assert drafts.gate_u_after_discomfort(0.3, 0) == 0.3        # 件数0は不変
    assert drafts.gate_u_after_discomfort(0.3, 1) == 0.45       # +0.15
    assert drafts.gate_u_after_discomfort(0.3, 2) == 0.6        # +0.30
    assert drafts.gate_u_after_discomfort(0.9, 3) == 0.9        # 上限0.9
    assert drafts.gate_u_after_discomfort(None, 2) is None      # 非数値は不変


def test_reject_endpoint_records_and_reads():
    db = _db(); appmod.DB = db
    c = appmod.app.test_client()
    _login(c, "a@example.com", db)
    did = c.post("/v4/drafts", json={"will_text": "A"}).get_json()["draft_id"]
    # 事実誤認
    r = c.post(f"/v4/drafts/{did}/reject", json={"kind": "fact_error", "note": "言っていない"})
    assert r.status_code == 200 and r.get_json()["status"] == "rejected"
    # 違和感も追加
    c.post(f"/v4/drafts/{did}/reject", json={"kind": "discomfort"})
    d = c.get(f"/v4/drafts/{did}").get_json()
    kinds = [x["kind"] for x in d["rejections"]]
    assert kinds == ["fact_error", "discomfort"]
    # 不正な種別は 400
    assert c.post(f"/v4/drafts/{did}/reject", json={"kind": "bogus"}).status_code == 400


def test_reject_endpoint_other_user_forbidden():
    db = _db(); appmod.DB = db
    c = appmod.app.test_client(); _login(c, "a@example.com", db)
    did = c.post("/v4/drafts", json={"will_text": "A"}).get_json()["draft_id"]
    c2 = appmod.app.test_client(); _login(c2, "b@example.com", db)
    assert c2.post(f"/v4/drafts/{did}/reject", json={"kind": "fact_error"}).status_code == 403


# ── 段階5: fallback B 廃止（§6-1）───────────────────────────────────────────────
def test_confirm_rejects_without_necessity(monkeypatch):
    db = _db(); appmod.DB = db
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)   # PG ゲートを通す
    c = appmod.app.test_client(); _login(c, "a@example.com", db)
    did = c.post("/v4/drafts", json={"will_text": "意志だけ"}).get_json()["draft_id"]
    r = c.post(f"/v4/drafts/{did}/confirm")
    assert r.status_code == 400 and "必要像" in r.get_json()["error"]


def test_v4_seekers_rejects_without_necessity(monkeypatch):
    db = _db(); appmod.DB = db
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)
    c = appmod.app.test_client(); _login(c, "a@example.com", db)
    r = c.post("/v4/seekers", json={"will_text": "意志だけ"})
    assert r.status_code == 400 and "必要像" in r.get_json()["error"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\ndrafts テスト: {len(tests)} 件 全 PASS")
