#!/usr/bin/env python3
"""指示書76 順2 — 計測の結果（stage3a_run.py の JSON）を読んで、§3 の線の合否と、報告に足す数を出す（数えるだけ）。

- 記録した出力（段1・段2）を、同じパイプライン（段1 → 確かめ → 段2 → 確かめ）にもう一度通して（モデルは呼ばない）、
  落ちた理由を段ごと（段1 の組・段2 の ①・規則）に分ける。もう一度通した結果の種類が、記録と同じかも確かめる
- モデルが付けた関係（段1 の生の出力の組）と、ラベル（expl_label）の対応表を、件数で出す
- 文は出さない（件数・id・理由だけ）。手元のデータ（local/）は読まない
使い方: python3 scripts/stage3a_analyze.py <result.json>
"""
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")) if p not in sys.path]
import explanation_gen as G  # noqa: E402
import explanation_measure as M  # noqa: E402
import stage3a_run as R  # noqa: E402

LINES = {"errors_max": 1, "unrelated_errors_max": 0, "recall_min": 17, "rule_excluded_max": 0, "leak_max": 0}


def judge(res):
    """§3 の線（事前登録）の合否。1 回目の数で見る（決定性が保たれていれば、5 回とも同じ）。"""
    c = res["runs"][0]["counts"]
    det = res["determinism"]
    lines = {
        "1 誤り": {"value": f"{c['errors']['shown']}/{c['errors']['n']}・無関係 {res['unrelated']['shown']}/{res['unrelated']['n']}",
                 "pass": c["errors"]["shown"] <= LINES["errors_max"] and res["unrelated"]["shown"] <= LINES["unrelated_errors_max"]},
        "2 拾えた": {"value": f"{c['recall']['shown']}/{c['recall']['n']}", "pass": c["recall"]["shown"] >= LINES["recall_min"]},
        "3 規則で出さない": {"value": f"{c['rule_excluded']['shown']}/{c['rule_excluded']['n']}",
                        "pass": c["rule_excluded"]["shown"] <= LINES["rule_excluded_max"]},
        "4 原文の漏れ": {"value": str(sum(r["counts"]["leak"] for r in res["runs"])),
                     "pass": all(r["counts"]["leak"] <= LINES["leak_max"] for r in res["runs"])},
        "5 決定性": {"value": f"{det['runs']} 回・{det['same']}/{det['calls']} の呼び出しが同じ", "pass": bool(det["all_same"]) and det["runs"] >= 5},
    }
    return lines, all(v["pass"] for v in lines.values())


def _stage_of(problem):
    """落ちた理由の行を、段1 の組・段2 の ①・目的の説明（規則・組が無い）に分ける。"""
    if "① " in problem or "①を" in problem or "① が" in problem or "① を" in problem:
        return "段2（①）"
    if re.search(r"組 \d+ を出さない", problem):
        return "段1（組）"
    if "この目的の説明を出さない" in problem:
        return "目的（①・規則・組が無い）"
    return "そのほか"


def replay(res, run_index=0):
    """記録した出力を、もう一度パイプラインに通す（モデルは呼ばない）。組ごとの理由（段つき）と、モデルが付けた関係を返す。"""
    run = res["runs"][run_index]
    calls = {}
    for c in run["calls"]:
        calls.setdefault(c["id"], []).append(c)
    syn = R.load_synthetic()
    out = {}
    for pr, n, o in M.synthetic_items(syn):
        if pr["id"] not in calls:
            continue
        def backend(rec=calls[pr["id"]], pid=pr["id"]):
            seq = list(rec)

            def responder(stage, payload):
                c = seq.pop(0)
                assert c["stage"] == stage, (pid, c["stage"], stage)
                return c["output"]
            return G.FixedBackend(responder, model="replay")
        r = G.generate_direction(n, o, backend())
        kind = M.run_pair(n, o, backend())["result"]
        raw1 = G.parse_json(calls[pr["id"]][0]["output"])
        model_rels = [e.get("relation") for p in (raw1 or {}).get("purposes") or [] if isinstance(p, dict)
                      for e in p.get("elements") or [] if isinstance(e, dict)]
        reasons = []
        for prob in r["problems"]:
            st = _stage_of(prob)
            for why in M.drop_reasons([prob]):
                reasons.append((st, why))
        out[pr["id"]] = {"model_relations": model_rels, "reasons": reasons, "stage2": len(calls[pr["id"]]) > 1,
                         "replayed_kind": kind}
    return out


def summary(res):
    rep = replay(res)
    recs = {r["id"]: r for r in res["runs"][0]["records"]}
    syn = {p["id"]: p for p in R.load_synthetic()["pairs"]}
    # 段2 まで進んだ組と、段2 の確かめで落ちた組
    s2 = [i for i, r in recs.items() if r.get("stage2")]
    s2_dropped = [i for i in s2 if recs[i].get("result") != "d"]
    s2_reasons = Counter(why for i in s2_dropped for st, why in rep.get(i, {}).get("reasons", []) if st != "段1（組）")
    # 落ちた理由ごと（段つき・組の数ではなく、理由の行の数）
    all_reasons = Counter(f"{st}: {why}" for i in rep for st, why in rep[i]["reasons"])
    # モデルが付けた関係 × ラベル（組＝要素の数と、組のある対の数）
    table_el, table_pair = Counter(), Counter()
    for i, r in rep.items():
        lab = M.label_class(syn[i]["expl_label"])
        lab_key = "・".join(x for x in syn[i]["expl_label"] if x in M.RELS) if lab == "説明できる" else lab
        rels = r["model_relations"]
        for rel in rels:
            table_el[(lab_key, rel)] += 1
        table_pair[(lab_key, "・".join(sorted(set(rels))) if rels else "組なし")] += 1
    mismatch = [i for i, r in rep.items() if r["replayed_kind"] != recs[i]["result"]]
    return {"replay_mismatch": mismatch, "stage2_reached": len(s2), "stage2_dropped": len(s2_dropped), "stage2_reasons": dict(s2_reasons),
            "reasons": dict(all_reasons), "table_elements": {f"{a}|{b}": v for (a, b), v in sorted(table_el.items())},
            "table_pairs": {f"{a}|{b}": v for (a, b), v in sorted(table_pair.items())},
            "r2_predicate": sum(v for k, v in all_reasons.items() if "R2 なのに申告の表現が無い" in k),
            "r1_predicate": sum(v for k, v in all_reasons.items() if "R1 なのに経験の表現が無い" in k)}


def per_run_counts(res):
    return [{k: r["counts"][k] for k in ("errors", "recall", "rule_excluded", "relation_match", "kinds", "leak", "open")}
            for r in res["runs"]]


def timing(res):
    secs = [c["seconds"] for r in res["runs"] for c in r["calls"]]
    s1 = [c["seconds"] for r in res["runs"] for c in r["calls"] if c["stage"] == 1]
    s2 = [c["seconds"] for r in res["runs"] for c in r["calls"] if c["stage"] == 2]
    margins = [c["margin"]["min"] for r in res["runs"][:1] for c in r["calls"] if c["margin"]["min"] is not None]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    return {"calls": len(secs), "stage1_avg": avg(s1), "stage1_max": max(s1) if s1 else None, "stage2_avg": avg(s2),
            "run_seconds": [r["seconds"] for r in res["runs"]],
            "margin_min": min(margins) if margins else None,
            "margin_under_0_1_calls": sum(1 for r in res["runs"][:1] for c in r["calls"] if c["margin"]["under_0_1"])}


if __name__ == "__main__":
    res = json.load(open(sys.argv[1], encoding="utf-8"))
    lines, ok = judge(res)
    print(json.dumps({"lines": lines, "pass": ok, "per_run": per_run_counts(res), "summary": summary(res),
                      "timing": timing(res)}, ensure_ascii=False, indent=1))
