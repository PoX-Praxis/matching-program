"""指示書30: 表示名レイヤーの検証。

制約:
  - 一意性は課さない（同一性は subject_id が担保）。
  - 上限30字。空は許さず未設定（行なし）＝ subject_id にフォールバック。
  - redact_text を通す（メール等 PII の混入防止）。
  - 本人のみ変更可（login_required + require_self）。
  - 台帳・content_hash・三つ組には入れない（本テーブルは通常DBの可変値）。
  - コミュニティは communities.name を正とし、display_names には二重に持たない。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import display_names as DN
import community as C
import app as appmod


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _client():
    os.environ.pop("POX_DEBUG", None)
    appmod.DB = _db()
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _login(c, sid):
    with c.session_transaction() as sess:
        sess["subject_id"] = sid


# ── モジュール層（redact / 30字 / 空→未設定 / 一意性なし）────────────────────
def test_set_get_roundtrip():
    db = _db()
    assert DN.set_display_name("u_a", "あきら", db_path=db) == "あきら"
    assert DN.get_display_name("u_a", db_path=db) == "あきら"


def test_empty_unsets_and_falls_back_to_none():
    db = _db()
    DN.set_display_name("u_a", "あきら", db_path=db)
    assert DN.set_display_name("u_a", "", db_path=db) is None      # 空は未設定に
    assert DN.get_display_name("u_a", db_path=db) is None          # 行が無い＝フォールバック
    # 空白のみも未設定扱い
    DN.set_display_name("u_a", "あきら", db_path=db)
    assert DN.set_display_name("u_a", "   ", db_path=db) is None
    assert DN.get_display_name("u_a", db_path=db) is None


def test_length_capped_at_30():
    db = _db()
    long = "あ" * 50
    saved = DN.set_display_name("u_a", long, db_path=db)
    assert len(saved) == 30
    assert DN.get_display_name("u_a", db_path=db) == "あ" * 30


def test_redacts_email_pii():
    db = _db()
    saved = DN.set_display_name("u_a", "連絡は a@b.com まで", db_path=db)
    assert "a@b.com" not in (saved or "")
    assert "@" not in (saved or "")


def test_no_uniqueness_two_ids_same_name():
    db = _db()
    DN.set_display_name("u_a", "同名", db_path=db)
    DN.set_display_name("u_b", "同名", db_path=db)   # 一意性は課さない
    assert DN.get_display_name("u_a", db_path=db) == "同名"
    assert DN.get_display_name("u_b", db_path=db) == "同名"


def test_get_many_batch():
    db = _db()
    DN.set_display_name("u_a", "A", db_path=db)
    DN.set_display_name("u_b", "B", db_path=db)
    got = DN.get_many(["u_a", "u_b", "u_c", None, ""], db_path=db)
    assert got == {"u_a": "A", "u_b": "B"}          # 未設定・空は含まれない


def test_overwrite_no_history():
    db = _db()
    DN.set_display_name("u_a", "旧", db_path=db)
    DN.set_display_name("u_a", "新", db_path=db)     # 上書き（履歴なし）
    assert DN.get_display_name("u_a", db_path=db) == "新"


# ── resolver（community は communities.name / 個人は表示名 / 無ければ subject_id）──
def test_resolver_prefers_community_name():
    db = _db()
    appmod.DB = db
    cid = C.create_community("u_founder", "みらい共同体", "d", db_path=db)["id"]
    # community に対しては display_names を引かず communities.name を返す
    names = appmod._resolve_names([cid])
    assert names[cid] == "みらい共同体"


def test_resolver_individual_and_fallback():
    db = _db()
    appmod.DB = db
    DN.set_display_name("u_named", "名前あり", db_path=db)
    names = appmod._resolve_names(["u_named", "u_nameless"])
    assert names["u_named"] == "名前あり"
    assert names["u_nameless"] == "u_nameless"       # 未設定は subject_id にフォールバック


# ── HTTP（本人のみ変更可）────────────────────────────────────────────────────
def test_set_display_name_requires_login():
    c = _client()
    r = c.post("/api/my/display-name", json={"id": "u_a", "name": "x"})
    assert r.status_code == 401


def test_set_display_name_rejects_impersonation():
    c = _client(); _login(c, "u_a")
    r = c.post("/api/my/display-name", json={"id": "u_hacker", "name": "x"})
    assert r.status_code == 403                       # require_self が食い違いを弾く


def test_set_display_name_self_ok_and_unset():
    c = _client(); _login(c, "u_a")
    r = c.post("/api/my/display-name", json={"id": "u_a", "name": "あきら"})
    body = r.get_json()
    assert r.status_code == 200
    assert body["display_name"] == "あきら" and body["is_set"] is True
    # 空にすると未設定＝subject_id にフォールバック
    r2 = c.post("/api/my/display-name", json={"id": "u_a", "name": ""})
    body2 = r2.get_json()
    assert body2["is_set"] is False and body2["display_name"] == "u_a"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\n表示名レイヤー: {len(tests)} 件 全 PASS")
