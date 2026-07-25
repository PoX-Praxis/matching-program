"""指示書13: プライバシーポリシー設置と同意の証跡記録。"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
from db import record_policy_consent, _connect


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


# ── ページ・導線 ────────────────────────────────────────────────────────────
def test_privacy_page_served():
    b = appmod.app.test_client().get("/privacy")
    assert b.status_code == 200
    body = b.get_data(as_text=True)
    assert "プライバシーポリシー" in body and "重要な点" in body


def test_nav_and_about_link_to_privacy():
    about = appmod.app.test_client().get("/about").get_data(as_text=True)
    assert 'href="/privacy"' in about                 # about に導線
    assert 'href="/privacy"' in about                 # _nav 経由でも（about に include）


def test_register_has_consent_checkbox_and_guard():
    b = appmod.app.test_client().get("/register").get_data(as_text=True)
    assert 'id="policyAgree"' in b                     # 既定オフのチェックボックス
    assert "プライバシーポリシーへの同意が必要です" in b   # ガードのメッセージ
    assert "PRIVACY_POLICY_VERSION" in b               # 版を1箇所で保持
    # 既存 consent（"本人合意済み"）は温存、新規は別フィールド privacy_policy_agreed（混同しない）
    assert "本人合意済み" in b and "privacy_policy_agreed" in b


# ── 同意の証跡記録（別テーブル・既存 consent 非改変）─────────────────────────
def test_record_policy_consent_persists():
    db = _db()
    record_policy_consent("u1", "2026-07", db_path=db)
    with _connect(db) as con:
        row = con.execute(
            "SELECT user_id, policy_version, agreed_at FROM policy_consents WHERE user_id=%s",
            ("u1",)).fetchone()
    assert row and row[0] == "u1" and row[1] == "2026-07" and row[2]


def test_record_policy_consent_upsert_same_version():
    db = _db()
    record_policy_consent("u1", "2026-07", db_path=db)
    record_policy_consent("u1", "2026-07", db_path=db)   # 再登録 → 重複行を作らない
    with _connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM policy_consents WHERE user_id=%s", ("u1",)).fetchone()[0]
    assert n == 1


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nprivacy consent テスト: {len(tests)} 件 全 PASS")
