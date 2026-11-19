#!/usr/bin/env python3
"""指示書78 順1 — 表 v0.3.2-dev（機械の読み M1〜M4・F1）を、60 組・gates_v1.2・v2（探索）で LLM なしで測る。

- 合否は決めない（数を出すだけ）。v0.3.1-dev（凍結）と比べて、変わった組を挙げる
- v2 は 77b 順1 で合否を固定した後に「探索」として開いたもの。記録に v2 の文（引用）を書いてよい
- ［A］は機械の［A］（a_rules_v1）。v2 で出た説明の、生成の ① と型の ① の数も出す
使い方:
  python3 scripts/t78_measure.py      # 測って記録を書く（標準出力は数だけ）
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")) if p not in sys.path]
import machine_a as MA  # noqa: E402
import match_table as MT  # noqa: E402
import t77a_measure as T  # noqa: E402
import t77b_measure as M  # noqa: E402

V_NEW, V_OLD = "v0.3.2-dev", "v0.3.1-dev"
RECORD = os.path.join(ROOT, "data", "explanation_bench", "runs", "78_match_tables_v0.3.2-dev.json")
DOC = os.path.join(ROOT, "docs", "match_tables_v0.3.2-dev.md")


def _key(r):
    return {"通った": r["passed"], "関係": r.get("relation"), "C の種類": r.get("kind"), "引用": r.get("clause"),
            "段": r.get("where"), "理由": r.get("why")}


def changed_pairs(items, old, new, gold):
    """v0.3.1-dev と v0.3.2-dev で、通ったか・関係・［C］の種類・引用・段・理由のどれかが変わった組。"""
    out = []
    for it in items:
        a, b = _key(old[it["id"]]), _key(new[it["id"]])
        if a != b:
            out.append({"id": it["id"], "正解": gold(it), "変わったもの": [k for k in a if a[k] != b[k]],
                        V_OLD: a, V_NEW: b})
    return out


def counts_60_gates(c):
    c60, cg = c["60"], c["gates"]
    return {"60": {"出した": c60["出した"], "誤り": f"{c60['誤り']['出した']}/{c60['誤り']['分母']}",
                   "拾えた": f"{c60['拾えた']['出した']}/{c60['拾えた']['分母']}", "関係も一致": c60["拾えた"]["関係も一致"],
                   "誤りの組": [x["id"] for x in c60["誤り"]["組"]],
                   "落ちた説明できる組": [x["id"] for x in c60["落ちた場所（説明できる組）"]]},
            "gates": {"負例": f"{cg['負例']['落とした']}/{cg['負例']['組']} を落とした",
                      "通した負例": [x["id"] for x in cg["負例"]["組ごと"] if not x["落とした"]],
                      "正例": f"{sum(1 for x in cg['正例'] if x['期待どおり'])}/{len(cg['正例'])} 期待どおり",
                      "期待どおりでない正例": [x["id"] for x in cg["正例"] if not x["期待どおり"]]}}


def v2_block(items, res):
    c = M.count(items, res)
    still = [r for r in c["rows"] if M.explainable({"gold": r["正解"], "purpose": r["目的"]}) and not r["通った"]]
    neg = [r for r in c["rows"] if r["正解"] == "説明できない"]
    by_stage = {}
    for r in still:
        by_stage[r["段"]] = by_stage.get(r["段"], 0) + 1
    neg_stage = {}
    for r in neg:
        k = "通した" if r["通った"] else r["段"]
        neg_stage[k] = neg_stage.get(k, 0) + 1
    return {"線1（誤り・機械で数えた分）": c["線1"]["誤り（機械で数えた分）"], "誤りの組": c["線1"]["組"],
            "線2（説明が出た）": f"{c['線2']['分子']}/{c['線2']['分母']}", "線2 の割合": c["線2"]["割合"],
            "出た説明": c["線3（記録だけ）"], "領域ごと": c["領域ごと（記録だけ）"], "取りちがえの表": c["取りちがえの表"],
            "まだ落ちる説明できる組": {"数": len(still), "段ごと": dict(sorted(by_stage.items())),
                                 "組": [{"id": r["id"], "正解": f"{r['正解']}・{r['正解の sub']}", "段": r["段"]} for r in still]},
            "説明できない組の段": dict(sorted(neg_stage.items())),
            "rows": [{k: r[k] for k in ("id", "領域", "目的", "正解", "正解の sub", "通った", "段", "関係", "C の種類",
                                         "出し方", "変換", "型の理由", "誤り")} for r in c["rows"]]}


def measure():
    items = T.load_items(T.GATES_V12)
    new = T.run_all(items, "split", version=V_NEW, words="lemma")
    old = T.run_all(items, "split", version=V_OLD, words="lemma")
    again = T.run_all(items, "split", version=V_NEW, words="lemma")
    c_new, c_old = T.summarize(items, new), T.summarize(items, old)
    v2 = M.load_v2()
    v2_new, v2_old = M.run_once(v2, version=V_NEW), M.run_once(v2, version=V_OLD)
    v2_again = M.run_once(v2, version=V_NEW)
    gold60 = lambda it: it["gold"] + (f"（{it['gold_sub']}）" if it.get("gold_sub") else "")  # noqa: E731
    gold2 = lambda it: f"{it['gold']}・{it['gold_sub']}"  # noqa: E731
    v2_changed = changed_pairs(v2, {i: {**x, "why": None} for i, x in v2_old.items()},
                               {i: {**x, "why": None} for i, x in v2_new.items()}, gold2)
    for x in v2_changed:
        for v in (V_OLD, V_NEW):
            x[v].pop("理由", None)
        a = (v2_new[x["id"]]["a"] or {})
        x["出し方"] = a.get("出し方")
        x["変換"] = a.get("変換")
        x["［A］"] = a.get("A")
    return {
        "version": "78-1", "日付": "2026-11-19",
        "表": {"版": V_NEW, "files": MT.table_digest(V_NEW), "文書": hashlib.sha256(open(DOC, "rb").read()).hexdigest(),
               "比べた版": V_OLD, "比べた版の files": MT.table_digest(V_OLD)},
        "［A］": MA.VERSION, "words": "lemma", "tools": MT.tool_versions(), "gates": os.path.basename(T.GATES_V12),
        "v2 の扱い": "探索（77b 順1 で合否を固定した後に開いた）。合否は決めない",
        "60 組と gates": {V_NEW: counts_60_gates(c_new), V_OLD: counts_60_gates(c_old),
                         "変わった組": changed_pairs(items, old, new, gold60)},
        "v2": {V_NEW: v2_block(v2, v2_new), V_OLD: {k: v for k, v in v2_block(v2, v2_old).items() if k != "rows"},
               "変わった組": v2_changed},
        "決定性": {"60 組と gates": json.dumps(T.slim_v03(new), ensure_ascii=False, sort_keys=True)
                   == json.dumps(T.slim_v03(again), ensure_ascii=False, sort_keys=True),
                   "v2": M.digest(v2_new) == M.digest(v2_again)},
    }


def main():
    rec = measure()
    with open(RECORD, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
        f.write("\n")
    for v in (V_OLD, V_NEW):
        g = rec["60 組と gates"][v]
        b = rec["v2"][v]
        print(v, "| 60 誤り", g["60"]["誤り"], "拾えた", g["60"]["拾えた"], "関係も一致", g["60"]["関係も一致"],
              "| gates 負例", g["gates"]["負例"], "正例", g["gates"]["正例"],
              "| v2 誤り", b["線1（誤り・機械で数えた分）"], "説明", b["線2（説明が出た）"],
              "まだ落ちる", b["まだ落ちる説明できる組"]["段ごと"])
    print("変わった組 60+gates:", [x["id"] for x in rec["60 組と gates"]["変わった組"]])
    print("変わった組 v2:", [x["id"] for x in rec["v2"]["変わった組"]])
    print("決定性:", rec["決定性"])


if __name__ == "__main__":
    main()
