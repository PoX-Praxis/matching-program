"""指示書66 順4 — 背景文集 bg-v2 と正解集合 el-v2（テスト 370〜372）。

文集は registration_v2 §2・§3 の条件だけを渡した新しい会話が書いた。ここでは、件数・分け方・出どころの記録が
registration_v2 のとおりであること、bg-v1・el-v1 を残していること、本人（KAORU）の文が入っていないことを確かめる。
"""
import json
import os
import random
import re
from collections import Counter

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "scripts", "probe_data")


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def _reg_blocks():
    with open(os.path.join(DATA, "registration_v2.md"), encoding="utf-8") as f:
        return re.findall(r"```\n(.*?)\n```", f.read(), flags=re.S)


OFFER_TYPES = ["してきた。", "を担ってきた。", "できる。", "の立場で関われる。", "を手伝える。", "を引き受けてきた。"]
OFFER_ORDER = ["の立場で関われる。", "を手伝える。", "を引き受けてきた。", "を担ってきた。", "できる。", "してきた。"]


# ── 370 bg-v2: 40 分野・束ごとの文末の型の数・型ごとに 1 : 3 の分け方・条件の全文 ─────────────────────
def test_t370_background_v2_sections_split_and_provenance():
    bg = _load("background_v2.json")
    assert bg["version"] == "bg-v2" and "i mod 4 = 0" in bg["split"]
    pv, sec = bg["provenance"], bg["sections"]
    assert "新しい会話" in pv["how"] and "第三者の著作物を含まない" in pv["license"]
    blocks = _reg_blocks()
    assert pv["offer"]["template"] in blocks and pv["need"]["template"] in blocks and pv["interest"]["template"] in blocks
    off = sec["offer"]
    assert len(off) == pv["offer"]["used"] and len({x["text"] for x in off}) == len(off)
    assert len({x["domain"] for x in off}) == 40 and [b["batch"] for b in pv["offer"]["batches"]] == list(range(1, 41))
    for b in pv["offer"]["batches"]:                                 # 束 k では ((k−1) mod 6)+1 番目から 4 つが 17
        q = [b["quotas"][t] for t in OFFER_TYPES]
        p = (b["batch"] - 1) % 6
        assert q == [17 if (i - p) % 6 < 4 else 16 for i in range(6)]
    for x in off:                                                    # 型は §2-1 の順に当てたもの
        assert x["type"] == next(e for e in OFFER_ORDER if x["text"].endswith(e))
    for t in OFFER_TYPES:                                            # 型ごとに（束, 位置）の順で i mod 4 = 0 が推定用
        lst = sorted((x for x in off if x["type"] == t), key=lambda x: (x["batch"], x["pos"]))
        assert [x["split"] for x in lst] == ["est" if i % 4 == 0 else "val" for i in range(len(lst))]
    n_est = sum(x["split"] == "est" for x in off)
    assert 900 <= n_est <= 1100 and 2700 <= len(off) - n_est <= 3000
    need = sec["need"]
    assert len(need) == pv["need"]["used"] and all(x["split"] == "est" for x in need) and 900 <= len(need) <= 1000
    assert [b["domains"] for b in pv["need"]["batches"]][0] == ["料理・飲食", "経理・事務"]
    it = sec["interest"]
    assert len(it) == pv["interest"]["used"] and len({x["domain"] for x in it}) == 40
    for k in range(1, 41):
        lst = sorted((x for x in it if x["batch"] == k), key=lambda x: x["pos"])
        assert [x["split"] for x in lst] == ["est" if i % 4 == 0 else "val" for i in range(len(lst))]
    bg1 = _load("background_v1.json")                                # bg-v1 は残す
    assert bg1["version"] == "bg-v1" and len(bg1["sections"]["offer"]) == 300


# ── 371 el-v2: C 群 36 文（el-v1 の 12 ＋ 足した 24）・hard negative 60・抜き取り（seed 6604）───────────────
def test_t371_element_sets_v2_c_hn_and_spot_check():
    el, el1 = _load("element_sets_v2.json"), _load("element_sets_v1.json")
    assert el["version"] == "el-v2"
    keys = {"1-1", "1-2", "1-3", "1-4", "2-1", "2-2"}
    c = el["C"]
    assert [x for x in c if x["src"] == "el-v1"] == [dict(x, src="el-v1") for x in el1["C"]]
    added = [x for x in c if x["src"] == "el-v2"]
    assert len(added) == 24 and Counter(x["target"] for x in added) == {k: 4 for k in keys}
    assert len(c) == 36 and Counter(x["target"] for x in c) == {k: 6 for k in keys}
    hn = el["HN"]
    assert len(hn["a"]) == 30 and Counter(x["target"] for x in hn["a"]) == {k: 5 for k in keys} and len(hn["b"]) == 30
    sc = el["spot_check"]
    assert sc["seed"] == 6604
    assert sc["C_added_index"] == sorted(random.Random(6604).sample(range(24), 10))
    assert sc["HN_index"] == sorted(random.Random(6604).sample(range(60), 10))
    blocks = _reg_blocks()
    for k in ("C", "HN"):
        p = el["provenance"][k]["prompt"]
        assert "リポジトリに入れない" in p and "{1-1〜2-2 の 6 文}" not in p
        assert any(b.replace("{1-1〜2-2 の 6 文}", "") == p.replace(p[p.index("（KAORU"):p.index("入れない）") + 5], "")
                   for b in blocks)                                   # 本人の文を伏せたほかは registration_v2 の全文


# ── 372 本人の文を入れない・手元のファイルをコミットしない ────────────────────────────────────────────
def test_t372_no_personal_text_committed():
    blob = "".join(open(os.path.join(DATA, n), encoding="utf-8").read()
                   for n in ("background_v2.json", "element_sets_v2.json"))
    assert "求めている相手の像:\\n1-1" not in blob                     # 番号つきの像の文を渡した形のままではない
    local = os.path.join(ROOT, "local", "kaoru_v5_public.json")
    if not os.path.exists(local):
        pytest.skip("本人の文は手元（local/）にだけある")
    import sys
    sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]
    import element_probe as EP
    k = EP.load_kaoru(local)
    for p in k["purposes"]:
        for n in p["needs"]:
            assert n["text"] and n["text"] not in blob
