#!/usr/bin/env python3
"""指示書77b 順1 — 照合の表 v0.3.1-dev ＋ 機械の［A］（a_rules_v1）で v2 を測る（LLM なし・GPU なし）。

- 合否の線は事前登録（docs/prereg/v2_lines_2026-11-19.md・確定・封印）の §2。1 回目が本番。同じ入力で 3 回流す
  （1 回目はこのプロセス、2・3 回目は別のプロセス）
- 記録（runs/77b_v2_machineA.json）には v2 の文を書かない。id・数・区分と、① の文の sha256 だけ
- v2 の ① の文は、KAORU 用のファイル（v2/77b_sentences_for_KAORU.md）にだけ書く。標準出力にも v2 の文は出さない
使い方:
  python3 scripts/t77b_measure.py            # 測って、記録と KAORU 用のファイルを書く（標準出力は数だけ）
  python3 scripts/t77b_measure.py --digest   # 1 回流して、結果一式の sha256 だけを出す（3 回の確かめ）
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")) if p not in sys.path]
import machine_a as MA  # noqa: E402
import match_table as MT  # noqa: E402

BENCH = os.path.join(ROOT, "data", "explanation_bench")
V2_ITEMS = os.path.join(BENCH, "v2", "v2_items.json")
V2_LABELS = os.path.join(BENCH, "v2", "v2_labels.json")
V2_LABELS_SHA256 = "2c47105e6425e4424d49d780dc59ee483b87274d515bf6e4883b98f2f28826cf"   # 固定（2026-11-19）
EXAMPLES = os.path.join(BENCH, "examples_v1.json")
PREREG = os.path.join(ROOT, "docs", "prereg", "v2_lines_2026-11-19.md")
PREREG_SHA256 = "17d60c6c14785c12e7b3f41fc2c30fb0df6ef560a83e8efe9b75a515bd2c91a8"     # 確定・封印
INSTRUCTION = os.path.join(ROOT, "docs", "instructions", "77b.md")
RECORD = os.path.join(BENCH, "runs", "77b_v2_machineA.json")
KAORU_FILE = os.path.join(BENCH, "v2", "77b_sentences_for_KAORU.md")
VERSION = "v0.3.1-dev"
N_WORD, O_WORD = "あなた", "〇〇さん"          # お手本（examples_v1）の ① と同じ呼び方
POSITIVE = ("E01", "E02", "E03", "E04", "E05", "E06", "E07")
LINES = {"1": 1, "2": 0.5}                       # 事前登録 §2: 誤り 1 件以下・説明できる組の 50% 以上


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def rel(path):
    return os.path.relpath(path, ROOT)


# ── 単体の試験（お手本の肯定 7 組）──────────────────────────────────────────────────────────
def examples_check():
    ex = json.load(open(EXAMPLES, encoding="utf-8"))
    rows = []
    for e in ex["items"]:
        if e["id"] not in POSITIVE:
            continue
        r, a, s = MA.explain(e["need"], e["offer"], version=VERSION, N=N_WORD, O=O_WORD)
        rows.append({"id": e["id"], "表を通った": r["passed"], "関係": r.get("relation"), "C の種類": r.get("kind"),
                     "お手本の表": [e["table"]["relation"], e["table"]["C_kind"]],
                     "offer_quote がお手本と同じ": r.get("clause") == e["stage1"]["proposals"][0]["offer_quote"],
                     "［A］": a["A"] if a else None, "お手本の［A］": e["stage2"]["A"],
                     "［A］が一字も違わない": bool(a) and a["A"] == e["stage2"]["A"],
                     "変換": a["変換"] if a else None, "① がお手本と同じ": s == e["sentence"]})
    return rows


# ── v2 ──────────────────────────────────────────────────────────────────────────────────
def load_v2():
    if sha256(V2_LABELS) != V2_LABELS_SHA256:
        raise SystemExit("v2_labels.json が固定の sha256 と違う（正解は変えない）")
    labels = json.load(open(V2_LABELS, encoding="utf-8"))["labels"]
    items = []
    for x in json.load(open(V2_ITEMS, encoding="utf-8"))["items"]:
        lab = labels[x["id"]]
        items.append({"id": x["id"], "need": x["need"], "offer": x["offer"], "purpose": x["purpose"], "area": x["area"],
                      "gold": lab["label"], "gold_sub": lab["sub"]})
    return items


def run_once(items, version=VERSION):
    out = {}
    for it in items:
        r, a, s = MA.explain(it["need"], it["offer"], version=version, N=N_WORD, O=O_WORD)
        out[it["id"]] = {"passed": r["passed"], "where": r.get("where"), "relation": r.get("relation"), "kind": r.get("kind"),
                         "clause": r.get("clause"), "B": r["need"].get("B"), "C": r.get("C"), "short": r.get("short"),
                         "joined": r.get("joined"), "a": a, "sentence": s}
    return out


def digest(results):
    return hashlib.sha256(json.dumps(results, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def shown_on_screen(it, x):
    """画面の規則: 補完の目的で R4 だけの組は出さない（60 組と同じ）。"""
    return x["passed"] and not (x["relation"] == "R4" and it["purpose"] == "補完")


def explainable(it):
    """線 2 の分母: 説明できる組のうち、画面に出るはずの組（補完の目的で R4 だけの組を除く）。"""
    return it["gold"] != "説明できない" and not (it["gold"] == "R4" and it["purpose"] == "補完")


def error_of(it, x):
    if not x["passed"]:
        return None
    if it["gold"] == "説明できない":
        return "説明できない組に出した"
    if x["relation"] != it["gold"]:
        return "関係が違う"
    if x["kind"] != it["gold_sub"]:
        return "C の種類が違う"
    return None


def line_counts(items, res):
    errs = [{"id": it["id"], "誤り": error_of(it, res[it["id"]]), "画面に出る": shown_on_screen(it, res[it["id"]])}
            for it in items if error_of(it, res[it["id"]])]
    den = [it for it in items if explainable(it)]
    num = [it for it in den if res[it["id"]]["passed"]]
    return {"線1": {"誤り（機械で数えた分）": len(errs), "組": errs,
                   "画面に出る組だけ": sum(1 for e in errs if e["画面に出る"])},
            "線2": {"分子": len(num), "分母": len(den), "割合": round(len(num) / len(den), 4) if den else None,
                   "出なかった組": [{"id": it["id"], "正解": f"{it['gold']}・{it['gold_sub']}", "段": res[it["id"]]["where"]}
                                for it in den if not res[it["id"]]["passed"]]}}


def count(items, res):
    rows = []
    for it in items:
        x = res[it["id"]]
        a = x["a"] or {}
        rows.append({"id": it["id"], "領域": it["area"], "目的": it["purpose"], "正解": it["gold"], "正解の sub": it["gold_sub"],
                     "通った": x["passed"], "段": x["where"], "関係": x["relation"], "C の種類": x["kind"],
                     "つないだ": x["joined"] or False, "画面に出る": shown_on_screen(it, x),
                     "出し方": a.get("出し方"), "変換": a.get("変換"), "型の理由": a.get("型の理由"),
                     "残っていた形": a.get("残っていた形"), "［A］の字数": len(a["A"]) if a.get("A") else None,
                     "offer_quote の字数": len(x["clause"]) if x["clause"] else None,
                     "① の sha256": hashlib.sha256(x["sentence"].encode("utf-8")).hexdigest() if x["sentence"] else None,
                     "誤り": error_of(it, x)})
    lines = line_counts(items, res)
    passed = [r for r in rows if r["通った"]]
    gen = [r for r in passed if r["出し方"] == "生成"]
    reasons = {}
    for r in passed:
        for n in r["型の理由"] or []:
            reasons[str(n)] = reasons.get(str(n), 0) + 1
    area = {}
    for name in sorted({it["area"] for it in items}):
        sub = [it for it in items if it["area"] == name]
        lc = line_counts(sub, res)
        area[name] = {"組": len(sub), "線1": lc["線1"]["誤り（機械で数えた分）"], "線1 の組": [e["id"] for e in lc["線1"]["組"]],
                      "線2": f"{lc['線2']['分子']}/{lc['線2']['分母']}", "線2 の割合": lc["線2"]["割合"]}
    conf = {}
    for it in items:
        x = res[it["id"]]
        k = f"{it['gold']}|{x['relation'] if x['passed'] else '落とした'}"
        conf[k] = conf.get(k, 0) + 1
    return {"組": len(items), "通った": len(passed), "画面に出る": sum(1 for r in rows if r["画面に出る"]),
            "線1": lines["線1"], "線2": lines["線2"],
            "線3（記録だけ）": {"生成の ①": len(gen), "型の ①": len(passed) - len(gen), "出た説明": len(passed),
                           "生成の割合": round(len(gen) / len(passed), 4) if passed else None,
                           "型の ① の理由（§3 の番号・重なりあり）": dict(sorted(reasons.items())),
                           "型の ① の組": [{"id": r["id"], "理由": r["型の理由"], "残っていた形": r["残っていた形"]}
                                         for r in passed if r["出し方"] == "型"],
                           "変換": {k: sum(1 for r in gen if r["変換"] == k) for k in sorted({r["変換"] for r in gen})}},
            "領域ごと（記録だけ）": area, "取りちがえの表": dict(sorted(conf.items())),
            "C の種類（通った組）": {k: sum(1 for r in passed if r["C の種類"] == k) for k in sorted({r["C の種類"] for r in passed})},
            "rows": rows}


# ── 60 組（開発用・参考）────────────────────────────────────────────────────────────────
def dev60():
    import t77a_measure as T
    items = [it for it in T.load_items(T.GATES_V12) if it["set"] == "60"]
    rows = []
    for it in items:
        r, a, s = MA.explain(it["need"], it["offer"], version=VERSION)
        if a is None:
            continue
        rows.append({"id": it["id"], "関係": r["relation"], "C の種類": r["kind"], "出し方": a["出し方"], "変換": a["変換"],
                     "型の理由": a["型の理由"], "詳しさ": a["詳しさ"], "offer_quote": r["clause"], "［A］": a["A"]})
    return {"通った": len(rows), "生成の ①": sum(1 for r in rows if r["出し方"] == "生成"),
            "型の ①": sum(1 for r in rows if r["出し方"] == "型"), "組": rows}


# ── 記録 ────────────────────────────────────────────────────────────────────────────────
def subprocess_digest():
    p = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "--digest"], capture_output=True, text=True,
                       cwd=ROOT, check=True)
    return p.stdout.strip()


def measure(runs=3):
    if sha256(PREREG) != PREREG_SHA256:
        raise SystemExit("事前登録の写しが封印の sha256 と違う")
    ex = examples_check()
    if not all(x["［A］が一字も違わない"] for x in ex):
        raise SystemExit("単体の試験で、お手本の［A］と違う組がある（止めて報告する）")
    items = load_v2()
    first = run_once(items)                                            # 1 回目が本番
    digests = [digest(first)] + [subprocess_digest() for _ in range(runs - 1)]
    c = count(items, first)
    same = len(set(digests)) == 1
    l1 = c["線1"]["誤り（機械で数えた分）"]
    l2 = c["線2"]["割合"]
    verdict = {"線1": {"線": "1 件以下", "値": l1, "合": l1 <= LINES["1"],
                      "注": "機械で数えたのは「説明できない組に出した」「関係・C の種類が正解と違う」。"
                            "［A］の言い過ぎ・主語や条件の取り違えは KAORU が KAORU 用のファイルで目で確かめる（増えれば合否が変わりうる）"},
               "線2": {"線": "50% 以上", "値": f"{c['線2']['分子']}/{c['線2']['分母']}", "割合": l2, "合": l2 is not None and l2 >= LINES["2"]},
               "線4": {"線": "3 回とも同じ", "値": same, "合": same}}
    verdict["合否（機械で数えた分）"] = "合" if all(v["合"] for k, v in verdict.items() if k.startswith("線")) else "否"
    if not verdict["線2"]["合"] or not verdict["線4"]["合"]:
        verdict["線1"]["注"] += "。ただし線 2 か線 4 で否が決まっているので、目の確かめで合否は変わらない"
    return {"version": "77b-1", "日付": "2026-11-19",
            "対象": {"照合の表": VERSION, "［A］": MA.VERSION, "words": "lemma", "LLM": "使わない（順2 で参考に測る）"},
            "sha256": {"表": MT.table_digest(VERSION), "規則（a_rules_v1）": MA.rules_digest(),
                       "正解（v2_labels.json）": sha256(V2_LABELS), "v2_items.json": sha256(V2_ITEMS),
                       "お手本（examples_v1.json）": sha256(EXAMPLES), "事前登録": sha256(PREREG), "指示書": sha256(INSTRUCTION)},
            "files": {"表": f"data/match_tables/{VERSION}/", "規則": rel(MA.RULES_PATH), "正解": rel(V2_LABELS),
                      "お手本": rel(EXAMPLES), "事前登録": rel(PREREG), "KAORU 用": rel(KAORU_FILE)},
            "tools": MT.tool_versions(),
            "単体の試験（お手本の肯定 7 組）": {"すべて一字も違わない": all(x["［A］が一字も違わない"] for x in ex),
                                         "① もお手本と同じ": all(x["① がお手本と同じ"] for x in ex), "組": ex},
            "60 組（参考）": dev60(),
            "v2": {"1 回目が本番": True, "3 回の sha256": digests, "3 回とも同じ": same, "数": c},
            "合否": verdict,
            "報告の決まり": "この記録に v2 の文は無い（id・数・区分と、① の文の sha256 だけ）。① の文は KAORU 用のファイルにだけ書いた"}, first, items


def kaoru_file(items, res):
    """KAORU 用（Claude には渡さない）。v2 で出た ① の文を全部。"""
    out = ["# 77b 順1 — v2 で出た ① の文（KAORU 用・Claude には渡さない）", "",
           "照合の表 v0.3.1-dev ＋ 機械の［A］（a_rules_v1）。1 回目（本番）の結果。呼び方はお手本と同じ（{O}=〇〇さん・{N}=あなた）。",
           "目で確かめること：意味の歪み・読みにくさ（とくに条件の入った［A］）・［A］の言い過ぎ・主語や条件の取り違え。", "",
           "- 「機械で数えた誤り」は、正解が説明できないのに出した・関係か C の種類が正解と違う、の 2 つだけ",
           "- 「画面に出ない」は、補完の目的で R4 だけの組（画面の規則で出さない）", ""]
    n = 0
    for it in items:
        x = res[it["id"]]
        if not x["passed"]:
            continue
        n += 1
        a = x["a"]
        how = "生成の ①" if a["出し方"] == "生成" else f"型の ①（§3 の {'・'.join(map(str, a['型の理由']))}）"
        err = error_of(it, x)
        out += [f"## {it['id']}（{it['area']}・{it['purpose']}）", "",
                f"- 正解：{it['gold']}・{it['gold_sub']}　／　表：{x['relation']}・{x['kind']}"
                + (f"　／　**機械で数えた誤り：{err}**" if err else "")
                + ("" if shown_on_screen(it, x) else "　／　画面に出ない"),
                f"- 必要：{it['need']}", f"- 相手：{it['offer']}", f"- {how}：{x['sentence']}",
                "- 確かめ：□ 意味の歪み　□ 読みにくい　□ 言い過ぎ　□ 主語の取り違え　□ 条件の取り違え", ""]
    out.insert(7, f"出た ① は {n} 組。")
    out.insert(8, "")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true")
    args = ap.parse_args()
    if args.digest:
        print(digest(run_once(load_v2())))
        return
    rec, first, items = measure()
    with open(RECORD, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(KAORU_FILE, "w", encoding="utf-8") as f:
        f.write(kaoru_file(items, first))
    v = rec["合否"]
    c = rec["v2"]["数"]
    print("単体の試験:", rec["単体の試験（お手本の肯定 7 組）"]["すべて一字も違わない"],
          "| 60 組（参考）生成/型:", rec["60 組（参考）"]["生成の ①"], rec["60 組（参考）"]["型の ①"])
    print("線1:", v["線1"]["値"], [e["id"] for e in c["線1"]["組"]], "| 線2:", v["線2"]["値"], v["線2"]["割合"],
          "| 線4:", v["線4"]["値"], "| 合否:", v["合否（機械で数えた分）"])
    print("線3: 生成", c["線3（記録だけ）"]["生成の ①"], "型", c["線3（記録だけ）"]["型の ①"],
          "理由", c["線3（記録だけ）"]["型の ① の理由（§3 の番号・重なりあり）"])
    print("領域:", json.dumps(c["領域ごと（記録だけ）"], ensure_ascii=False))


if __name__ == "__main__":
    main()
