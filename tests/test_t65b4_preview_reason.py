"""指示書65B-4 — 申し出る前の根拠（テスト 355〜362。65-B §6-1 の制約 1〜8）。

GET /api/connections/preview?with=<相手>: 常に「自分 × 相手」。閲覧者自身の必要像の文 × 相手の公開の文のうち、
判定を通った対だけ（数値なし）。下層の表示が閉じている間は上層で通った対だけ・通らなければ「要素単位の照合は準備中です」。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
from test_t61_resonance_gate import X, Y, Z, _cli, _doc, db  # noqa: E402,F401
from test_t65b2_two_layer import _person, world2  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _fresh_rate():
    appmod._PREVIEW_HITS.clear()
    yield
    appmod._PREVIEW_HITS.clear()


def _pv(sid, other, **q):
    qs = "&".join(f"{k}={v}" for k, v in {"with": other, **q}.items())
    return _cli(sid).get(f"/api/connections/preview?{qs}")


# ── 355 制約 1・2: 自分 × 相手だけ。閲覧者の必要像 × 相手の文の対だけ・数値なし ─────────────────────
def test_t355_self_times_other_only(world2, monkeypatch):
    monkeypatch.delenv("POX_LOWER_LAYER_OPEN", raising=False)
    d = _pv("me", "c_full").get_json()
    assert d["status"] == "ok" and d["layer"] == "upper"
    assert {x["kind"] for x in d["reasons"]} == {"fill_mine"}                  # 相手の必要像の向きは返さない
    assert [x["need"] for x in d["reasons"]][0] == "事業を立ち上げる局面で、構想を実行に移す側として関わってきた人。"
    blob = json.dumps(d, ensure_ascii=False)
    for k in ('"g"', "score", "resonance", "gate"):
        assert k not in blob, k
    assert _pv("me", "c_full", me="c_z").status_code == 403                   # 他人として引けない
    assert _pv("me", "me").status_code == 400


# ── 356 制約 3: 母集団非依存（無関係な人を足しても同じ）────────────────────────────────────
def test_t356_population_independent(world2):
    before = _pv("me", "c_full").get_json()
    other = [[[0.0, 0.0, -1.0]] * 2, [[0.0, 0.0, -1.0]]]
    for i in range(10):
        _person(world2, f"u_{i}", _doc(), needs=other, offers=[X, Y], dest=X)
    assert _pv("me", "c_full").get_json() == before


# ── 357 制約 4: 見送りが効いている相手には出さない。申し出中・承認待ちには出す ───────────────────────
def test_t357_declined_excluded(world2):
    assert _cli("c_full").post("/approve", json={"to_id": "me"}).status_code == 200
    assert _pv("me", "c_full").status_code == 200                              # 承認待ち
    assert _cli("me").post("/api/connections/decline", json={"to_id": "c_full"}).status_code == 200
    assert _pv("me", "c_full").status_code == 403
    assert _pv("c_full", "me").status_code == 403                              # 見送られた側も


# ── 358 制約 5: 閲覧者に目的（必要像）が無ければ返さない ────────────────────────────────────────
def test_t358_no_profile(world2):
    d = _pv("nobody", "c_full").get_json()
    assert d["status"] == "no_profile" and d["reasons"] == []


# ── 359 制約 6: 閉じている間は上層だけ・通らなければ「準備中」。開けると要素の対・無ければ「見つかりませんでした」 ────
def test_t359_closed_upper_only_and_wording(world2, monkeypatch):
    monkeypatch.delenv("POX_LOWER_LAYER_OPEN", raising=False)
    d = _pv("me", "c_part").get_json()                                         # 欠かせない X に届かない
    assert d["status"] == "none" and d["message"] == "要素単位の照合は準備中です" and d["reasons"] == []
    monkeypatch.setenv("POX_LOWER_LAYER_OPEN", "1")
    d = _pv("me", "c_part").get_json()
    assert d["status"] == "ok" and d["layer"] == "element"
    assert [x["need"] for x in d["reasons"]] == ["事業構造をロジカルに組み立てる立場で関わる人。"]
    _person(world2, "c_none", _doc(), needs=[[[0.0, 0.0, -1.0]] * 2, [[0.0, 0.0, -1.0]]],
            offers=[[0.0, 0.0, -1.0]] * 2, dest=X)
    d = _pv("me", "c_none").get_json()
    assert d["status"] == "none" and d["message"] == "あなたの必要に応える文は見つかりませんでした"


# ── 360 制約 7: レート制限 ───────────────────────────────────────────────────────────
def test_t360_rate_limited(world2, monkeypatch):
    monkeypatch.setattr(appmod, "PREVIEW_RATE", (2, 600))
    assert _pv("me", "c_full").status_code == 200
    assert _pv("me", "c_part").status_code == 200
    assert _pv("me", "c_z").status_code == 429
    assert _pv("c_z", "me").status_code == 200                                 # 閲覧者ごと


# ── 361 制約 8: 相手の文は公開の日時の閾値に従う ───────────────────────────────────────────
def test_t361_other_sentences_follow_public_threshold(world2, monkeypatch):
    monkeypatch.setenv("POX_OFFER_PUBLIC_SINCE", "2999-01-01T00:00:00+00:00")   # 相手の与え像の版は閾値より前
    d = _pv("me", "c_full").get_json()
    assert d["reasons"] and all(x["offer"] == appmod.OFFER_HIDDEN_TEXT and x.get("offer_hidden") for x in d["reasons"])
    assert "まだ形になっていない仕組みを試作として形にする立場で関われる。" not in json.dumps(d, ensure_ascii=False)


# ── 362 画面: プロフィールに根拠の欄（一覧から開いても同じページ）・照合できないときは別の文言 ─────────────────
def test_t362_profile_shows_preview_and_availability(world2, monkeypatch):
    html = open(os.path.join(ROOT, "templates", "profile.html"), encoding="utf-8").read()
    assert 'id="previewBox"' in html and '{% include "_match_reason.html" %}' in html
    assert "/api/connections/preview?with=" in html and "PoXReason.previewHtml(d)" in html
    part = open(os.path.join(ROOT, "templates", "_match_reason.html"), encoding="utf-8").read()
    assert "function previewHtml(d)" in part
    monkeypatch.setattr(appmod, "_matching_available", lambda: False)
    d = _pv("me", "c_full").get_json()
    assert d["status"] == "unavailable" and d["message"] == "いま照合できません"
