"""
指示書09: 公開APIの遮断とスコープ化。

- GET /seekers/<id> は削除（404）
- GET /seekers は id・一行紹介・意志抜粋のみ（seeker 原文なし）
- GET /api/my/vessels?id=X は当事者分のみ
- GET / は /about へ、開発コンソール /dev と全件 /ledger は POX_DEBUG=1 のときのみ
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
from db import save_profile, list_public_seeker_index


# ── db 射影: 原文を出さない ─────────────────────────────────────────────────
def test_list_public_seeker_index_no_raw():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    save_profile("u_alice", {
        "_meta": {"schema_version": "v4"},
        "意志": "あ" * 60,  # 40字超 → 切り詰め＋…
        "現状": {"持っているもの": "秘密の資源", "できること_型": "秘密の型"},
        "supporting_material": {"一行紹介": "つなぐ人", "生テキスト": ["これは原文で秘密"]},
    }, db_path=db)
    rows = list_public_seeker_index(db_path=db)
    assert len(rows) == 1
    r = rows[0]
    assert set(r.keys()) == {"id", "one_liner", "will_excerpt"}    # この3キーのみ
    assert r["id"] == "u_alice" and r["one_liner"] == "つなぐ人"
    assert r["will_excerpt"].endswith("…") and len(r["will_excerpt"]) <= 41
    # 原文（生テキスト・現状詳細・意志全文）が混入しないこと
    blob = str(r)
    assert "生テキスト" not in blob and "秘密の資源" not in blob and "秘密の型" not in blob
    assert "あ" * 60 not in blob   # 意志全文は出ない（抜粋のみ）


# ── HTTP: 経路の遮断 ────────────────────────────────────────────────────────
def _client():
    return appmod.app.test_client()


def test_seeker_by_id_route_removed():
    assert _client().get("/seekers/u_alice").status_code == 404


def test_get_seekers_returns_projection_only(monkeypatch=None):
    appmod.list_public_seeker_index = lambda db_path=None: [
        {"id": "x", "one_liner": "o", "will_excerpt": "w"}]
    try:
        r = _client().get("/seekers")
        assert r.status_code == 200
        data = r.get_json()
        assert data and set(data[0].keys()) == {"id", "one_liner", "will_excerpt"}
    finally:
        from db import list_public_seeker_index as real
        appmod.list_public_seeker_index = real


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
        r = _client().get("/api/my/vessels?id=alice")
        ids = {v["vessel_id"] for v in r.get_json()}
        assert ids == {"v1", "v2"}      # alice が当事者の2件のみ（v3 は他人）
    finally:
        appmod.load_all_vessels = orig


def test_my_vessels_requires_id():
    assert _client().get("/api/my/vessels").status_code == 400


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n公開API遮断テスト: {len(tests)} 件 全 PASS")
