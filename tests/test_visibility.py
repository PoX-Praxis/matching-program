"""指示書17 §5: visibility.changed（プロフィール公開範囲の変更）。Nomic 非依存。

- 本人のみ変更（body id != path → 403）。scope は public|private
- profiles.visibility を更新し visibility.changed を台帳へ。churn は同一 scope でスキップ
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
import subject_ledger as SL
import ledger_events as le
from db import save_profile, get_profile_visibility


def _setup():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.DB = db
    save_profile("u1", {"_meta": {"schema_version": "v4"}, "意志": "つなぐ",
                        "現状": {"持っているもの": "知識"},
                        "supporting_material": {}}, db_path=db)
    return db


def test_emitter_churn_and_chain():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    r1 = SL.publish_visibility_changed("u1", "private", db_path=db)
    assert r1["skipped"] is False
    r2 = SL.publish_visibility_changed("u1", "private", db_path=db)     # 同一 scope
    assert r2["skipped"] is True
    r3 = SL.publish_visibility_changed("u1", "public", db_path=db)
    assert r3["skipped"] is False
    evs = le.get_events(type_="visibility.changed", db_path=db)
    assert [e["payload"]["scope"] for e in evs] == ["private", "public"]
    assert le.verify_chain(db_path=db)["ok"] is True


def _login(c, sid):   # 指示書22: セッションが唯一の身元
    with c.session_transaction() as sess:
        sess["subject_id"] = sid


def test_endpoint_updates_db_and_ledger():
    db = _setup()
    c = appmod.app.test_client()
    _login(c, "u1")
    assert get_profile_visibility("u1", db_path=db) == "public"       # 既定
    r = c.post("/api/profile/u1/visibility", json={"id": "u1", "scope": "private"})
    assert r.status_code == 200
    assert get_profile_visibility("u1", db_path=db) == "private"      # DB 反映
    assert le.get_events(type_="visibility.changed", db_path=db)[0]["payload"]["scope"] == "private"


def test_endpoint_owner_only_and_scope_validation():
    db = _setup()
    c = appmod.app.test_client()
    _login(c, "u1")
    # body の id が本人（セッション）と食い違う → 403
    assert c.post("/api/profile/u1/visibility", json={"id": "other", "scope": "private"}).status_code == 403
    # path が本人（セッション）と食い違う → 403（指示書22: id を知るだけでは他人を書き換えられない）
    assert c.post("/api/profile/other/visibility", json={"id": "other", "scope": "private"}).status_code == 403
    # 本人・不正 scope → 400
    assert c.post("/api/profile/u1/visibility", json={"id": "u1", "scope": "weird"}).status_code == 400
    # 未ログイン → 401（404 で隠さない）
    assert appmod.app.test_client().post(
        "/api/profile/u1/visibility", json={"id": "u1", "scope": "private"}).status_code == 401
    # 本人だがプロフィール不在 → 404（別セッションで検証）
    c2 = appmod.app.test_client(); _login(c2, "nope")
    assert c2.post("/api/profile/nope/visibility", json={"id": "nope", "scope": "private"}).status_code == 404


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nvisibility テスト: {len(tests)} 件 全 PASS")
