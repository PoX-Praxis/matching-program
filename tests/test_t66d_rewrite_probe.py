"""指示書66D 順4・順5 — 忠実性の組み方と、要素の集合だけを差し替える計測（本人の文を使わない）。"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]
import rewrite_faithfulness_66d as RF  # noqa: E402
import rewrite_probe_66d as RP  # noqa: E402


def _emb(table):
    def f(text):
        v = np.asarray(table[text], dtype=float)
        return v / np.linalg.norm(v)
    return f


# ── 397 忠実性の組: 同じ目的の中だけ・R→O を先に・O→R で足す・同点は並びの前・消えた必要の候補 ─────
def test_t397_faithfulness_pairs_rule():
    O = [{"id": "1-1", "purpose": 1, "text": "o11"}, {"id": "1-2", "purpose": 1, "text": "o12"},
         {"id": "1-3", "purpose": 1, "text": "o13"}, {"id": "2-1", "purpose": 2, "text": "o21"}]
    R = [{"id": "r1-1", "purpose": 1, "text": "r11"}, {"id": "r1-2", "purpose": 1, "text": "r12"},
         {"id": "r2-1", "purpose": 2, "text": "r21"}]
    t = {"o11": [1, 0, 0], "o12": [0.9, 0.1, 0], "o13": [0, 0, 1], "o21": [0, 1, 0],
         "r11": [1, 0, 0], "r12": [1, 0.05, 0], "r21": [1, 0, 0]}          # r21 は目的1 の文に近いが、目的2 の中で組む
    res = RF.pairs(O, R, _emb(t), seed=6605, max_pairs=10)
    assert [(p["rewritten"], p["original"]) for p in res["r2o"]] == [("r1-1", "1-1"), ("r1-2", "1-1"), ("r2-1", "2-1")]
    assert res["picked"][:3] == res["r2o"]                                # R→O をすべて先に
    extra = [(p["rewritten"], p["original"]) for p in res["picked"][3:]]
    assert ("r1-2", "1-2") in extra or ("r1-1", "1-2") in extra          # O→R で足す（R→O に無い組だけ）
    assert ("r1-1", "1-1") not in extra
    assert res["vanished"] == ["1-2", "1-3"] and res["correspondence"]["r1-2"] == "1-1"
    small = RF.pairs(O, R, _emb(t), seed=6605, max_pairs=2)              # 枠より多い R→O は seed で選ぶ
    assert len(small["picked"]) == 2 and all(p["from"] == "R→O" for p in small["picked"])
    assert small["picked"] == RF.pairs(O, R, _emb(t), seed=6605, max_pairs=2)["picked"]


# ── 398 計測は要素の集合だけを差し替える（向かう先・門・背景・b はそのまま）・AUC ─────
def test_t398_rewrite_substitutes_elements_only():
    kaoru = {"purposes": [{"dest": "d1", "needs": [{"text": "a", "must": True}], "gate_s": 0.6, "gate_u": 0.3},
                          {"dest": "d2", "needs": [{"text": "b", "must": False}], "gate_s": 0.3, "gate_u": 0.3}],
             "offers": ["o"], "interests": []}
    rw = {"elements": [{"id": "r1-1", "purpose": 1, "text": "x", "type": "関わり方", "must": True},
                       {"id": "r1-2", "purpose": 1, "text": "y", "type": "資源", "must": True},
                       {"id": "r2-1", "purpose": 2, "text": "z", "type": "関わり方", "must": False}]}
    kr = RP.rewritten_kaoru(kaoru, rw)
    assert [n["text"] for n in kr["purposes"][0]["needs"]] == ["x", "y"] and kr["purposes"][1]["needs"][0]["text"] == "z"
    assert kr["purposes"][0]["dest"] == "d1" and kr["purposes"][0]["gate_s"] == 0.6 and kr["offers"] == ["o"]
    assert kaoru["purposes"][0]["needs"][0]["text"] == "a"                 # 元を書き換えない
    bg, el, el1 = {"bg": 1}, {"C": [{"target": "1-1", "text": "old", "src": "el-v2"}], "HN": {"a": [], "b": ["hb"]}}, {"E": 1}
    sets = RP.rewritten_sets((bg, el, el1), {"C": [{"target": "r1-2", "text": "c"}]}, [{"target": "r2-1", "text": "h"}])
    assert sets[0] is bg and sets[2] is el1 and sets[1]["HN"]["b"] == ["hb"]
    assert sets[1]["C"] == [{"target": "1-2", "text": "c", "src": "66d"}] and sets[1]["HN"]["a"] == [{"target": "2-1", "text": "h"}]
    assert el["C"][0]["text"] == "old"
    assert RP.auc([3, 4], [1, 2]) == 1.0 and RP.auc([1], [1]) == 0.5 and RP.auc([], [1]) is None
    assert RP.SCHEMES == ("R", "C2", "C2+b1", "C2+b2h", "C2+b2s", "C2+b3", "C2+b123")   # C6m・L を測らない
