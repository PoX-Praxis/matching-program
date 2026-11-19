"""v2 の正解（固定・2026-11-19）。テスト 545。

545 v2_labels.json は固定した sha256 のまま。60 組の正解の数・V54 の決定・出どころのファイルの sha256 が記録のとおり
（v2 の文は、テストにも書かない）
"""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V2 = os.path.join(ROOT, "data", "explanation_bench", "v2")
FIXED = "2c47105e6425e4424d49d780dc59ee483b87274d515bf6e4883b98f2f28826cf"


def sha(name):
    return hashlib.sha256(open(os.path.join(V2, name), "rb").read()).hexdigest()


def test_t545_v2_labels_are_fixed():
    assert sha("v2_labels.json") == FIXED                                   # 固定の後は正解を変えない
    assert FIXED in open(os.path.join(V2, "README.md"), encoding="utf-8").read()
    rec = json.load(open(os.path.join(V2, "v2_labels.json"), encoding="utf-8"))
    for name, h in rec["sources"].items():
        assert sha(name) == h, name
    labels = rec["labels"]
    assert sorted(labels) == [f"V{i:02d}" for i in range(1, 61)]
    assert rec["counts"] == {"全部": 60, "説明できる": 25, "説明できない": 35,
                             "内訳": {"R1・現職": 2, "R1・経験": 6, "R1・継続": 2, "R2・申告": 5, "R3・使える立場": 2,
                                    "R3・所有": 3, "R4・関心": 5}}
    decided = [i for i, v in labels.items() if v["決め方"] != "v2_items と GPT が一致"]
    assert decided == ["V54"] and set(rec["KAORU の決定"]) == {"V54"}
    assert (labels["V54"]["label"], labels["V54"]["sub"]) == ("R4", "関心")
    items = {x["id"]: x for x in json.load(open(os.path.join(V2, "v2_items.json"), encoding="utf-8"))["items"]}
    for i, v in labels.items():
        if i != "V54":
            assert (v["label"], v["sub"]) == (items[i]["label"], items[i]["sub"]), i
