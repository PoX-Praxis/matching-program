"""指示書22: 本人限定エンドポイントの認証ゲート（401/403・DEBUG バイパス）。

本人限定データは「?id= を知っていること」ではなく「セッションに紐づく本人であること」で守る。
- セッション無し → 401（404 で隠さない）
- セッションと id が食い違う → 403
- セッション本人なら 200（?id= は受け取ってよいが一致必須）
- POX_DEBUG=1 は開発バイパス（セッション無しでも通る）
- 対象外（登録 POST /seekers・/v4/seekers・公開 GET /api/profile/<id>）はゲートしない
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod


def _client(db=None):
    appmod.DB = db or os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod.DB


def _login(c, sid):
    with c.session_transaction() as sess:
        sess["subject_id"] = sid


# 第1群＋第2群の GET（?id= / path）を一括で確認する対象表
GET_ENDPOINTS = [
    ("/api/my/necessity?id=%s", "u1"),
    ("/api/my/vessels?id=%s", "u1"),
    ("/api/profile/%s/edit", "u1"),
    ("/api/inbox?id=%s", "u1"),
    ("/api/conversation?me=%s&with=u2", "u1"),
]


def _url(tmpl, who):
    return tmpl % ((who, ) if tmpl.count("%s") == 1 else (who, ))


# ── 401: 未ログイン（404 で隠さない）─────────────────────────────
def test_get_endpoints_401_without_session():
    os.environ.pop("POX_DEBUG", None)
    c, _ = _client()
    for tmpl, who in GET_ENDPOINTS:
        r = c.get(_url(tmpl, who))
        assert r.status_code == 401, f"{tmpl} → {r.status_code}（401 期待・404 で隠さない）"
        assert r.get_json().get("auth_required") is True


def test_post_endpoints_401_without_session():
    os.environ.pop("POX_DEBUG", None)
    c, _ = _client()
    r = c.post("/api/snapshot/s1/visibility", json={"id": "u1", "hidden": True})
    assert r.status_code == 401
    r = c.post("/api/profile/u1/visibility", json={"id": "u1", "scope": "private"})
    assert r.status_code == 401


# ── 403: ログインしているが id が食い違う ───────────────────────
def test_get_endpoints_403_on_mismatch():
    os.environ.pop("POX_DEBUG", None)
    c, _ = _client()
    _login(c, "u1")
    for tmpl, _who in GET_ENDPOINTS:
        r = c.get(_url(tmpl, "SOMEONE_ELSE"))
        assert r.status_code == 403, f"{tmpl} → {r.status_code}（403 期待）"


def test_post_endpoints_403_on_mismatch():
    os.environ.pop("POX_DEBUG", None)
    c, _ = _client()
    _login(c, "u1")
    # snapshot: body id が他人
    r = c.post("/api/snapshot/s1/visibility", json={"id": "victim", "hidden": False})
    assert r.status_code == 403
    # profile visibility: path が他人
    r = c.post("/api/profile/victim/visibility", json={"id": "victim", "scope": "public"})
    assert r.status_code == 403
    # profile visibility: path は本人だが body id が他人 → 403
    r = c.post("/api/profile/u1/visibility", json={"id": "victim", "scope": "public"})
    assert r.status_code == 403


# ── 200: セッション本人なら通る（?id= は一致）─────────────────────
def test_get_endpoints_200_for_owner():
    os.environ.pop("POX_DEBUG", None)
    c, db = _client()
    from db import save_profile
    save_profile("u1", {"意志": "所有者テスト", "現状": {}}, db_path=db)  # edit が 404 でなく 200 を返すよう用意
    _login(c, "u1")
    for tmpl, who in GET_ENDPOINTS:
        r = c.get(_url(tmpl, who))
        assert r.status_code == 200, f"{tmpl} → {r.status_code}（200 期待）"


# ── DEBUG バイパス（開発）──────────────────────────────────────
def test_debug_bypass_allows_without_session():
    os.environ["POX_DEBUG"] = "1"
    try:
        c, db = _client()
        from db import save_profile
        save_profile("u1", {"意志": "DEBUG テスト", "現状": {}}, db_path=db)
        for tmpl, who in GET_ENDPOINTS:
            r = c.get(_url(tmpl, who))
            assert r.status_code == 200, f"{tmpl} → {r.status_code}（DEBUG 200 期待）"
    finally:
        os.environ.pop("POX_DEBUG", None)


# ── 登録はログイン後（選択肢1）。プロフィール id はセッションに束縛される ──────────
def test_registration_requires_login():
    os.environ.pop("POX_DEBUG", None)
    c, _ = _client()
    # 未ログインでは登録は 401（登録はログイン後・選択肢1）
    r = c.post("/seekers", json={"意志": "何かを作りたい", "現状": {"持っているもの": "時間"}})
    assert r.status_code == 401


def test_registration_binds_profile_to_session():
    os.environ.pop("POX_DEBUG", None)
    c, _ = _client()
    with c.session_transaction() as sess:
        sess["subject_id"] = "u_alice"
    # user_id を送らなくても、プロフィール id はセッションの subject_id になる
    r = c.post("/seekers", json={"意志": "作りたい", "現状": {"持っているもの": "時間"}})
    assert r.status_code == 201 and r.get_json()["id"] == "u_alice"
    # 他人の id を指定して上書きしようとしても 403（なりすまし阻止）
    r2 = c.post("/seekers", json={"user_id": "u_victim", "意志": "x", "現状": {}})
    assert r2.status_code == 403


def test_public_profile_not_gated():
    os.environ.pop("POX_DEBUG", None)
    c, db = _client()
    from db import save_profile
    save_profile("pub1", {"意志": "公開テスト", "現状": {}}, db_path=db)
    r = c.get("/api/profile/pub1")   # 公開ビュー: 未ログインでも 200
    assert r.status_code == 200


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n認証ゲート テスト: {len(tests)} 件 全 PASS")
