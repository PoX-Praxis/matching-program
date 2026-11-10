"""指示書66D 順4: 書き直した必要像と元の必要像の忠実性の組（10 対・seed 6605）を作る。

組み方は scripts/probe_data/rewrite_66d_v1.json の "faithfulness" のとおり（計測の前に固定したもの）。
- 同じ目的の番号どうしで、nomic（query の役割を両側）で g = (1+cos)/2 を出す
- R→O: 書き直した文ごとに g が最も大きい元の文（同点は元の並びで前）
- O→R: 元の文ごとに g が最も大きい書き直した文（同点は書き直した並びで前）
- 10 対: R→O をすべて先に入れ、残りの枠に R→O に無い O→R を入れる（多ければ seed 6605 で選ぶ）
- 消えた必要の候補: R→O でどの書き直した文からも選ばれなかった元の文

出力は local/66/d/ だけ（KAORU の文を含むのでコミットしない）。
"""
import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

FIXED = os.path.join(ROOT, "scripts", "probe_data", "rewrite_66d_v1.json")


def g_of(u, v):
    import numpy as np
    return float((1.0 + float(np.dot(u, v))) / 2.0)


def pairs(originals, rewritten, emb, seed, max_pairs):
    """originals: [{"id","purpose","text"}]・rewritten: 同じ形。emb(text) は正規化済みのベクトル。"""
    vec = {}
    for x in originals + rewritten:
        vec[x["id"]] = emb(x["text"])
    r2o, o2r = [], []
    for r in rewritten:
        cands = [o for o in originals if o["purpose"] == r["purpose"]]
        if not cands:
            continue
        best = max(cands, key=lambda o: (g_of(vec[r["id"]], vec[o["id"]]), -cands.index(o)))
        r2o.append({"rewritten": r["id"], "original": best["id"], "g": g_of(vec[r["id"]], vec[best["id"]]), "from": "R→O"})
    for o in originals:
        cands = [r for r in rewritten if r["purpose"] == o["purpose"]]
        if not cands:
            continue
        best = max(cands, key=lambda r: (g_of(vec[o["id"]], vec[r["id"]]), -cands.index(r)))
        o2r.append({"rewritten": best["id"], "original": o["id"], "g": g_of(vec[o["id"]], vec[best["id"]]), "from": "O→R"})
    rng = random.Random(seed)
    if len(r2o) > max_pairs:
        chosen = sorted(rng.sample(range(len(r2o)), max_pairs))
        picked = [r2o[i] for i in chosen]
    else:
        picked = list(r2o)
        seen = {(p["rewritten"], p["original"]) for p in r2o}
        extra = [p for p in o2r if (p["rewritten"], p["original"]) not in seen]
        room = max_pairs - len(picked)
        if len(extra) > room:
            extra = [extra[i] for i in sorted(rng.sample(range(len(extra)), room))]
        picked += extra
    used = {p["original"] for p in r2o}
    vanished = [o["id"] for o in originals if o["id"] not in used]
    corr = {p["rewritten"]: p["original"] for p in r2o}
    return {"r2o": r2o, "o2r": o2r, "picked": picked, "vanished": vanished, "correspondence": corr}


def load(kaoru_path, rewrite_path):
    with open(kaoru_path, encoding="utf-8") as f:
        k = json.load(f)
    originals = []
    for pi, p in enumerate(k.get("purposes") or [], 1):
        for ni, n in enumerate(p.get("必要像") or [], 1):
            originals.append({"id": f"{pi}-{ni}", "purpose": pi, "text": str(n.get("文") or ""),
                              "type": n.get("型"), "must": bool(n.get("必須"))})
    with open(rewrite_path, encoding="utf-8") as f:
        rw = json.load(f)
    rewritten = [{"id": e["id"], "purpose": e["purpose"], "text": e["text"], "type": e["type"], "must": e["must"]}
                 for e in rw["elements"]]
    return originals, rewritten


def to_markdown(res, originals, rewritten, info):
    txt = {x["id"]: x for x in originals + rewritten}
    n = len(res["picked"])
    head = f"{n} 対（R→O と O→R の和集合が 10 以下のため、すべて）" if n < 10 else "10 対"
    model = info.get("mode") if isinstance(info, dict) else str(info)
    lines = [f"# 66D 忠実性の確認（{head}）と、消えた必要の候補", "",
             "- 出どころ: `scripts/rewrite_faithfulness_66d.py`（組み方は `rewrite_66d_v1.json` の faithfulness に固定）",
             f"- 埋め込み: {model}・query の役割を両側・g = (1+cos)/2・同じ目的の番号どうし",
             f"- 選び方: R→O を先にすべて入れ、残りの枠に R→O に無い O→R（seed 6605）",
             "- 確かめてほしいこと: 書き直した文と元の文が、**同じ必要を別の言い方にしただけか**",
             "- ラベル: 「同じ」「違う必要を足した・落とした」「判断できない」", "",
             "| # | 組み方 | 書き直した文 | 元の文 | ラベル |", "|---|---|---|---|---|"]
    for i, p in enumerate(res["picked"], 1):
        r, o = txt[p["rewritten"]], txt[p["original"]]
        rlab = f'{r["id"]}（{r["type"]}{"・必須" if r["must"] else ""}）'
        olab = f'{o["id"]}（{o["type"]}{"・必須" if o["must"] else ""}）'
        lines.append(f'| {i} | {p["from"]} | {rlab} {r["text"]} | {olab} {o["text"]} | |')
    lines += ["", "## 消えた必要の候補（どの書き直した文からも最も近いとされなかった元の文）", ""]
    if res["vanished"]:
        lines += ["| 元の要素 | 文 |", "|---|---|"]
        lines += [f'| {v}（{txt[v]["type"]}{"・必須" if txt[v]["must"] else ""}） | {txt[v]["text"]} |' for v in res["vanished"]]
    else:
        lines.append("（なし）")
    lines += ["", "## 書き直した要素ごとの「対応する元の要素」（R→O の組。hard negative の使い回しに使う）", "",
              "| 書き直した要素 | 対応する元の要素 |", "|---|---|"]
    lines += [f"| {r} | {o} |" for r, o in res["correspondence"].items()]
    return "\n".join(lines) + "\n"


def main(argv=None, encode=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kaoru", default=os.path.join(ROOT, "local", "kaoru_v5_public.json"))
    ap.add_argument("--rewrite", default=os.path.join(ROOT, "local", "66", "d", "rewrite_output.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "local", "66", "d"))
    args = ap.parse_args(argv)
    import discrimination_probe as DP
    emb, _, info = DP.make_embedder(None, None, encode=encode, strict=encode is None)
    with open(FIXED, encoding="utf-8") as f:
        fx = json.load(f)["faithfulness"]
    originals, rewritten = load(args.kaoru, args.rewrite)
    res = pairs(originals, rewritten, lambda t: emb(t, "query"), fx["seed"], fx["max_pairs"])
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "faithfulness_pairs.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "faithfulness_pairs.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(res, originals, rewritten, info))
    print(f"保存しました: {os.path.join(args.out, 'faithfulness_pairs.md')}（{len(res['picked'])} 対・消えた候補 {len(res['vanished'])}）")
    return res


if __name__ == "__main__":
    main()
