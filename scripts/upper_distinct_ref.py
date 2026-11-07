#!/usr/bin/env python3
"""上層の連言の別の形（参考値）— 「必須ごとに別の文で覆う（1 文は 1 つの必須にしか使えない）」。判定は変えない。

KAORU の指示（2026-11-07）。registration_v2 §13 に規則の変更として記録した参考値で、本番の照合（上層 0.70 のまま）と
決定には使わない。PR-66B（element_probe_v2）の保存済みの埋め込みだけを使う（モデルを読み込まない）。

- いまの形: 必須のそれぞれを、人の文のどれかが満たす（同じ 1 文が複数の必須を満たしてよい）
- 別の形: 必須のそれぞれに、互いに違う文を割り当てて満たす（二部グラフの割り当て）
- 上層の案: R 0.70（現行）・C2 δ＝0（bg-v2 の推定用の 99% 点）
出力: local/66/b/upper_distinct.md・.json（PA との値は通過の有無だけ。本人・PA の文は書かない）
"""
import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (os.path.join(ROOT, "scripts"), ROOT, os.path.join(ROOT, "src")) if p not in sys.path]
import element_probe as EP  # noqa: E402
import element_probe_v2 as E2  # noqa: E402

UPPER = {"R 0.70": ("R", 0.70), "C2 δ=0": ("C2", 0.0), "R 0.70 + b-3": ("R+b3", 0.70), "C2 δ=0 + b-3": ("C2+b3", 0.0)}


def upper_passes(s, G, d, S):
    """上層の要素ごとの通過。b-3＝要素への固有性 g(n,s) − max_{n'≠n} g(n',s) > 0（n' は閲覧者の役割の他の要素）を足す形も。"""
    if s == "R+b3":
        base = E2.passes("R", G, d, S)
        for n, Sn in enumerate(S):
            others = [j for j in Sn if j != n]
            if others:
                base[n] &= G.g[n] - G.g[others].max(axis=0) > 0
        return base
    return E2.passes(s, G, d, S)


def covers_distinct(hit_sets):
    """必須ごとの「満たす文の集合」に、互いに違う文を 1 つずつ割り当てられるか（増加道で割り当てる）。"""
    owner = {}

    def assign(i, seen):
        for s in hit_sets[i]:
            if s in seen:
                continue
            seen.add(s)
            if s not in owner or assign(owner[s], seen):
                owner[s] = i
                return True
        return False

    return all(assign(i, set()) for i in range(len(hit_sets)))


def covers_any(hit_sets):
    return all(bool(h) for h in hit_sets)


def purpose_ok(hits, keys, musts, distinct):
    """hits[n] = 要素 n を満たす文の集合。必須が無い目的は、どれかの要素が通れば成立（v1・66B と同じ）。"""
    if not musts:
        return any(hits[k] for k in keys)
    sets = [hits[m] for m in musts]
    return covers_distinct(sets) if distinct else covers_any(sets)


def run(kaoru, sets, emb, audit=None, labels=None):
    X = E2.prepare(kaoru, sets, emb)
    nkeys, ntext, S = X["nkeys"], X["ntext"], X["S"]
    neg, pos_bg, ctarget, cidx = X["neg"], X["pos_bg"], X["ctarget"], X["cidx"]
    needs = X["needs"]
    purposes = [[i for i, k in enumerate(nkeys) if k.startswith(f"{pi}-")] for pi in range(1, len(kaoru["purposes"]) + 1)]
    actual = [[i for i in keys if needs[i][1]["must"]] for keys in purposes]
    variants = []
    for pi, keys in enumerate(purposes, 1):
        variants.append((pi, keys, actual[pi - 1], "本番の登録の必須"))
        if pi == 2 and "2-1" in nkeys:
            variants.append((pi, keys, [nkeys.index("2-1")], "2-1 を必須（§7-2）"))
    pa = EP.pa_from_audit(audit, emb) if audit else None
    out = {"persons": {}, "fits": {}, "single_double": {}, "labeled": {}, "pa": {}}
    for uname, (s, d) in UPPER.items():
        Pv, Pc = upper_passes(s, X["Gv"], d, S), upper_passes(s, X["Gc"], d, S)

        def hits_of(person):
            h = [set() for _ in nkeys]
            for g, j in person:
                col = (Pv if g == "v" else Pc)[:, j]
                for n in np.nonzero(col)[0]:
                    h[n].add((g, j))
            return h

        neg_h = [hits_of([("v", j) for j in p]) for p in neg]
        rows = []
        for pi, keys, musts, label in variants:
            fits = []
            for n in keys:
                for j in cidx[nkeys[n]]:
                    fits.append([("c", j)] + [("v", b) for b in pos_bg[j]])
            if len(keys) > 1:
                for a in cidx[nkeys[keys[0]]]:
                    for b in cidx[nkeys[keys[1]]]:
                        fits.append([("c", a), ("c", b)] + [("v", x) for x in pos_bg[a][:3]])
            truth = [all(any(g == "c" and ctarget[j] == nkeys[m] for g, j in p) for m in musts)
                     and any(g == "c" and nkeys.index(ctarget[j]) in keys for g, j in p) for p in fits]
            fh = [hits_of(p) for p in fits]
            row = {"purpose": pi, "label": label, "musts": [nkeys[m] for m in musts], "pos": sum(truth),
                   "unrel": len(neg_h), "fit_neg": len(fits) - sum(truth)}
            for tag, dist in (("now", False), ("distinct", True)):
                got = [purpose_ok(h, keys, musts, dist) for h in fh]
                row[tag] = {"unrel_pass": sum(purpose_ok(h, keys, musts, dist) for h in neg_h),
                            "fn": sum(1 for t, g in zip(truth, got) if t and not g),
                            "fit_fp": sum(1 for t, g in zip(truth, got) if not t and g)}
            rows.append(row)
        out["persons"][uname] = rows
        m1 = actual[0]
        if len(m1) >= 2:
            ov = [h for h in neg_h if covers_any([h[m] for m in m1])]
            with_both = [h for h in ov if set.intersection(*[h[m] for m in m1])]
            out.setdefault("overlap", {})[uname] = {
                "pass_now": len(ov), "with_one_sentence_both": len(with_both),
                "with_both_still_distinct": sum(1 for h in with_both if covers_distinct([h[m] for m in m1])),
                "no_overlap_ids": [f"N{i:03d}" for i, h in enumerate(neg_h)
                                   if covers_any([h[m] for m in m1]) and not set.intersection(*[h[m] for m in m1])]}
            out.setdefault("_remain", {})[uname] = [i for i, h in enumerate(neg_h) if covers_distinct([h[m] for m in m1])]
            out.setdefault("_negh", {})[uname] = neg_h
        # 1 文で両方の必須を満たす文（目的1）: 検証用の文と、通った負例の人のうちそれで成り立っていた人
        m1 = actual[0]
        if len(m1) >= 2:
            both = np.all(Pv[m1], axis=0)
            only_by_one = sum(1 for h in neg_h if covers_any([h[m] for m in m1]) and not covers_distinct([h[m] for m in m1]))
            out["single_double"][uname] = {"val_sentences_both": int(both.sum()), "val": int(both.size),
                                           "persons_lost": only_by_one}
        # 65-A の 48 対: 相手の文 1 つが目的1 の必須を両方満たすか（PA は通過の有無だけ）
        if labels and len(m1) >= 2:
            others = sorted({p["other"] for p in labels.get("pairs", [])})
            Po = E2.mat(emb, others, "passage")
            Go = E2.Group(E2.gmat(X["Qn"], Po), X["q99n"], X["q99s_of"](Po))
            Pp = upper_passes(s, Go, d, S)
            both_o = {others[j] for j in range(len(others)) if Pp[m1, j].all()}
            ids = [{"id": p["id"], "kind": p["other_kind"], "need_key": p["need_key"], "label": p.get("final") or p.get("provisional"),
                    "own_is_must": p["need_key"] in {nkeys[m] for m in m1},
                    "single_now": purpose_ok([{0} if n in m1 else set() for n in range(len(nkeys))], purposes[0], m1, False),
                    "single_distinct": purpose_ok([{0} if n in m1 else set() for n in range(len(nkeys))], purposes[0], m1, True)}
                   for p in labels.get("pairs", []) if p["other"] in both_o]
            out["labeled"][uname] = {"others": len(others), "both": len(both_o), "pairs": ids}
        # KAORU × PA（v4 の人の与え像の側は現状の全文 1 文。表示の根拠は下層・現行の作動点 0.70）
        if pa:
            Ppa = E2.mat(emb, [pa["whole"]], "passage")
            Gpa = E2.Group(E2.gmat(X["Qn"], Ppa), X["q99n"], X["q99s_of"](Ppa))
            up = upper_passes(s, Gpa, d, S)[:, 0]
            h = [{("pa", 0)} if up[n] else set() for n in range(len(nkeys))]
            low = (Gpa.g >= E2.LOWER_TH)[:, 0]
            res = []
            for pi, keys, musts, label in variants:
                res.append({"purpose": pi, "label": label, "musts": [nkeys[m] for m in musts],
                            "now": purpose_ok(h, keys, musts, False), "distinct": purpose_ok(h, keys, musts, True),
                            "lower_pairs": {nkeys[n]: bool(low[n]) for n in keys}})
            out["pa"][uname] = res
    # 追加A: 新しい決まり（R 0.70・別の文）で残った負例の人に、C2 δ＝0 を当てる
    m1 = actual[0]
    if "_remain" in out and len(m1) >= 2:
        rem = out["_remain"]["R 0.70"]
        hc = out["_negh"]["C2 δ=0"]
        out["addA"] = {"remain_R": len(rem),
                       "C2_now": sum(1 for i in rem if covers_any([hc[i][m] for m in m1])),
                       "C2_distinct": sum(1 for i in rem if covers_distinct([hc[i][m] for m in m1]))}
    out.pop("_remain", None)
    out.pop("_negh", None)
    return out


def to_markdown(res):
    L = ["# 上層の連言の別の形（参考値・事後）— 必須ごとに別の文で覆う", "",
         "指示書66C 追補2（2026-11-07・KAORU 確認済み）。**数値を見た後の発想であり、事前登録したものではない。** 66C の決定には使わない。", "",
         "判定はマッチング（必須の集合と文の集合の二部マッチング。増加道）で行う。最大スコアの要素に割り当てる方式は使わない。"
         "b-3 を足した案（R 0.70 + b-3・C2 δ＝0 + b-3）は、要素への固有性 g(n,s) − max_{n'≠n} g(n',s) > 0（n' は閲覧者の役割の他の要素）を足したもの（追加B）。", "",
         "KAORU の指示（2026-11-07）。registration_v2 §13 に規則の変更として記録。**本番の判定と決定は変えない**（上層は 0.70 のまま）。"
         "66B の保存済みの値だけを使った。", "",
         "- いまの形: 必須のそれぞれを、人の文のどれかが満たす（同じ 1 文が複数の必須を満たしてよい）",
         "- 別の形: 必須のそれぞれに、互いに違う文を割り当てて満たす（1 文は 1 つの必須にしか使えない）", "",
         "## 1・2. 負例の人の通過と、当てはまる人の取りこぼし", "",
         "| 上層の案 | 目的 | 必須 | 無関係な人の通過: いま → 別の形 | 当てはまる人の取りこぼし: いま → 別の形 | 当てはまらない組の通過: いま → 別の形 |",
         "|---|---|---|---|---|---|"]
    for u, rows in res["persons"].items():
        for r in rows:
            L.append(f"| {u} | {r['purpose']}（{r['label']}） | {'・'.join(r['musts']) or 'なし'} | "
                     f"{r['now']['unrel_pass']} → **{r['distinct']['unrel_pass']}**（/{r['unrel']}） | "
                     f"{r['now']['fn']} → **{r['distinct']['fn']}**（/{r['pos']}） | {r['now']['fit_fp']} → {r['distinct']['fit_fp']}（/{r['fit_neg']}） |")
    L += ["", "1 文で目的1 の必須を両方満たす検証用の文と、別の形で通らなくなる負例の人:", ""]
    for u, x in res["single_double"].items():
        L.append(f"- {u}: 両方を満たす文 {x['val_sentences_both']}/{x['val']}・別の形で通らなくなる人 {x['persons_lost']}")
    L += ["", "いまの形で目的1 を通った負例の人の内訳:", ""]
    for u, x in res.get("overlap", {}).items():
        L.append(f"- {u}: 通った {x['pass_now']} 人のうち、1 文で両方を満たす文を持つ人 {x['with_one_sentence_both']}"
                 f"（そのうち別の 2 文でも覆えて残る人 {x['with_both_still_distinct']}）・重なりの無い人 {len(x['no_overlap_ids'])}"
                 f"（{'・'.join(x['no_overlap_ids'][:8]) or 'なし'}{'…' if len(x['no_overlap_ids']) > 8 else ''}）")
    a = res.get("addA")
    if a:
        L += ["", f"**追加A:** 新しい決まり（R 0.70・別の文）で残った負例の人 {a['remain_R']} 人に C2 δ＝0 を当てると、"
              f"いまの形で {a['C2_now']} 人・新しい決まりで {a['C2_distinct']} 人が通る"]
    L += ["", "## 3. 65-A の 48 対で、相手の文 1 つが目的1 の必須を両方満たしていた対", ""]
    for u, x in res["labeled"].items():
        ids = "・".join(f"{p['id']}（{p['kind'][:12]}・{p['need_key']}・{p['label']}）" for p in x["pairs"]) or "なし"
        L.append(f"- {u}: 相手の文 {x['others']} 文のうち {x['both']} 文。該当する対: {ids}")
        hit = [p for p in x["pairs"] if p["label"] == "応える" and p["own_is_must"]]
        for p in hit:
            L.append(f"  - 「応える」で、自分の要素が目的1 の必須: {p['id']}（{p['need_key']}）。その文 1 つだけの人は、"
                     f"いまの形で目的1 を{'通り' if p['single_now'] else '通らず'}、新しい決まりで{'通る' if p['single_distinct'] else '落ちる'}"
                     "（要素の対そのもの（下層）は変わらない。上層は、1-2 を満たす別の文があれば通る）")
    if res["pa"]:
        L += ["", "## 4. KAORU × PA（v4 の人の与え像の側は現状の全文 1 文。通過の有無だけ）", "",
              "| 上層の案 | 目的 | 必須 | 上層: いま | 上層: 別の形 | 根拠（下層・現行の作動点）の要素の対 |", "|---|---|---|---|---|---|"]
        for u, rows in res["pa"].items():
            for r in rows:
                L.append(f"| {u} | {r['purpose']}（{r['label']}） | {'・'.join(r['musts']) or 'なし'} | {'通る' if r['now'] else '通らない'} | "
                         f"{'通る' if r['distinct'] else '通らない'} | " + "・".join(f"{k} {'出る' if v else '出ない'}" for k, v in r["lower_pairs"].items()) + " |")
    return "\n".join(L) + "\n"


def main(argv=None, encode=None, sets=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kaoru", default=os.path.join(ROOT, "local", "kaoru_v5_public.json"))
    ap.add_argument("--audit", default=os.path.join(ROOT, "local", "audit_sentences.json"))
    ap.add_argument("--labels", default=os.path.join(ROOT, "local", "65a", "labels_v1.json"))
    ap.add_argument("--emb-cache", default=os.path.join(ROOT, "local", "66", "b", "cache"))
    ap.add_argument("--out", default=os.path.join(ROOT, "local", "66", "b"))
    args = ap.parse_args(argv)

    def read(path):
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None

    cache = E2.VecCache(args.emb_cache)
    if encode is None:                                  # 保存済みの埋め込みだけを使う（無ければ止める）
        def emb(text, role):
            raise SystemExit("保存済みの埋め込みに無い文があります。PR-66B の計測を先に実行してください")
    else:
        import discrimination_probe as DP
        emb, _, _ = DP.make_embedder(None, None, encode=encode, strict=False)
    res = run(EP.load_kaoru(args.kaoru), sets or E2.load_sets(), cache.embedder(emb), read(args.audit), read(args.labels))
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "upper_distinct.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(res))
    with open(os.path.join(args.out, "upper_distinct.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return res


if __name__ == "__main__":
    main()
