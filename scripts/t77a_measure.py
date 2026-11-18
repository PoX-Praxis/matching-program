#!/usr/bin/env python3
"""指示書77a — 照合の表 v0.2 を、LLM を使わずに単体で測る（60 組の正解 r2・最小対 gates_v1）。

- 照合は src/match_table.py（表は data/match_tables/v0.2/）。句に分ける形（split・77a §2-2 の 1）を本計測にし、
  文を 1 つの句とする形（whole）を、句の切り方で結果が変わった文を見つけるための診断として並べる
- nomic は「対象の句どうし」の近さだけに使う（表 §6 の 2）。線は、60 組と gates_v1 の負例が 0 になる、いちばん低い線（機械的に決める）
- 文はすべて架空（synthetic_v1・gates_v1）。KAORU と PA の文・local/ は読まない
使い方:
  python3 scripts/t77a_measure.py --nomic            # nomic を手元で読み込んで値を出し、記録を書く
  python3 scripts/t77a_measure.py --sims-from <記録>  # 記録にある nomic の値で、もう一度計算する（モデルは読まない）
  python3 scripts/t77a_measure.py --version v0.3 [--nomic | --sims-from <記録>]   # 追補1: 表 v0.3・gates_v1.1
追補1（表 v0.3）: 主は「辞書形で当てる」形、比べは「表記のままで当てる」形。nomic は判定に使わず、
「概念不明」のログに参考の近さだけを残す（数値は記録のファイルだけ。画面・公開 API には出さない）。
"""
import argparse
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (ROOT, os.path.join(ROOT, "src")) if p not in sys.path]
import match_table as MT  # noqa: E402

SYN = os.path.join(ROOT, "data", "explanation_bench", "synthetic_v1.json")
R2 = os.path.join(ROOT, "data", "explanation_bench", "synthetic_v1_labels_r2.json")
GATES = os.path.join(ROOT, "data", "explanation_bench", "gates_v1.json")
GATES_V11 = os.path.join(ROOT, "data", "explanation_bench", "gates_v1.1.json")
RECORD = os.path.join(ROOT, "data", "explanation_bench", "runs", "77a_match_tables_v0.2.json")
RECORD_V03 = os.path.join(ROOT, "data", "explanation_bench", "runs", "77a_match_tables_v0.3.json")
DOCS = {"表": os.path.join(ROOT, "docs", "match_tables_v0.2.md"), "原則": os.path.join(ROOT, "docs", "answer_principle_v1.md")}
DOCS_V03 = {"表": os.path.join(ROOT, "docs", "match_tables_v0.3.md"), "原則": os.path.join(ROOT, "docs", "answer_principle_v1.md")}
NOMIC = ("nomic-ai/nomic-embed-text-v2-moe", "1066b6599d099fbb93dfcb64f9c37a7c9e503e85")       # element_probe.NOMIC と同じ
NOMIC_CODE = ("nomic-ai/nomic-bert-2048", "7710840340a098cfb869c4f65e87cf2b1b70caca")
NOMIC_PREFIX = "search_query: "        # embedding_config の nomic-emb-v2 の symmetric（対象の句どうしは対称に比べる）
RELS = ("R1", "R2", "R3", "R4")
# gates_v1 の kind → 落とす段（関門は表 §1 の名前）
GATE_KIND = {"否定": ("関門", "否定"), "推量": ("関門", "推量・伝聞"), "伝聞": ("関門", "推量・伝聞"),
             "主語が O でない": ("関門", "主語が O でない"), "希望": ("関門", "希望"), "受益": ("関門", "受身・受益"),
             "受身": ("関門", "受身・受益"), "探す": ("関門", "探す・募集する"), "募集する": ("関門", "探す・募集する"),
             "消費": ("関門", "消費"), "学ぶ側": ("関門", "学ぶ側"), "期間なしの現在": ("種類", "応えない：現在の形・期間なし"),
             "条件が違う": ("条件", None), "領域が近い": ("領域", None), "枠の内容が違う": ("枠", None), "述語が違う": ("述語", None),
             "従属節の否定": ("関門", "否定"), "主語": ("関門", "主語が O でない"), "禁止語": ("領域", None)}


def load_items(gates_path=GATES):
    syn = json.load(open(SYN, encoding="utf-8"))
    r2 = json.load(open(R2, encoding="utf-8"))["labels"]
    gates = json.load(open(gates_path, encoding="utf-8"))["items"]
    kinds = {p["id"]: p["種類"] for p in syn["people"]}
    items = []
    for x in syn["pairs"]:
        lab = r2[x["id"]]
        items.append({"id": x["id"], "set": "60", "need": x["need"], "offer": x["other"], "purpose_kind": kinds[x["person"]],
                      "gold": lab["label"] if lab["label"] in RELS else "説明できない", "gold_sub": lab.get("sub"),
                      "basis": lab.get("basis", ""), "r2_reason": lab.get("reason", "")})
    for g in gates:
        exp = g["expected"]
        rel = exp[:2] if exp[:2] in RELS else "説明できない"
        sub = exp[exp.find("（") + 1:exp.find("）")] if "（" in exp else None
        items.append({"id": g["id"], "set": "gates", "need": g["need"], "offer": g["offer"], "purpose_kind": kinds[g["person"]],
                      "gold": rel, "gold_sub": sub, "kind": g["kind"], "base": g["base"], "note": g.get("note", "")})
    return items


def run_all(items, mode, sim=None, threshold=None, version=None, words=None):
    out = {}
    for it in items:
        r = MT.match(it["need"], it["offer"], mode=mode, sim=sim, threshold=threshold, version=version, words=words)
        out[it["id"]] = r
    return out


def nomic_queries(results):
    """nomic の段まで進んだ句の (必要の対象, 相手の対象, 概念の判定)。"""
    qs = []
    for i, r in results.items():
        for c in r["clauses"]:
            q = c.get("nomic")
            if q and q["必要の対象"] and q["相手の対象"] and q["必要の名詞の種類"] and q["必要の名詞の種類"] == q["相手の名詞の種類"]:
                qs.append((i, q["必要の対象"], q["相手の対象"], q["概念の判定"]))
    return qs


def choose_threshold(items, mode, sims):
    """線: 60 組と gates_v1 の負例が 0 になる、いちばん低い線（越えたら合う）。
    nomic が「合う」と言えば最後まで通ってしまう負例の、近さの最大値を線にする（それを越えるものだけが合う）。
    そうした負例が無ければ、線は -1（下の限り）。返り値: (線, 決めた負例の一覧)"""
    gold = {it["id"]: it["gold"] for it in items}
    base = run_all(items, mode)
    always = run_all(items, mode, sim=lambda a, b: 1.0, threshold=-2.0)
    dependent = [i for i in always if always[i]["passed"] and not base[i]["passed"]]
    neg = []
    for i in dependent:
        if gold[i] != "説明できない":
            continue
        vals = [sims[(q[1], q[2])] for q in nomic_queries({i: base[i]}) if (q[1], q[2]) in sims]
        if vals:
            neg.append((i, max(vals)))
    if not neg:
        return -1.0, [], dependent
    return max(v for _, v in neg), sorted(neg, key=lambda x: -x[1]), dependent


def nomic_sims(pairs):
    sys.path.insert(0, os.path.join(ROOT, "nomic_server"))
    from model import NomicEmbedder        # nomic_server/model.py（本番の推論サーバーと同じ読み込み）
    import numpy as np
    emb = NomicEmbedder(model_name=NOMIC[0])
    texts = sorted({t for p in pairs for t in p})
    vecs = emb.encode_batch([NOMIC_PREFIX + t for t in texts])
    v = {t: np.asarray(x, dtype=np.float64) for t, x in zip(texts, vecs)}
    out = {}
    for a, b in pairs:
        va, vb = v[a] / np.linalg.norm(v[a]), v[b] / np.linalg.norm(v[b])
        out[(a, b)] = float(round(float(va @ vb), 6))
    return out, {"model": NOMIC[0], "revision_pinned": NOMIC[1], "code_revision_pinned": NOMIC_CODE[1],
                 "revision_cached": hf_revision(NOMIC[0]), "code_revision_cached": hf_revision(NOMIC_CODE[0]),
                 "dtype": os.environ.get("NOMIC_TORCH_DTYPE", "bfloat16"), "prefix": NOMIC_PREFIX,
                 "dim": len(next(iter(v.values())))}


def hf_revision(repo):
    base = os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")), "hub",
                        "models--" + repo.replace("/", "--"))
    try:
        return open(os.path.join(base, "refs", "main"), encoding="utf-8").read().strip()
    except OSError:
        return None


# ── 数える ─────────────────────────────────────────────────────────────────────────────
def summarize(items, results):
    by = {it["id"]: it for it in items}
    s60 = [it for it in items if it["set"] == "60"]
    pos = [it for it in s60 if it["gold"] != "説明できない"]
    neg = [it for it in s60 if it["gold"] == "説明できない"]
    shown = [it for it in s60 if results[it["id"]]["passed"]]
    correct = [it for it in shown if it["gold"] != "説明できない" and results[it["id"]]["relation"] == it["gold"]]
    conf = {}
    for it in s60:
        r = results[it["id"]]
        k = (it["gold"], r["relation"] if r["passed"] else "落とした")
        conf[k] = conf.get(k, 0) + 1
    c60 = {
        "出した": len(shown),
        "正しさ": {"分子": len(correct), "分母": len(shown)},
        "誤り": {"出した": sum(1 for it in neg if results[it["id"]]["passed"]), "分母": len(neg),
                "組": [{"id": it["id"], "関係": results[it["id"]]["relation"], "句": results[it["id"]]["clause"],
                       "理由": f"{results[it['id']]['kind']}・{results[it['id']].get('domain')}"}
                      for it in neg if results[it["id"]]["passed"]]},
        "拾えた": {"出した": sum(1 for it in pos if results[it["id"]]["passed"]), "分母": len(pos),
                 "関係も一致": sum(1 for it in pos if results[it["id"]]["passed"] and results[it["id"]]["relation"] == it["gold"])},
        "取りちがえの表": {f"{a}|{b}": v for (a, b), v in sorted(conf.items())},
        "落ちた場所（説明できる組）": [{"id": it["id"], "正解": it["gold"], "段": results[it["id"]].get("where"),
                                   "理由": results[it["id"]].get("why")} for it in pos if not results[it["id"]]["passed"]],
        "C の種類": {},
        "補完で R4 だけ（画面には出さない）": [it["id"] for it in shown if results[it["id"]]["relation"] == "R4"
                                   and it["purpose_kind"] == "補完"],
    }
    for it in shown:
        k = results[it["id"]]["kind"]
        c60["C の種類"][k] = c60["C の種類"].get(k, 0) + 1
    gs = [it for it in items if it["set"] == "gates"]
    gneg = [it for it in gs if it["gold"] == "説明できない"]
    per_kind = {}
    rows = []
    for it in gneg:
        r = results[it["id"]]
        exp_step, exp_gate = GATE_KIND.get(it["kind"], (None, None))
        hits = sorted({g for c in r["clauses"] for g in (c.get("関門") or [])})
        reasons = [c.get("理由") for c in r["clauses"]]
        if exp_step == "関門":
            at_expected = exp_gate in hits or exp_gate in (r.get("why") or "")
        elif exp_gate is not None:
            at_expected = exp_gate in reasons
        else:
            at_expected = any(c.get("落ちた段") == exp_step for c in r["clauses"])
        d = per_kind.setdefault(it["kind"], {"組": 0, "落とした": 0, "その段で": 0})
        d["組"] += 1
        d["落とした"] += int(not r["passed"])
        d["その段で"] += int((not r["passed"]) and at_expected)
        rows.append({"id": it["id"], "kind": it["kind"], "落とした": not r["passed"], "その段で": at_expected,
                     "段": r.get("where"), "関門": hits, "理由": r.get("why")})
    gpos = []
    for it in gs:
        if it["gold"] == "説明できない":
            continue
        r = results[it["id"]]
        gpos.append({"id": it["id"], "期待": f"{it['gold']}（{it['gold_sub']}）", "通った": r["passed"],
                     "関係": r.get("relation"), "C の種類": r.get("kind"), "C": r.get("C"),
                     "期待どおり": bool(r["passed"] and r["relation"] == it["gold"]
                                    and (r["kind"] == it["gold_sub"] or (r.get("short") or "").startswith(it["gold_sub"] or "-"))),
                     "段": r.get("where"), "理由": r.get("why")})
    bases = {}
    for it in gs:
        b = by[it["base"]]
        r = results[b["id"]]
        ok = (r["passed"] and r["relation"] == b["gold"]) if b["gold"] != "説明できない" else not r["passed"]
        bases[b["id"]] = {"正解 r2": b["gold"], "表": r["relation"] if r["passed"] else "落とした", "r2 のとおり": ok}
    cg = {"負例": {"組": len(gneg), "落とした": sum(1 for it in gneg if not results[it["id"]]["passed"]),
                  "種類ごと": per_kind, "組ごと": rows},
          "正例": gpos, "元の組": bases}
    return {"60": c60, "gates": cg}


def type1_list(items, results):  # noqa: C901
    out = []
    for it in items:
        r = results[it["id"]]
        if not r["passed"]:
            continue
        B = r["need"]["B"]
        out.append({"id": it["id"], "関係": r["relation"], "C の種類": r["kind"], "①": MT.type1_sentence(B, r["clause"], r["short"]),
                    "offer_quote の字数": len(r["clause"]), "画面の規則": ("補完で R4 だけは出さない"
                                                                if r["relation"] == "R4" and it["purpose_kind"] == "補完" else "")})
    return out


def clause_breaks(items, split, whole):
    """句の切り方で結果が変わった文（split と whole で、通ったかが違う）。"""
    out = []
    for it in items:
        a, b = split[it["id"]], whole[it["id"]]
        if a["passed"] != b["passed"]:
            out.append({"id": it["id"], "正解": it["gold"], "split": a["relation"] if a["passed"] else f"落とした（{a.get('where')}）",
                        "whole": b["relation"] if b["passed"] else f"落とした（{b.get('where')}）",
                        "句": [c["句"] for c in a["clauses"]]})
    return out


def slim(results):
    """記録に残す形（句ごとの段と理由・関係と［C］）。"""
    out = {}
    for i, r in results.items():
        out[i] = {"passed": r["passed"], "relation": r.get("relation"), "kind": r.get("kind"), "C": r.get("C"),
                  "short": r.get("short"), "clause": r.get("clause"), "domain": r.get("domain"),
                  "where": r.get("where"), "why": r.get("why"), "clauses": r["clauses"]}
    return out


def measure(sims, nomic_info=None):
    items = load_items()
    needs = {}
    for it in items:
        if it["need"] not in needs:
            needs[it["need"]] = MT.match(it["need"], "。", mode="whole")["need"]
    out = {"version": "77a-1", "table": {"version": MT.VERSION, "files": MT.table_digest(),
                                         "docs": {k: __import__("hashlib").sha256(open(p, "rb").read()).hexdigest()
                                                  for k, p in DOCS.items()}},
           "tools": MT.tool_versions(), "needs": needs, "modes": {}}
    for mode in MT.MODES:
        th, neg, dep = choose_threshold(items, mode, sims)
        res = run_all(items, mode, sim=lambda a, b: sims.get((a, b)), threshold=th)
        out["modes"][mode] = {
            "nomic": {"線": th, "決め方": "nomic が「合う」と言えば最後まで通ってしまう負例（60 組＋gates_v1）の近さの最大値。"
                                       "これを越えたものだけ合う。そうした負例が無ければ -1",
                      "線を決めた負例": [{"id": i, "近さ": v} for i, v in neg],
                      "nomic で通りうる組": dep,
                      "nomic で通った組": [i for i, r in res.items() if r["passed"] and r.get("domain") == "nomic"],
                      "問い合わせ": [{"id": i, "必要の対象": a, "相手の対象": b, "概念の判定": s, "近さ": sims.get((a, b))}
                                for i, a, b, s in nomic_queries(run_all(items, mode))]},
            "counts": summarize(items, res), "type1": type1_list(items, res), "results": slim(res)}
    out["clause_breaks"] = clause_breaks(items, run_all(items, "split", sim=lambda a, b: sims.get((a, b)),
                                                        threshold=out["modes"]["split"]["nomic"]["線"]),
                                         run_all(items, "whole", sim=lambda a, b: sims.get((a, b)),
                                                 threshold=out["modes"]["whole"]["nomic"]["線"]))
    out["nomic"] = nomic_info or {}
    out["nomic"]["sims"] = [{"a": a, "b": b, "cos": v} for (a, b), v in sorted(sims.items())]
    # 決定性: 同じ入力で、もう一度計算して同じか
    again = run_all(items, "split", sim=lambda a, b: sims.get((a, b)), threshold=out["modes"]["split"]["nomic"]["線"])
    out["determinism"] = {"same": json.dumps(slim(again), ensure_ascii=False, sort_keys=True)
                          == json.dumps(out["modes"]["split"]["results"], ensure_ascii=False, sort_keys=True)}
    return out


# ── 追補1（表 v0.3）──────────────────────────────────────────────────────────────────────
def slim_v03(results):
    out = slim(results)
    for i, r in results.items():
        out[i].update({"joined": r.get("joined", False), "units": r.get("units"), "unit": r.get("unit"),
                       "つながなかった": r.get("つながなかった", [])})
    return out


def concept_unknown_log(items, results, sims):
    """表 v0.3 §6 の 4: 領域で「分からない」で落ちた組の行。人のメモの欄は空。nomic の近さは参考（判定に使わない）。"""
    rows = []
    for it in items:
        r = results[it["id"]]
        if r["passed"] or r.get("where") != "領域":
            continue
        unit = r.get("unit")
        rows.append({"id": it["id"], "必要の句": it["need"], "相手の句": unit, "人のメモ": "",
                     "nomic の近さ（参考）": sims.get((it["need"], unit)), "落とした理由": r.get("why")})
    return rows


def compare_with_v02(items, res03):
    """v0.2 で拾えていた組（記録の split）のうち、v0.3 でどうなったか。共通の概念と、両側の領域の名詞も書く。"""
    rec = json.load(open(RECORD, encoding="utf-8"))
    v02 = rec["modes"]["split"]["results"]
    by = {it["id"]: it for it in items}
    rows = []
    for i, r in v02.items():
        if not r["passed"] or i not in res03:
            continue
        r3 = res03[i]
        row = {"id": i, "v0.2": f"{r['relation']}・{r['kind']}", "v0.3": (f"{r3['relation']}・{r3['kind']}" if r3["passed"]
                                                                        else f"落とした（{r3.get('where')}：{r3.get('why')}）")}
        if r3["passed"]:
            tok = MT._VER.set("v0.3")
            tw = MT._WORDS.set("lemma")
            try:
                need = MT.Span([t for t in MT.tokenize(by[i]["need"]) if t.surface != "。"])
                off = MT.Span(MT.tokenize(r3["clause"]))
                c = (r3.get("domain") or "").replace("概念 ", "")
                row["概念"] = c
                row["領域の名詞（必要）"] = MT.concept_detail(need)[c]["領域の名詞"] if c in MT.concept_detail(need) else []
                row["領域の名詞（相手）"] = MT.concept_detail(off)[c]["領域の名詞"] if c in MT.concept_detail(off) else []
            finally:
                MT._VER.reset(tok)
                MT._WORDS.reset(tw)
        rows.append(row)
    return rows


def words_diff(items, a, b):
    """辞書形と表記のままで、結果（通ったか・関係・［C］・段）が変わった組。"""
    out = []
    for it in items:
        x, y = a[it["id"]], b[it["id"]]
        kx = (x["passed"], x.get("relation"), x.get("kind"), x.get("clause"), x.get("where"))
        ky = (y["passed"], y.get("relation"), y.get("kind"), y.get("clause"), y.get("where"))
        if kx != ky:
            out.append({"id": it["id"], "辞書形": kx, "表記のまま": ky})
    return out


def measure_v03(sims, nomic_info=None):
    items = load_items(GATES_V11)
    tok = MT._VER.set("v0.3")
    try:
        needs = {}
        for it in items:
            if it["need"] not in needs:
                needs[it["need"]] = MT.match(it["need"], "。", version="v0.3")["need"]
        out = {"version": "77a-s1", "table": {"version": "v0.3", "files": MT.table_digest("v0.3"),
                                              "docs": {k: __import__("hashlib").sha256(open(p, "rb").read()).hexdigest()
                                                       for k, p in DOCS_V03.items()}},
               "gates": os.path.basename(GATES_V11), "tools": MT.tool_versions(), "needs": needs, "modes": {}}
    finally:
        MT._VER.reset(tok)
    res = {}
    for words in MT.WORDS:
        res[words] = run_all(items, "split", version="v0.3", words=words)
        out["modes"][words] = {"counts": summarize(items, res[words]), "type1": type1_list(items, res[words]),
                               "joined": [{"id": i, "offer_quote": r["clause"]} for i, r in res[words].items()
                                          if r["passed"] and r.get("joined")],
                               "results": slim_v03(res[words])}
    out["words_diff"] = words_diff(items, res["lemma"], res["surface"])
    out["v0.2 で拾えていた組"] = compare_with_v02(items, res["lemma"])
    out["概念不明"] = concept_unknown_log(items, res["lemma"], sims)
    out["offer_quote"] = {i: res["lemma"][i].get("clause") for i in ("G31", "G34")}
    out["nomic"] = dict(nomic_info or {})
    out["nomic"]["使い方"] = "判定に使わない。「概念不明」のログの参考の近さだけ（必要の文と、領域の段まで進んだ相手の句）"
    out["nomic"]["sims"] = [{"a": a, "b": b, "cos": v} for (a, b), v in sorted(sims.items())]
    again = run_all(items, "split", version="v0.3", words="lemma")
    out["determinism"] = {"same": json.dumps(slim_v03(again), ensure_ascii=False, sort_keys=True)
                          == json.dumps(out["modes"]["lemma"]["results"], ensure_ascii=False, sort_keys=True)}
    return out


def sims_from_record(path):
    rec = json.load(open(path, encoding="utf-8"))
    return {(x["a"], x["b"]): x["cos"] for x in rec["nomic"]["sims"]}, {k: v for k, v in rec["nomic"].items() if k != "sims"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--nomic", action="store_true", help="nomic を手元で読み込んで、対象の句どうしの近さを出す")
    ap.add_argument("--sims-from", help="記録にある nomic の値を使う（モデルは読まない）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--version", default="v0.2", choices=MT.VERSIONS)
    a = ap.parse_args(argv)
    if a.version == "v0.3":
        return main_v03(a)
    a.out = a.out or RECORD
    items = load_items()
    if a.nomic:
        pairs = sorted({(q[1], q[2]) for mode in MT.MODES for q in nomic_queries(run_all(items, mode))})
        sims, info = nomic_sims(pairs)
    elif a.sims_from:
        sims, info = sims_from_record(a.sims_from)
    else:
        sims, info = {}, {"note": "nomic の値なし（その段は分からない）"}
    rec = measure(sims, info)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
        f.write("\n")
    c = rec["modes"]["split"]["counts"]
    print(json.dumps({"60": {k: c["60"][k] for k in ("出した", "正しさ", "拾えた")} | {"誤り": c["60"]["誤り"]["出した"]},
                      "gates 負例": f"{c['gates']['負例']['落とした']}/{c['gates']['負例']['組']}",
                      "nomic の線": rec["modes"]["split"]["nomic"]["線"], "決定性": rec["determinism"]},
                     ensure_ascii=False))
    return rec


def main_v03(a):
    items = load_items(GATES_V11)
    if a.nomic:
        base = run_all(items, "split", version="v0.3", words="lemma")
        pairs = sorted({(r["need"], r["相手の句"]) for r in [{"need": it["need"], "相手の句": base[it["id"]].get("unit")}
                                                          for it in items if not base[it["id"]]["passed"]
                                                          and base[it["id"]].get("where") == "領域"]})
        sims, info = nomic_sims(pairs)
    elif a.sims_from:
        sims, info = sims_from_record(a.sims_from)
    else:
        sims, info = {}, {"note": "nomic の値なし（参考の近さは空）"}
    rec = measure_v03(sims, info)
    out = a.out or RECORD_V03
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
        f.write("\n")
    c = rec["modes"]["lemma"]["counts"]
    print(json.dumps({"60": {k: c["60"][k] for k in ("出した", "正しさ", "拾えた")} | {"誤り": c["60"]["誤り"]["出した"]},
                      "gates 負例": f"{c['gates']['負例']['落とした']}/{c['gates']['負例']['組']}",
                      "gates 正例": f"{sum(1 for x in c['gates']['正例'] if x['期待どおり'])}/{len(c['gates']['正例'])}",
                      "辞書形と表記のままの差": len(rec["words_diff"]), "概念不明": len(rec["概念不明"]),
                      "決定性": rec["determinism"]}, ensure_ascii=False))
    return rec


if __name__ == "__main__":
    main()
