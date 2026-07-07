"""
MRL 256次元検証（決定基準③）— キャッシュ済みベクトルのみ使用・サーバー不要・再encodeしない

各モデルについて、フル次元ランキング vs 先頭256次元ランキングを突合する。
  - 候補ベクトル: eval/cache/{model_tag}_vectors.npz（full と _256 を保持）
  - seeker ベクトル: eval/cache/seeker_{model_tag}_{necessity_id}.json（solo_eval が保存）
  - パラメータ: 該当モデルの 1090件スナップショット params から取得（同一条件）

256版は「先頭256次元スライス→L2再正規化」。キャッシュの _256 は embed() が既に
l2_normalize(full[:256]) で作っているため、これをそのまま使う（＝スライス＋再正規化済み）。

出力: eval/analysis/mrl_report.md ＋ 標準出力の数値表。個人本文は出さない（login・数値・軸のみ）。
実行: python eval/scripts/mrl_report.py
"""
import sys, os, json, glob, math, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from embedding_service import cosine
from matcher_v4 import rank_candidates
import vector_cache

BASE     = pathlib.Path(__file__).parent.parent
CACHE_DIR = BASE / "cache"
SNAP_DIR = BASE / "snapshots"
OUT      = BASE / "analysis" / "mrl_report.md"

MODELS = [
    ("Qwen3",    "qwen3-embedding-0.6b-d1024"),
    ("EmbGemma", "embgemma-300m"),
    ("Nomic",    "nomic-emb-v2"),
]
FULL4 = ("will_symmetric", "will_passage", "state_passage", "necessity_query")
SHORT_MAP = {  # 256版で使うキー（remap して matcher に渡す）
    "will_symmetric": "will_sym_256", "will_passage": "will_pas_256",
    "state_passage": "state_pas_256", "necessity_query": "necessity_q_256",
}

# H-3 合否目安（損失<1%相当）
H3_JACCARD = 0.90
H3_TOP10_KEEP = 9


def _rankdata(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    r = [0.0] * len(vals); i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def _pearson(x, y):
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    dx = math.sqrt(sum((a - mx) ** 2 for a in x)); dy = math.sqrt(sum((b - my) ** 2 for b in y))
    if dx == 0 or dy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (dx * dy)


def jaccard(a, b):
    a, b = set(a), set(b); u = a | b
    return len(a & b) / len(u) if u else 0.0


def spearman_common(rank_a, rank_b):
    common = sorted(set(rank_a) & set(rank_b))
    if len(common) < 3:
        return None, len(common)
    return _pearson(_rankdata([rank_a[l] for l in common]),
                    _rankdata([rank_b[l] for l in common])), len(common)


def top10_retention(full_top, short_top):
    return len(set(full_top[:10]) & set(short_top[:10]))


def load_seeker(model_tag):
    fs = glob.glob(str(CACHE_DIR / f"seeker_{model_tag}_*.json"))
    if not fs:
        return None
    d = json.load(open(sorted(fs)[-1], encoding="utf-8"))
    return d["vecs"]


def load_params(model_tag):
    chosen = None
    for f in sorted(glob.glob(str(SNAP_DIR / f"solo_KH_{model_tag}_*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        if d.get("pool_size") == 1090:
            chosen = d["params"]
    if not chosen:
        return dict(gamma=0.43, p=-0.3, alpha=1.0, beta=1.2)  # フォールバック（match_config相当）
    return chosen


def rank_full_short(seeker, cache, params):
    """フル版と256版のランキング（final score）を返す。各 [(login, score, attribution), ...]。"""
    s_full = {k: seeker[k] for k in FULL4}
    s_short = {k: seeker[SHORT_MAP[k]] for k in FULL4}
    cl_full, cl_short = [], []
    for cid, (vecs, _h) in cache.items():
        cl_full.append((cid, {k: vecs[k] for k in FULL4}))
        cl_short.append((cid, {k: vecs[SHORT_MAP[k]] for k in FULL4}))
    kw = dict(gamma=params["gamma"], p=params["p"], alpha=params["alpha"], beta=params["beta"], top_k=None)
    return rank_candidates(s_full, cl_full, **kw), rank_candidates(s_short, cl_short, **kw)


def channel_ranking(seeker, cache, ch, use_short):
    """単一チャネル（a/b/c）の cos でランキング（login 降順）。"""
    def key(name, short):
        return SHORT_MAP[name] if short else name
    order = []
    for cid, (vecs, _h) in cache.items():
        if ch == "a":
            sim = cosine(seeker[key("will_symmetric", use_short)], vecs[key("will_symmetric", use_short)])
        elif ch == "b":
            sim = cosine(seeker[key("necessity_query", use_short)], vecs[key("state_passage", use_short)])
        else:  # c
            sim = cosine(seeker[key("will_passage", use_short)], vecs[key("will_passage", use_short)])
        order.append((cid, sim))
    order.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in order]


def analyze(model_tag):
    seeker = load_seeker(model_tag)
    cpath = vector_cache.cache_path(model_tag)
    if seeker is None or not cpath.exists():
        return None
    cache = vector_cache.load_cache(model_tag)
    params = load_params(model_tag)

    full, short = rank_full_short(seeker, cache, params)
    f_login = [r["candidate_id"] for r in full]
    s_login = [r["candidate_id"] for r in short]
    f_rank30 = {r["candidate_id"]: i + 1 for i, r in enumerate(full[:30])}
    s_rank30 = {r["candidate_id"]: i + 1 for i, r in enumerate(short[:30])}

    # 最終スコアの突合
    jac = jaccard(f_login[:30], s_login[:30])
    rho, ncommon = spearman_common(f_rank30, s_rank30)
    keep10 = top10_retention(f_login, s_login)
    # limiting_axis 一致率（TOP30 共通候補）
    f_la = {r["candidate_id"]: (r["attribution"] or {}).get("limiting_axis") for r in full[:30]}
    s_la = {r["candidate_id"]: (r["attribution"] or {}).get("limiting_axis") for r in short[:30]}
    common = set(f_la) & set(s_la)
    la_agree = (sum(1 for l in common if f_la[l] == s_la[l]) / len(common)) if common else None

    # 経路別
    ch_res = {}
    for ch in ("a", "b", "c"):
        ff = channel_ranking(seeker, cache, ch, False)
        ss = channel_ranking(seeker, cache, ch, True)
        fr = {l: i + 1 for i, l in enumerate(ff[:30])}
        sr = {l: i + 1 for i, l in enumerate(ss[:30])}
        crho, _ = spearman_common(fr, sr)
        ch_res[ch] = {"jaccard": jaccard(ff[:30], ss[:30]), "spearman": crho,
                      "keep10": top10_retention(ff, ss)}

    verdict = "PASS" if (jac >= H3_JACCARD and keep10 >= H3_TOP10_KEEP) else "FAIL"
    return {"n": len(cache), "jaccard": jac, "spearman": rho, "ncommon": ncommon,
            "keep10": keep10, "la_agree": la_agree, "channels": ch_res, "verdict": verdict}


def main():
    lines = ["# MRL 256次元検証（決定基準③）\n",
             f"H-3 合否目安: TOP30 Jaccard ≥ {H3_JACCARD} かつ TOP10残存 ≥ {H3_TOP10_KEEP}/10。\n",
             "キャッシュ済みベクトルのみ使用・再encodeなし。256版はキャッシュ _256（先頭256スライス＋L2再正規化済）。\n"]
    print(lines[0].strip())

    lines.append("## 最終スコアの突合\n")
    lines.append("| モデル | 候補数 | TOP30 Jaccard | TOP30 Spearman | TOP10残存/10 | limiting_axis一致率 | H-3 |")
    lines.append("|---|---|---|---|---|---|---|")
    rows = []
    for disp, tag in MODELS:
        r = analyze(tag)
        if r is None:
            lines.append(f"| {disp} | — | データ無し（cache/seekerベクトル未取得）| | | | — |")
            print(f"  {disp}: データ無し（eval/cache に .npz と seeker_{tag}_*.json が必要）")
            continue
        rho = f"{r['spearman']:.3f}" if r["spearman"] is not None else "n<3"
        la = f"{r['la_agree']*100:.0f}%" if r["la_agree"] is not None else "-"
        lines.append(f"| {disp} | {r['n']} | {r['jaccard']:.3f} | {rho} | {r['keep10']} | {la} | **{r['verdict']}** |")
        print(f"  {disp}: Jaccard={r['jaccard']:.3f} Spearman={rho} TOP10残存={r['keep10']}/10 la一致={la} → {r['verdict']}")
        rows.append((disp, r))

    if rows:
        lines.append("\n## 経路別（a=意志↔意志 / b=必要像→現状 / c=意志passage）フル vs 256\n")
        lines.append("| モデル | 経路 | Jaccard | Spearman | TOP10残存/10 |")
        lines.append("|---|---|---|---|---|")
        for disp, r in rows:
            for ch in ("a", "b", "c"):
                c = r["channels"][ch]
                crho = f"{c['spearman']:.3f}" if c["spearman"] is not None else "n<3"
                lines.append(f"| {disp} | {ch} | {c['jaccard']:.3f} | {crho} | {c['keep10']} |")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[保存] {OUT}")


if __name__ == "__main__":
    main()
