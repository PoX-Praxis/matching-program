#!/usr/bin/env python3
"""指示書69 §5 — 接続の説明の計測の仕組み（段階3 の準備。閾値を決めない・数値を報告に載せない）。

正解つきのデータで、パイプライン全体（段1 → 確かめ → 段2 → 確かめ → 表示するか）を走らせ、次を数える。
  - 人が確かめた負例で、説明を出してしまう数（48 対の「応えない」・KAORU が「応えない」とした上層の 4 人）
  - 48 対の「応える」で、説明を出せる数
  - hard negative 60（el-v2）・C 群 36（el-v2）での同じ数（補助）
  - 確かめで落ちた数（段ごと・理由ごと）
  - 決定性（同じ入力を 2 回）
  - 段2 に原文が漏れていないこと（段2 の入力が、確かめ済みの句・ラベル・見出しだけか）
  - お手本（KAORU×PA の 2 枚）の再現（組・ラベル・句・① の一致）
出力: local/69/explanation_measure_<backend>.json・.md（件数だけ。文は書かない）。
候補の線（§2）は通さない（データの対は、すでに組になっている）。上層の結果は使わない。外部の AI の API は使わない。
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (ROOT, os.path.join(ROOT, "src")) if p not in sys.path]
import explanation as X  # noqa: E402
import explanation_gen as G  # noqa: E402

DATA = os.path.join(ROOT, "scripts", "probe_data")


def n_person_from_kaoru(doc):
    """KAORU の v5 の公開の範囲の JSON（local/）から、N の側の公開の文を作る（目的の種類は §4-4 の仮の規則）。"""
    purposes = []
    for i, p in enumerate(doc.get("purposes") or [], 1):
        needs = [{"text": str(x.get("文") or ""), "type": str(x.get("型") or ""), "must": bool(x.get("必須"))}
                 for x in p.get("必要像") or [] if str(x.get("文") or "").strip()]
        if needs:
            purposes.append({"purpose_id": p.get("purpose_id") or f"p{i}", "title": str(p.get("向かう先") or ""),
                             "means": str(p.get("手段") or ""), "needs": needs})
    return {"subject": "n", "purposes": purposes}


def one_sentence_person(text):
    return {"subject": "o", "offers": [{"text": text, "type": ""}], "state": {}, "interests": []}


def _strings(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)
    elif isinstance(x, str):
        yield x


def leak_violations(direction, stage2_payload):
    """段2 の入力の文字列が、確かめ済みの句・ラベル・見出し・種類・目的の参照だけか。外れた数を返す。"""
    allowed = {"補完", "共鳴"}
    for p in direction["purposes"]:
        allowed |= {p["title"], p["ref"]}
    for p in stage2_payload.get("purposes") or []:
        allowed |= {p.get("purpose_ref"), p.get("見出し")}
        for e in p.get("elements") or []:
            allowed |= {e["relation"], X.RELATIONS.get(e["relation"], ("",))[0]}
    kept = {(e["need_quote"], e["offer_quote"]) for p in direction["purposes"] for e in p["elements"]}
    quotes = {q for pair in kept for q in pair}
    bad = 0
    for p in stage2_payload.get("purposes") or []:
        for e in p.get("elements") or []:
            if (e["need_quote"], e["offer_quote"]) not in kept and not (e["need_quote"] in quotes and e["offer_quote"] in quotes):
                bad += 1
    bad += sum(1 for s in _strings({k: v for k, v in stage2_payload.items()}) if s not in allowed and s not in quotes)
    return bad


def run_one(n, o, backend, drops, stage_drops):
    trace = []
    res = G.generate_direction(n, o, backend, trace=trace)
    for pr in res["problems"]:
        m = re.search(r"（(.+)）", pr)
        for why in (m.group(1).split("・") if m else [pr]):
            drops[why] += 1
        stage_drops["段1" if "組" in pr else "段2" if "①" in pr else "全体"] += 1
    shown = X.showable(X.check(X.assemble(res["direction"], None, True))[0])
    leak = leak_violations(res["direction"], trace[0]["stage2_payload"]) if trace else 0
    return res, shown, leak


def exemplar_persons(direction):
    """お手本の向き（N.*・O.* の参照）から、N と O の公開の文を組み直す（引用の欄の原文から）。"""
    src = {c["ref"]: c["text"] for c in direction["citations"]}
    purposes = []
    for p in direction["purposes"]:
        pid = p["ref"].split(".")[2]
        needs = sorted([(r, t) for r, t in src.items() if r.startswith(f"N.need.{pid}.")])
        musts = {e["need_ref"] for e in p["elements"] if e["must"]} | set(p["missing_must"])
        purposes.append({"purpose_id": pid, "title": p["title"], "means": "", "kind": p["kind"],
                         "needs": [{"text": t, "type": "", "must": r in musts} for r, t in needs]})
    offers = [{"text": t, "type": ""} for r, t in sorted(src.items()) if r.startswith("O.offer.")]
    state = {r.split(".", 2)[2]: t for r, t in src.items() if r.startswith("O.state.")}
    interests = [t for r, t in sorted(src.items()) if r.startswith("O.interest.")]
    return {"subject": "n", "purposes": purposes}, {"subject": "o", "offers": offers, "state": state, "interests": interests}


def compare_exemplar(want, got):
    """お手本との比べ方（案）: 組（必要の参照・相手の参照）の一致・ラベルの一致・句の一致・① の一致・状態の一致を数える。
    参照は、組み直した入力で番号が変わりうるので、句の組で比べる。"""
    def pairs(d):
        return {(e["need_quote"], e["offer_quote"]): e["relation"] for p in d["purposes"] for e in p["elements"]}
    w, g = pairs(want), pairs(got)
    wm = {p["title"]: (p["meaning"] or {}).get("text") for p in want["purposes"]}
    gm = {p["title"]: (p["meaning"] or {}).get("text") for p in got["purposes"]}
    ws = {p["title"]: p["status"] for p in want["purposes"]}
    gs = {p["title"]: p["status"] for p in got["purposes"]}
    return {"pairs_want": len(w), "pairs_got": len(g), "pairs_same": len(set(w) & set(g)),
            "labels_same": sum(1 for k in set(w) & set(g) if w[k] == g[k]),
            "meaning_same": sum(1 for t in wm if wm[t] and gm.get(t) == wm[t]), "meanings_want": sum(1 for t in wm if wm[t]),
            "status_same": sum(1 for t in ws if gs.get(t) == ws[t]), "purposes": len(ws)}


def to_markdown(res):
    L = [f"# 接続の説明の計測（{res['backend']}）", "",
         "- 指示書69 §5 の計測の仕組み。**件数だけ。文は書かない。閾値は決めていない（段階3 の前に KAORU が決める）**",
         f"- モデル: {res['model']}（revision {res['revision']}）・走らせた時間 {res['seconds']} 秒", ""]
    for k, label in (("labeled_neg", "48 対の「応えない」で出した"), ("labeled_pos", "48 対の「応える」で出した"),
                     ("upper_neg", "上層の「応えない」4 人で出した"), ("hn_shown", "hard negative で出した"),
                     ("c_shown", "C 群で出した")):
        v = res.get(k)
        L.append(f"- {label}: " + ("データが無い" if v is None else f"{v['shown']}/{v['n']}"))
    L += ["", "確かめで落ちた数（理由ごと）:", ""]
    L += [f"- {k}: {v}" for k, v in sorted(res["drops"].items(), key=lambda x: -x[1])] or ["- なし"]
    L += ["", "段ごと: " + "・".join(f"{k} {v}" for k, v in res["stage_drops"].items()),
          f"決定性: {res['determinism']['same']}/{res['determinism']['runs']} が同じ出力",
          f"段2 に原文が漏れた数: {res['leak']['violations']}（{res['leak']['runs']} 回）", ""]
    ex = res.get("exemplar")
    if ex:
        L += ["お手本の再現:", ""] + [f"- {k}: " + "・".join(f"{a} {b}" for a, b in v.items()) for k, v in ex.items()]
    else:
        L.append("お手本: データが無い")
    return "\n".join(L) + "\n"


def main(argv=None, backend=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backend", default="fixed", choices=("fixed", "local"))
    ap.add_argument("--kaoru", default=os.path.join(ROOT, "local", "kaoru_v5_public.json"))
    ap.add_argument("--labels", default=os.path.join(ROOT, "local", "65a", "labels_v1.json"))
    ap.add_argument("--upper-neg", default=os.path.join(ROOT, "local", "65a", "upper_negatives.json"))
    ap.add_argument("--exemplar", default=os.path.join(ROOT, "local", "68", "explanation_cards.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "local", "69"))
    ap.add_argument("--limit", type=int, default=0, help="集合ごとに先頭の N 件だけ（local の動作確かめ用）")
    args = ap.parse_args(argv)
    t0 = time.time()

    def read(path):
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None

    backend = backend or (G.FixedBackend() if args.backend == "fixed" else G.LocalBackend())
    lim = (lambda xs: xs[:args.limit]) if args.limit else (lambda xs: xs)
    n = n_person_from_kaoru(read(args.kaoru) or {})
    with open(os.path.join(DATA, "element_sets_v2.json"), encoding="utf-8") as f:
        el = json.load(f)
    drops, stage_drops = Counter(), Counter({"段1": 0, "段2": 0, "全体": 0})
    leak = {"runs": 0, "violations": 0}
    det = {"runs": 0, "same": 0}
    res = {"backend": backend.name, "model": backend.model, "revision": backend.revision}

    def count(texts):
        shown = 0
        for t in texts:
            r, s, lk = run_one(n, one_sentence_person(t), backend, drops, stage_drops)
            shown += int(s)
            leak["runs"] += 1
            leak["violations"] += lk
        return {"shown": shown, "n": len(texts)}

    res["hn_shown"] = count(lim([x["text"] for x in el["HN"]["a"]] + list(el["HN"]["b"]))) if n["purposes"] else None
    res["c_shown"] = count(lim([x["text"] for x in el["C"]])) if n["purposes"] else None
    labels = read(args.labels)
    if labels and n["purposes"]:
        pairs = labels.get("pairs") or []
        res["labeled_neg"] = count(lim([p["other"] for p in pairs if (p.get("final") or p.get("provisional")) == "応えない"]))
        res["labeled_pos"] = count(lim([p["other"] for p in pairs if (p.get("final") or p.get("provisional")) == "応える"]))
    else:
        res["labeled_neg"] = res["labeled_pos"] = None
    up = read(args.upper_neg)
    if up and n["purposes"]:
        shown = 0
        for person in lim(up.get("persons") or []):
            _, s, _ = run_one(n, {"subject": "o", **person}, backend, drops, stage_drops)
            shown += int(s)
        res["upper_neg"] = {"shown": shown, "n": len(lim(up.get("persons") or []))}
    else:
        res["upper_neg"] = None
    for t in lim([x["text"] for x in el["C"]])[:3] if n["purposes"] else []:     # 決定性（同じ入力を 2 回）
        a = G.generate_direction(n, one_sentence_person(t), backend)["output_digest"]
        b = G.generate_direction(n, one_sentence_person(t), backend)["output_digest"]
        det["runs"] += 1
        det["same"] += int(a == b)
    ex = read(args.exemplar)
    if ex and ex.get("directions"):
        res["exemplar"] = {}
        for i, d in enumerate(ex["directions"]):
            en, eo = exemplar_persons(d)
            r = G.generate_direction(en, eo, backend)
            res["exemplar"][f"向き{i + 1}"] = compare_exemplar(d, r["direction"])
    else:
        res["exemplar"] = None
    res.update(drops=dict(drops), stage_drops=dict(stage_drops), leak=leak, determinism=det,
               seconds=round(time.time() - t0, 1))
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, f"explanation_measure_{backend.name}.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, f"explanation_measure_{backend.name}.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(res))
    return res


if __name__ == "__main__":
    main()
