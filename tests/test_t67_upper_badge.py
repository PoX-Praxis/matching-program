"""指示書67 — 上層の印の保留（POX_UPPER_BADGE_SCOPE）。

- related（既定）: 関わっている組（申し出中・承認待ち・接続中）以外には、上層の印・成否・「成立しない」文言を
  画面にも API にも出さない
- 関わっている組では、いまの表示のまま
- all に戻すと、いまの振る舞いに戻る
- 上層の計算は related でも行い、監査のルートでは見える
- 下層の表示のフラグは閉じたまま
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT, os.path.join(ROOT, "tests")]

import app as appmod  # noqa: E402
import match_config  # noqa: E402
import matcher_v5  # noqa: E402
from test_t61_resonance_gate import _cli, db  # noqa: E402,F401
from test_t65b2_two_layer import _match, world2  # noqa: E402,F401

TOKEN = "test-anchor-token"


@pytest.fixture
def related(monkeypatch):
    monkeypatch.delenv("POX_UPPER_BADGE_SCOPE", raising=False)      # 既定（related）
    monkeypatch.delenv("POX_LOWER_LAYER_OPEN", raising=False)
    assert match_config.upper_badge_scope() == "related"


def _preview(other, sid="me"):
    return _cli(sid).get(f"/api/connections/preview?with={other}").get_json()


def _html():
    return open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()


# ── 381 related: 関わっていない組には、上層の印・成否・成立しない文言を画面にも API にも出さない ─────────────
def test_t381_related_hides_upper_for_unrelated(world2, related):
    d, g, pids = _match()
    assert d["upper_scope"] == "related"
    assert all(x["results"] == [] for x in d["groups"]) and d["results"] == []      # c_full は上層を通るが出さない
    assert all(x["purpose_id"] != "_theirs" for x in d["groups"])                    # 「あなたを必要としている相手」も
    assert d["status"] == "none"
    # 申し出る前の根拠: 上層が通る相手（c_full）と通らない相手（c_part）で、まったく同じ応答（成否を読み取らせない）
    full, part = _preview("c_full"), _preview("c_part")
    assert full == part == {"status": "none", "message": appmod.PREVIEW_PREPARING, "reasons": []}
    # 画面（つながるの「照合の結果」の節と、上層の保留の文言）は指示書70 §4-4 で外した
    html = _html()
    assert "RELATED_ZERO_TEXT" not in html and "upperScope" not in html


# ── 382 related: 申し出中・承認待ち・接続中の組では、いまと同じ表示 ─────────────────────────────────────
def test_t382_related_keeps_engaged_pairs(world2, related, monkeypatch):
    d, g, pids = _match()
    assert g[pids[0]]["results"] == []
    # 申し出中（自分 → c_full。目的2 で申し出）: 目的1 のカードは出る（その目的で申し出中なら、従来どおりその目的からだけ外す）
    assert _cli("me").post("/approve", json={"to_id": "c_full", "purpose_id": pids[1]}).status_code == 200
    d, g, pids = _match()
    assert [r["candidate_id"] for r in g[pids[0]]["results"]] == ["c_full"]
    monkeypatch.setenv("POX_UPPER_BADGE_SCOPE", "all")
    d_all, g_all, _ = _match()
    strip = lambda grp: [{k: v for k, v in r.items()} for r in grp["results"]]  # noqa: E731
    assert strip(g[pids[0]]) == strip(g_all[pids[0]])                             # 中身は all と同じ
    monkeypatch.delenv("POX_UPPER_BADGE_SCOPE", raising=False)
    # 申し出る前の根拠も、関わっている組（申し出中）では上層で通った対を出す
    p = _preview("c_full")
    assert p["status"] == "ok" and p["reasons"] and p["layer"] == "upper"
    # 承認待ち（相手 c_part → 自分）: 上層が通らない相手なので従来どおり「準備中」。関わっている組として扱われる
    assert _cli("c_part").post("/approve", json={"to_id": "me"}).status_code == 200
    assert appmod._engaged("me", "c_part") is True
    # 接続中（相互の承認）: 申し出る前の根拠は従来どおり。/api/connections/reason（関わっている組だけ）は変えていない
    assert _cli("c_full").post("/approve", json={"to_id": "me"}).status_code == 200
    assert appmod._engaged("me", "c_full") is True
    assert _preview("c_full")["status"] == "ok"
    assert _cli("me").get("/api/connections/reason?with=c_full").status_code == 200
    assert _cli("me").get("/api/connections/reason?with=c_z").status_code == 403          # 関わっていない組は従来どおり不可


# ── 383 all に戻すと、いまの振る舞い。知らない値は related（印を出す側に倒さない）──────────────────────────
def test_t383_all_restores_current_behavior(world2, monkeypatch):
    monkeypatch.setenv("POX_UPPER_BADGE_SCOPE", "all")
    assert match_config.upper_badge_scope() == "all"
    d, g, pids = _match()
    assert d["upper_scope"] == "all"
    assert [r["candidate_id"] for r in g[pids[0]]["results"]] == ["c_full"]
    p = _preview("c_full")
    assert p["status"] == "ok" and p["reasons"]
    assert _preview("c_part") == {"status": "none", "message": appmod.PREVIEW_PREPARING, "reasons": []}
    for v in ("", "ALL ", "open", "1"):
        monkeypatch.setenv("POX_UPPER_BADGE_SCOPE", v)
        assert match_config.upper_badge_scope() == ("all" if v.strip().lower() == "all" else "related")


# ── 384 上層の計算は related でも行い、監査のルート（トークン）では見える ─────────────────────────────────
def test_t384_upper_still_computed_and_audit_sees_it(world2, related, monkeypatch):
    calls = []
    real = matcher_v5.match_pair
    monkeypatch.setattr(matcher_v5, "match_pair", lambda a, b: calls.append(1) or real(a, b))
    d, g, pids = _match()
    assert calls and g[pids[0]]["results"] == []                                 # 計算はしたが、出していない
    monkeypatch.setenv("POX_ANCHOR_TOKEN", TOKEN)
    r = appmod.app.test_client().get("/ledger/audit/match?pair=me,c_full", headers={"X-Anchor-Token": TOKEN})
    assert r.status_code == 200
    a_to_b = r.get_json()["v5"]["a_to_b"]
    assert any(x["purpose_id"] == pids[0] and x["complement"] is True for x in a_to_b)   # 上層は成立している
    assert appmod.app.test_client().get("/ledger/audit/match?pair=me,c_full").status_code == 404   # トークン必須のまま


# ── 385 下層の表示のフラグは閉じたまま（related でも要素の行を返さない）─────────────────────────────────
def test_t385_lower_layer_flag_stays_closed(world2, related):
    assert match_config.lower_layer_open() is False
    d, g, pids = _match()
    assert all("elements" not in x for x in d["groups"])
    src = open(os.path.join(ROOT, "src", "match_config.py"), encoding="utf-8").read()
    assert 'return _flag("POX_LOWER_LAYER_OPEN")' in src
