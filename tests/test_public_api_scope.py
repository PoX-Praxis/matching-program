"""
指示書09: 公開APIの遮断とスコープ化。

- GET /seekers/<id> は削除（404）
- GET /seekers の形は tests/test_directory_v4.py（指示書55 PR-B で v4 化）
- GET /api/my/vessels?id=X は当事者分のみ
- GET / は /about へ、開発コンソール /dev と全件 /ledger は POX_DEBUG=1 のときのみ
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod


# ── HTTP: 経路の遮断 ────────────────────────────────────────────────────────
def _client():
    return appmod.app.test_client()


def test_seeker_by_id_route_removed():
    assert _client().get("/seekers/u_alice").status_code == 404


def test_root_redirects_to_about():
    # 指示書09 §3-5: / は /about へリダイレクト。LP 相当の原稿は about に統合（指示書15）。
    r = _client().get("/")
    assert r.status_code in (301, 302)
    assert "/about" in r.headers.get("Location", "")


def test_about_carries_landing_copy():
    # 指示書15: 新原稿が「PoXとは」に全面差し替えられている。
    body = _client().get("/about").get_data(as_text=True)
    assert "まだ形になっていないことに、必要な人を。" in body
    assert "1｜誰のためのものか" in body
    # 5「つながった後」に接続の後段としてコミュニティを統合（指示書15 改訂）
    assert "5｜つながった後" in body
    assert "参加できるのは、作成者が承認した人だけです。" in body
    # §3 削除対象（旧・機能紹介）が残っていない
    assert "誰でも、どんな目的でも使えます" not in body
    assert "コミュニティ機能" not in body        # 旧・節見出しは残っていない
    assert "founder" not in body                  # §5: 内部用語を出さない


def test_dev_console_gated_by_debug():
    os.environ.pop("POX_DEBUG", None)
    assert _client().get("/dev").status_code == 404
    os.environ["POX_DEBUG"] = "1"
    try:
        assert _client().get("/dev").status_code == 200      # 有効時は表示
    finally:
        os.environ.pop("POX_DEBUG", None)


def test_ledger_gated_by_debug():
    os.environ.pop("POX_DEBUG", None)
    assert _client().get("/ledger").status_code == 404


# ── HTTP: 台帳スコープ化 ────────────────────────────────────────────────────
def _vessels():
    return [
        {"vessel_id": "v1", "founder": "alice", "joins": [{"joiner": "bob"}], "is_connected": False},
        {"vessel_id": "v2", "founder": "carol", "joins": [{"joiner": "alice"}], "is_connected": True},
        {"vessel_id": "v3", "founder": "carol", "joins": [{"joiner": "dave"}], "is_connected": False},
    ]


def test_my_vessels_scoped_to_party():
    orig = appmod.load_all_vessels
    appmod.load_all_vessels = lambda db_path=None: _vessels()
    try:
        c = _client()
        with c.session_transaction() as sess:   # 指示書22: セッション本人限定
            sess["subject_id"] = "alice"
        r = c.get("/api/my/vessels?id=alice")
        ids = {v["vessel_id"] for v in r.get_json()}
        assert ids == {"v1", "v2"}      # alice が当事者の2件のみ（v3 は他人）
    finally:
        appmod.load_all_vessels = orig


def test_my_vessels_requires_login():
    # 指示書22: 未ログインは 401（?id= を知っていても他人の接続一覧は出さない・404 で隠さない）
    os.environ.pop("POX_DEBUG", None)
    assert _client().get("/api/my/vessels?id=alice").status_code == 401
    # 他人になりすまし（セッション ≠ ?id=）→ 403
    c = _client()
    with c.session_transaction() as sess:
        sess["subject_id"] = "alice"
    assert c.get("/api/my/vessels?id=bob").status_code == 403


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n公開API遮断テスト: {len(tests)} 件 全 PASS")
