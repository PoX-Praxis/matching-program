"""指示書65B-3 — 門のラベル化（テスト 352〜354。248 は書き換え）。

要素どうしは門の成否によらず常に比べる。閲覧者自身の目的については、門の結果をラベル
「向かう先は異なるが、この要素には応える」として出し、その要素一致は申し出の候補にしない。
相手の目的の門の結果は、従来どおり出さない・記録しない。門が立たない目的では要素一致を申し出の候補にしてよい。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import v5  # noqa: E402
from matcher_v5 import match_pair  # noqa: E402
from test_t61_resonance_gate import X, Y, Z, _cli, _groups, _side, db, world  # noqa: E402,F401

LABEL = "向かう先は異なるが、この要素には応える"


# ── 352 受信箱: 自分の目的の門が閉じた要素一致はラベルつき。相手の目的の門の結果は出さない ─────────────────
def test_t352_inbox_own_gate_label(world):
    pid = v5.live_necessities_v5("c_gate", db_path=appmod.DB)[0]["purpose_id"]
    assert _cli("c_gate").post("/approve", json={"to_id": "me", "purpose_id": pid}).status_code == 200
    r = _cli("me").get("/api/connections/reason?with=c_gate").get_json()["reason"]
    assert r["axis"] == "element"
    mine = [x for x in r["reasons"] if x["kind"] == "fill_mine"]
    assert mine and all(x["label"] == LABEL for x in mine)
    assert all("label" not in x for x in r["reasons"] if x["kind"] == "fill_theirs")
    # 相手の側（c_gate）から見ると、c_gate 自身の目的の門の結果（me の向かう先とは共鳴しない）は c_gate にだけ見える
    r2 = _cli("c_gate").get("/api/connections/reason?with=me").get_json()["reason"]
    assert all("label" not in x for x in (r2 or {}).get("reasons", []) if x["kind"] == "fill_theirs")
    html = open(os.path.join(ROOT, "templates", "_match_reason.html"), encoding="utf-8").read()
    assert "if (x.label) h +=" in html


# ── 353 門が立たない目的では要素一致が申し出の候補・門が閉じた目的では候補にしない ─────────────────────
def test_t353_offer_candidate_by_own_gate(world, monkeypatch):
    monkeypatch.setenv("POX_LOWER_LAYER_OPEN", "1")
    d, _ = _groups()
    pids = [n["purpose_id"] for n in v5.live_necessities_v5("me", db_path=appmod.DB)]
    g = {x["purpose_id"]: x for x in d["groups"]}
    p2 = {p["candidate_id"]: p for e in g[pids[1]]["elements"] for p in e["people"]}
    assert p2["c_low"]["offer_candidate"] is True and "label" not in p2["c_low"]       # 目的2 は門が立たない
    p1 = {p["candidate_id"]: p for e in g[pids[0]]["elements"] for p in e["people"]}
    assert p1["c_gate"]["offer_candidate"] is False and p1["c_gate"]["label"] == LABEL
    assert p1["c_ok"]["offer_candidate"] is True                                       # 門が立って通る
    html = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert "p.offer_candidate ?" in html and "p.label ?" in html


# ── 354 照合の関数: 門が閉じても要素を比べる。門の結果は自分の目的の分だけ返す ─────────────────────────
def test_t354_elements_compared_regardless_of_gate():
    mine = _side(needs=[X], dest=X, gs=0.9, gu=0.0, pid="mine")                       # 自分の目的は門あり
    theirs = _side(needs=[Z], offers=[X], dest=Z, gs=0.9, gu=0.0, pid="theirs")       # 相手の目的も門あり・共鳴しない
    r = match_pair(mine, theirs)
    assert r["by_purpose"] == {}                                                       # 上層は門で不成立
    assert r["elements"]["mine"][0]["offers"] == ["o"]                                 # 要素は比べる
    assert r["own_gate_closed"] == {"mine": True}                                      # 相手の目的の門は返さない
    assert "theirs" not in json.dumps(r["own_gate_closed"])
