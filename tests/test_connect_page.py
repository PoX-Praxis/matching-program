"""指示書10: 「つながる」ページ /connect の骨組み・ナビ配線。"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app as appmod


def _c():
    return appmod.app.test_client()


def test_connect_route_ok():
    r = _c().get("/connect")
    assert r.status_code == 200
    b = r.get_data(as_text=True)
    assert "あなたへのおすすめ" in b and "登録者一覧" in b
    assert "/v4/match" in b and 'fetch("/seekers")' in b


def test_connect_sort_is_client_side_labels():
    b = _c().get("/connect").get_data(as_text=True)
    for label in ("総合順", "補完が効いた順", "共鳴が効いた順", "renderRecs"):
        assert label in b


def test_nav_points_to_connect():
    b = _c().get("/about").get_data(as_text=True)
    assert 'href="/connect" id="navMatch"' in b


def test_connect_reuses_existing_match_endpoint():
    rules = {r.rule for r in appmod.app.url_map.iter_rules()}
    assert "/connect" in rules and "/v4/match" in rules  # 新 match API は作っていない


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nconnect ページ テスト: {len(tests)} 件 全 PASS")
