"""指示書27: 生成状態の表示と再試行の配線。

- /v4/seekers/<id>/status・/retry が本人限定ゲート済み（§2-4）:
  未ログイン→401、他人id→403、本人→認証は通過（SQLite では以降 503）。
- retry の自動判定材料（§5-6）: 保存済み necessity の有無で
  is_fallback（生成からやり直すか、再ベクトル化のみか）が決まる。MemoryStore で検証。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import auth
import app as appmod
from db_v4 import MemoryStore, receive_profile_v4, GEN_PREPARING
from embedding_config import MODEL_TAG


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _login(c, email, db):
    """マジックリンクでログインし、セッションの subject_id を返す。"""
    tok = auth.issue_token(email, db_path=db)
    r = c.get(f"/auth/verify?token={tok}", follow_redirects=False)
    return r.headers["Location"].split("id=")[-1]


# ── §2-4 認証ゲート ────────────────────────────────────────────────────────────
def test_status_and_retry_require_login():
    db = _db(); appmod.DB = db
    c = appmod.app.test_client()
    # 未ログイン → 401（login_required）
    assert c.get("/v4/seekers/u_x/status").status_code == 401
    assert c.post("/v4/seekers/u_x/retry").status_code == 401


def test_status_and_retry_reject_other_user():
    db = _db(); appmod.DB = db
    c = appmod.app.test_client()
    sid = _login(c, "a@example.com", db)          # 本人 = sid
    # 他人の id → 403（require_self 不一致）
    assert c.get("/v4/seekers/u_other/status").status_code == 403
    assert c.post("/v4/seekers/u_other/retry").status_code == 403
    # 本人の id → 認証は通過（SQLite なので以降 503。401/403 でないことを確認）
    assert c.get(f"/v4/seekers/{sid}/status").status_code == 503
    assert c.post(f"/v4/seekers/{sid}/retry").status_code == 503


# ── §5-6 retry の自動判定材料 ───────────────────────────────────────────────────
def _profile_input():
    return {"will_text": "地域の居場所をつくる", "state_have": "場所",
            "state_can_type": "", "state_bound": "", "state_unsorted": "",
            "supporting_raw": {}}


def test_retry_material_necessity_saved_means_revectorize_only():
    """必要像が保存済み → get_necessity 非None → retry は再ベクトル化のみ（is_fallback=False）。"""
    store = MemoryStore()
    receive_profile_v4(store, "u_with", _profile_input(),
                       {"necessity_text": "伴走者が必要"}, generation_status=GEN_PREPARING)
    necessity = store.get_necessity("u_with", MODEL_TAG)
    assert necessity is not None
    assert (necessity is None) is False            # retry の is_fallback = necessity is None


def test_retry_material_no_necessity_means_regenerate():
    """必要像が未保存 → get_necessity None → retry は生成からやり直す（is_fallback=True）。"""
    store = MemoryStore()
    receive_profile_v4(store, "u_without", _profile_input(), None,
                       generation_status=GEN_PREPARING)
    necessity = store.get_necessity("u_without", MODEL_TAG)
    assert necessity is None
    assert (necessity is None) is True             # retry の is_fallback = necessity is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nv4 status/retry テスト: {len(tests)} 件 全 PASS")
