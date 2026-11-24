"""架空の組（指示書80 §2）：data/explanation_bench/synthetic_v1.json の架空の人と相手の文から、二人の構造化の JSON を作る。
部品の数が違う 3 組（多い・中くらい・少ない）。実在の人の文は含まない。現状の文（縛られているもの など）は、ここで書いた架空の文。

- 相手の文（other）の置き場所：R3 は持っているもの、R4・判断できない は関心、ほかは与え像（R1 は型「経験」、ほかは「申告」）
- 一人目は二人目の必要像への相手の文を、二人目は一人目の必要像への相手の文を持つ（互いに相手の文を持つ）
- 少ない組の二人目は、目的の欄の無い人（v4 の形：必要像と現状だけ）"""
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SOURCE = os.path.join(_ROOT, "data", "explanation_bench", "synthetic_v1.json")
# (組の番号, 一人目, 二人目, 相手の文を何文ずつ使うか, 二人目を v4 の形にするか)
PLAN = (("synL", "P1", "P2", 10, False), ("synM", "P3", "P4", 10, False), ("synS", "P5", "P6", 2, True))
STATE = {  # 架空の現状（ここで書いた文）
    "P1": {"縛られているもの": "平日の日中は本業があり、動けるのは週末だけです。", "持っているもの": "果樹園の一角にある、使っていない作業小屋。"},
    "P2": {"縛られているもの": "場所を借りる費用が、まだ足りていません。", "できること_型": "子どもの話を急かさずに聞くこと。"},
    "P3": {"縛られているもの": "工場の機械は、決まった人しか動かしてはいけない決まりです。"},
    "P4": {"未分類": "古い写真を家族から預かっている。"},
    "P5": {"縛られているもの": "音響の機材を買う予算がありません。"},
    "P6": {"持っているもの": "地域の集会所を、月に二回借りられる。", "できること_型": "スマートフォンの基本の操作を、ゆっくり教えること。",
           "縛られているもの": "車の運転ができません。"},
}


def _others(src, pid, n):
    return [x for x in src["pairs"] if x["person"] == pid][:n]


def _place(others):
    offer, interest, have = [], [], []
    for x in others:
        lab = x["expl_label"]
        if "R3" in lab:
            have.append(x["other"])
        elif "R4" in lab or "判断できない" in lab:
            interest.append({"文": x["other"]})
        else:
            offer.append({"型": "経験" if "R1" in lab else "申告", "文": x["other"]})
    return offer, interest, have


def person(src, pid, gives, v4=False):
    p = next(x for x in src["people"] if x["id"] == pid)
    offer, interest, have = _place(gives)
    st = dict(STATE.get(pid, {}))
    if have:
        st["持っているもの"] = "".join(([st["持っているもの"]] if st.get("持っているもの") else []) + have)
    needs = [{"文": n["text"], "必須": bool(n["必須"]), "型": "経験"} for n in p["needs"]]
    if v4:
        return {"id": f"syn-{pid}", "schema_version": "v4", "必要像": needs, "現状": st}
    return {"id": f"syn-{pid}", "schema_version": "v5",
            "purposes": [{"purpose_id": "p1", "向かう先": p["purpose"], "手段": "", "必要像": needs}],
            "与え像": offer, "関心": interest, "現状": st, "supporting_material": {}}


def pairs(src=None):
    """架空の 3 組の spec（runner.run_pair に渡す形）。"""
    src = src or json.load(open(SOURCE, encoding="utf-8"))
    out = []
    for sid, pa, pb, n, v4 in PLAN:
        a = person(src, pa, _others(src, pb, n))
        b = person(src, pb, _others(src, pa, n), v4=v4)
        out.append({"id": sid, "a": a, "b": b, "names": [f"架空{pa}", f"架空{pb}"],
                    "people": {"A": {"name": f"架空{pa}", "tag": f"synthetic-{pa}"}, "B": {"name": f"架空{pb}", "tag": f"synthetic-{pb}"}}})
    return out
