"""指示書66D: 役割の言葉で書き直した必要像の計測（§2 の 1・2・3・4・5・6。参考値。決定には使わない）。

66B・66C と同じスクリプト（element_probe_v2・upper_distinct_ref）を、要素の集合だけ差し替えて走らせる。
- 必要像: 書き直した要素（local/66/d/rewrite_output.json）。要素の番号は r<目的>-<順>（中では <目的>-<順> として扱う）
- C 群: local/66/d/c_group_output.json（要素あたり 6 文。仮ラベル「応える」）
- hard negative a: 忠実性の確認の後に決めた表（--hn）。無ければ新しく書かせた分（仮）で走らせ、表には出さない
- hard negative b・背景（bg-v2）・負例 599 人（seed 6601）・正例の背景（seed 6602）・格子・規則・同点の順: 66B と同じ
- クロスエンコーダ（C6m・C6m+b-1）と L は測らない（66D §1-4・KAORU の指示 2026-11-10）
- 比べる相手: 元の要素の 66B の値（local/66/b/element_probe_v2_result.json）。AUC と「1 文で複数の要素」は
  このスクリプトで元の要素にも同じ計算をして並べる
出力: local/66/d/rewrite_probe_66d.md・.json（KAORU・PA の文は書かない。PA との値は通過の有無だけ）
"""
import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (os.path.join(ROOT, "scripts"), ROOT, os.path.join(ROOT, "src")) if p not in sys.path]
import discrimination_probe as DP  # noqa: E402
import element_probe as EP  # noqa: E402
import element_probe_v2 as E2  # noqa: E402
import upper_distinct_ref as UD  # noqa: E402

SCHEMES = tuple(s for s in E2.SCHEMES if not s.startswith("C6m"))
AUC_SCORES = {"R": lambda G: G.g, "C2": lambda G: G.d, "C2+b1": lambda G: G.b1}
FIXED_POINTS = (("R", 0.70), ("C2", 0.0))
PA_SENTINEL = {"_66d": "PA は local/pa_v4_public.json から作る"}


# ── 入力の差し替え ──────────────────────────────────────────────────────────────────
def inner_key(rid):
    """r1-2 → 1-2（element_probe_v2 の中の番号）。"""
    return rid[1:] if rid.startswith("r") else rid


def rewritten_kaoru(kaoru, rewrite):
    """元の KAORU から、目的ごとの必要像だけを書き直した要素に差し替える（向かう先・門の値はそのまま）。"""
    out = {k: v for k, v in kaoru.items() if k != "purposes"}
    out["purposes"] = []
    for pi, p in enumerate(kaoru["purposes"], 1):
        q = dict(p)
        q["needs"] = [{"text": e["text"], "must": bool(e["must"])} for e in rewrite["elements"] if e["purpose"] == pi]
        out["purposes"].append(q)
    return out


def rewritten_sets(sets, cgroup, hn_a):
    bg, el, el1 = sets
    el2 = dict(el)
    el2["C"] = [{"target": inner_key(c["target"]), "text": c["text"], "src": c.get("src", "66d")} for c in cgroup["C"]]
    el2["HN"] = {"a": [{"target": inner_key(x["target"]), "text": x["text"]} for x in hn_a],
                 "b": list(el["HN"]["b"])}
    return bg, el2, el1


def pa_from_public(path, emb):
    """local/pa_v4_public.json（監査の応答から取り出した PA の v4 の現状）から、現状の全文（本番の build_vectors と同じ形）を作る。"""
    if not path or not os.path.exists(path):
        return None
    from embedding_service import state_concat
    from pii_redaction import redact_text
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    slots = doc.get("現状") or {}
    prof = {k: redact_text(slots.get(jp) or "") for k, jp, _ in DP.STATE_SLOTS}
    return {"whole": state_concat(prof), "repro": "local/pa_v4_public.json（監査の再現の欄の割り当て）"}


# ── 追加の値（AUC・1 文で複数の要素）────────────────────────────────────────────────
def auc(pos, negs):
    pos, negs = np.asarray(pos, dtype=float), np.asarray(negs, dtype=float)
    if not pos.size or not negs.size:
        return None
    allv = np.concatenate([pos, negs])
    order = np.argsort(allv, kind="mergesort")
    ranks = np.empty(allv.size, dtype=float)
    ranks[order] = np.arange(1, allv.size + 1)
    for v in np.unique(allv):                                       # 同点は平均の順位
        m = allv == v
        ranks[m] = ranks[m].mean()
    return float((ranks[:pos.size].sum() - pos.size * (pos.size + 1) / 2) / (pos.size * negs.size))


def extras(X, chosen):
    """文単位の AUC（C 群の文 × target の要素 対 検証用の文 × 役割の要素）・人 × 要素の AUC（5 文の最大）・
    1 文で複数の要素を同時に満たす文の数（検証用 2997 文・C 群）と、そういう文を持つ負例の人の数（599 人）。"""
    nkeys, role, ctarget, neg, pos_bg = X["nkeys"], X["role"], X["ctarget"], X["neg"], X["pos_bg"]
    Gv, Gc, S = X["Gv"], X["Gc"], X["S"]
    out = {"auc": {}, "multi": {}}
    for s, f in AUC_SCORES.items():
        sv, sc = f(Gv), f(Gc)
        sp = [sc[nkeys.index(t), j] for j, t in enumerate(ctarget) if nkeys.index(t) in role]
        sn = sv[role].ravel()
        pp = [max(sc[nkeys.index(t), j], sv[nkeys.index(t), pos_bg[j]].max()) for j, t in enumerate(ctarget) if nkeys.index(t) in role]
        pn = [sv[n, p].max() for n in role for p in neg]
        out["auc"][s] = {"sentence": auc(sp, sn), "person": auc(pp, pn), "n_pos": len(sp), "n_neg": int(sn.size),
                         "n_pos_person": len(pp), "n_neg_person": len(pn)}
    points = list(FIXED_POINTS) + [(s, chosen[s]["delta"]) for s in SCHEMES]
    purposes = sorted({k.split("-")[0] for k in nkeys})
    for s, d in points:
        Pv, Pc = E2.passes(s, Gv, d, S), E2.passes(s, Gc, d, S)
        kv, kc = Pv[role].sum(axis=0), Pc[role].sum(axis=0)
        same_p = np.zeros(Pv.shape[1], dtype=bool)
        for p in purposes:
            idx = [i for i in role if nkeys[i].startswith(p + "-")]
            if len(idx) >= 2:
                same_p |= Pv[idx].sum(axis=0) >= 2
        persons = sum(1 for p in neg if (kv[p] >= 2).any())
        out["multi"][f"{s} δ={d:g}"] = {
            "val_multi": int((kv >= 2).sum()), "val_multi_same_purpose": int(same_p.sum()), "val_any": int((kv >= 1).sum()),
            "val": int(Pv.shape[1]), "c_multi": int((kc >= 2).sum()), "c": int(Pc.shape[1]),
            "persons_multi": persons, "persons": len(neg), "elements": len(role)}
    return out


# ── 報告 ─────────────────────────────────────────────────────────────────────────
def _rk(k, rewritten):
    return ("r" + k) if rewritten else k


def summary_of_rewrite(rewrite, kaoru_orig):
    rows = []
    for pi, p in enumerate(kaoru_orig["purposes"], 1):
        new = [e for e in rewrite["elements"] if e["purpose"] == pi]
        kind = next((x.get("種類") for x in rewrite["output"]["purposes"] if x.get("purpose") == pi), None)
        rows.append({"purpose": pi, "orig_n": len(p["needs"]), "orig_must": sum(1 for n in p["needs"] if n["must"]),
                     "orig_interest": sum(1 for n in p["needs"] if EP.is_interest(n["text"])),
                     "new_n": len(new), "new_must": sum(1 for e in new if e["must"]),
                     "new_types": {t: sum(1 for e in new if e["type"] == t) for t in sorted({e["type"] for e in new})},
                     "kind": kind})
    return rows


def to_markdown(res):
    b, r = res["base"], res["rew"]
    L = ["# 66D 計測（書き直した必要像・参考値）", "",
         "- 指示書66D §2 の 1・2・4・5・6（3 は KAORU の確認の後）。**決定には使わない**（registration_v2 の規則に照らした結果は参考）",
         "- 埋め込みの案だけ（C6m・C6m+b-1・L は測らない）。bg-v2・負例 599 人（seed 6601）・正例の背景（seed 6602）は 66B と同じ",
         "- 元の要素の値: 66B の取り直し（クロスエンコーダなし）。AUC と「1 文で複数の要素」は、このスクリプトで元の要素にも同じ計算をした",
         "- 人の数と文の数を分けて書く（負例の人は 599 人・検証用の文は 2997 文）", ""]
    L += ["## 6. 書き直しの出力（文の数・必須・型・種類）", "",
          "| 目的 | 元: 文 | 元: 必須 | 元: 関心型 | 書き直し: 文 | 書き直し: 必須 | 書き直し: 型 | 種類 |", "|---|---|---|---|---|---|---|---|"]
    for x in res["rewrite_summary"]:
        L.append(f"| {x['purpose']} | {x['orig_n']} | {x['orig_must']} | {x['orig_interest']} | {x['new_n']} | {x['new_must']} | "
                 + "・".join(f"{t} {n}" for t, n in x["new_types"].items()) + f" | {x['kind']} |")
    L += ["", f"決定に使う要素（関心型でない）: 元 {len(b['role_keys'])}（{'・'.join(b['role_keys'])}）→ 書き直し {len(r['role_keys'])}"
          f"（{'・'.join('r' + k for k in r['role_keys'])}）", ""]
    L += ["## 1. 下層: 人 × 要素の偽陽性と通過（registration_v2 の規則で。参考）", "",
          f"規則: 偽陽性の上側 95% ≤ {E2.FP_MAX * 100:.4f}%・通過 ≥ 70%。満たさないときは参考の作動点（通過 70% 以上で偽陽性が最少）。",
          "偽陽性は「負例の人（599 人）× 要素」の数、通過は「正例の人（C 群の文 1 つ＋背景 4 文）」の数。", "",
          "| 案 | 元: δ | 元: 偽陽性（人×要素） | 元: 通過 | 書き直し: δ | 書き直し: 偽陽性（人×要素） | 書き直し: 通過 | 書き直し: 規則 |",
          "|---|---|---|---|---|---|---|---|"]
    for s in SCHEMES:
        cb, cr = b["chosen"][s], r["chosen"][s]
        rb, rr = cb["row"], cr["row"]
        L.append(f"| {s} | {cb['delta']} | {rb['fp']}/{rb['trials']}（上側 {rb['fp_upper'] * 100:.2f}%） | {rb['pos']}/{rb['pos_n']} | "
                 f"{cr['delta']} | {rr['fp']}/{rr['trials']}（上側 {rr['fp_upper'] * 100:.2f}%） | {rr['pos']}/{rr['pos_n']} | "
                 f"{'満たす' if cr['ok'] else '満たさない'} |")
    L += ["", "同じ作動点で比べた値（人 × 要素）:", "",
          "| 作動点 | 元: 偽陽性 | 元: 通過 | 書き直し: 偽陽性 | 書き直し: 通過 |", "|---|---|---|---|---|"]
    for s, d in FIXED_POINTS:
        fb = next(x for x in b["curves"][s] if abs(x["delta"] - d) < 1e-9)
        fr = next(x for x in r["curves"][s] if abs(x["delta"] - d) < 1e-9)
        L.append(f"| {s} δ={d:g} | {fb['fp']}/{fb['trials']}（{fb['fp_rate'] * 100:.2f}%） | {fb['pos']}/{fb['pos_n']} | "
                 f"{fr['fp']}/{fr['trials']}（{fr['fp_rate'] * 100:.2f}%） | {fr['pos']}/{fr['pos_n']} |")
    L += ["", "要素ごとの偽陽性（R δ=0.70・人の数 /599）:", ""]
    for tag, x, rw in (("元", b, False), ("書き直し", r, True)):
        row = next(c for c in x["curves"]["R"] if abs(c["delta"] - 0.70) < 1e-9)
        L.append(f"- {tag}: " + "・".join(f"{_rk(k, rw)} {v['fp']}" for k, v in row["per"].items()))
    L += ["", "## 2. 下層: 見分ける力（AUC）", "",
          "文単位＝C 群の文 × その target の要素（応える）対 検証用の文 × 役割の要素（応えない）。人 × 要素＝5 文の最大。", "",
          "| スコア | 元: 文単位 | 書き直し: 文単位 | 元: 人×要素 | 書き直し: 人×要素 |", "|---|---|---|---|---|"]
    for s in AUC_SCORES:
        ab, ar = res["extras_base"]["auc"][s], res["extras_rew"]["auc"][s]
        L.append(f"| {s} | {ab['sentence']:.3f}（{ab['n_pos']}・{ab['n_neg']}） | {ar['sentence']:.3f}（{ar['n_pos']}・{ar['n_neg']}） | "
                 f"{ab['person']:.3f} | {ar['person']:.3f} |")
    L += ["", "## 4. 1 文で複数の要素を同時に満たす文（文の数と人の数を分ける）", "",
          "| 作動点 | 元: 検証用の文（2 要素以上） | 元: 同じ目的の中で 2 要素以上 | 元: そういう文を持つ負例の人 | "
          "書き直し: 検証用の文（2 要素以上） | 書き直し: 同じ目的の中で 2 要素以上 | 書き直し: そういう文を持つ負例の人 | 元: C 群 | 書き直し: C 群 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k in res["extras_rew"]["multi"]:
        mb = res["extras_base"]["multi"].get(k)
        mr = res["extras_rew"]["multi"][k]
        if mb is None:
            continue
        L.append(f"| {k} | {mb['val_multi']}/{mb['val']} 文 | {mb['val_multi_same_purpose']} 文 | {mb['persons_multi']}/{mb['persons']} 人 | "
                 f"{mr['val_multi']}/{mr['val']} 文 | {mr['val_multi_same_purpose']} 文 | {mr['persons_multi']}/{mr['persons']} 人 | "
                 f"{mb['c_multi']}/{mb['c']} 文 | {mr['c_multi']}/{mr['c']} 文 |")
    mb, mr = res["extras_base"]["multi"], res["extras_rew"]["multi"]
    keys_b = [k for k in mb if k not in mr]
    keys_r = [k for k in mr if k not in mb]
    if keys_b or keys_r:
        L += ["", "作動点が元と書き直しで違う案（それぞれの報告の作動点）:", ""]
        L += [f"- 元 {k}: 検証用の文 {mb[k]['val_multi']}/{mb[k]['val']} 文・負例の人 {mb[k]['persons_multi']}/{mb[k]['persons']} 人" for k in keys_b]
        L += [f"- 書き直し {k}: 検証用の文 {mr[k]['val_multi']}/{mr[k]['val']} 文・負例の人 {mr[k]['persons_multi']}/{mr[k]['persons']} 人" for k in keys_r]
    L += ["", "## 5. 上層（参考値）: 目的1・目的2（本番の登録の必須）", "",
          "無関係な人＝負例の人 599 人。当てはまる人＝C 群の文を持つ人（66B と同じ組み方）。",
          "「いまの形」＝必須のそれぞれを人の文のどれかが満たす。「別の形」＝必須ごとに互いに違う文で満たす（追補2）。", "",
          "| 上層の案 | 目的 | 元: 必須 | 元: 無関係な人の通過 いま → 別の形 | 元: 取りこぼし いま → 別の形 | "
          "書き直し: 必須 | 書き直し: 無関係な人の通過 いま → 別の形 | 書き直し: 取りこぼし いま → 別の形 |", "|---|---|---|---|---|---|---|---|"]
    for u in ("R 0.70", "C2 δ=0"):
        rb = [x for x in res["ud_base"]["persons"][u] if x["label"] == "本番の登録の必須"]
        rr = [x for x in res["ud_rew"]["persons"][u] if x["label"] == "本番の登録の必須"]
        for xb, xr in zip(rb, rr):
            L.append(f"| {u} | {xb['purpose']} | {'・'.join(xb['musts']) or 'なし'} | "
                     f"{xb['now']['unrel_pass']} → {xb['distinct']['unrel_pass']}（/{xb['unrel']} 人） | "
                     f"{xb['now']['fn']} → {xb['distinct']['fn']}（/{xb['pos']} 人） | "
                     f"{'・'.join('r' + m for m in xr['musts']) or 'なし'} | "
                     f"{xr['now']['unrel_pass']} → {xr['distinct']['unrel_pass']}（/{xr['unrel']} 人） | "
                     f"{xr['now']['fn']} → {xr['distinct']['fn']}（/{xr['pos']} 人） |")
    L += ["", "1 文で目的1 の必須を両方満たす検証用の文（文の数）と、別の形で通らなくなる負例の人（人の数）:", ""]
    for tag, ud in (("元", res["ud_base"]), ("書き直し", res["ud_rew"])):
        for u, x in ud["single_double"].items():
            if u in ("R 0.70", "C2 δ=0"):
                L.append(f"- {tag} {u}: 両方を満たす文 {x['val_sentences_both']}/{x['val']} 文・別の形で通らなくなる人 {x['persons_lost']} 人")
    if res["ud_rew"]["pa"]:
        L += ["", "KAORU × PA（PA の側は v4 の現状の全文 1 文。通過の有無だけ）:", "",
              "| 上層の案 | 目的 | 元: いま | 元: 別の形 | 書き直し: いま | 書き直し: 別の形 |", "|---|---|---|---|---|---|"]
        for u in ("R 0.70", "C2 δ=0"):
            rb = [x for x in res["ud_base"]["pa"][u] if x["label"] == "本番の登録の必須"]
            rr = [x for x in res["ud_rew"]["pa"][u] if x["label"] == "本番の登録の必須"]
            for xb, xr in zip(rb, rr):
                f = lambda v: "通る" if v else "通らない"  # noqa: E731
                L.append(f"| {u} | {xb['purpose']} | {f(xb['now'])} | {f(xb['distinct'])} | {f(xr['now'])} | {f(xr['distinct'])} |")
        L += ["", "根拠（下層・現行の作動点 0.70）に出る要素の対の数（KAORU の必要像 × PA の現状の全文）:", ""]
        for tag, ud, rw in (("元", res["ud_base"], False), ("書き直し", res["ud_rew"], True)):
            rows = [x for x in ud["pa"]["R 0.70"] if x["label"] == "本番の登録の必須"]
            L.append(f"- {tag}: " + "・".join(f"目的{x['purpose']} {sum(x['lower_pairs'].values())}/{len(x['lower_pairs'])}" for x in rows))
    if res.get("hn_final"):
        L += ["", "## 3. hard negative（決定の分母に入れない）", "",
              "| 案 | 元: a（自分の要素） | 元: a（役割のどれか） | 元: b（役割のどれか） | 書き直し: a（自分の要素） | 書き直し: a（役割のどれか） | 書き直し: b（役割のどれか） |",
              "|---|---|---|---|---|---|---|"]
        for s in SCHEMES:
            hb, hr = b["detail"][s]["hn"], r["detail"][s]["hn"]
            L.append(f"| {s} | {hb['a_own']}/{hb['a_n']} | {hb['a_any_role']}/{hb['a_n']} | {hb['b_any_role']}/{hb['b_n']} | "
                     f"{hr['a_own']}/{hr['a_n']} | {hr['a_any_role']}/{hr['a_n']} | {hr['b_any_role']}/{hr['b_n']} |")
    else:
        L += ["", "## 3. hard negative", "", "KAORU の忠実性の確認の後に出す（66D §1-2）。"]
    L += ["", f"走らせた時間: {res['seconds']} 秒", ""]
    return "\n".join(L)


def main(argv=None, encode=None, sets=None):
    import time
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kaoru", default=os.path.join(ROOT, "local", "kaoru_v5_public.json"))
    ap.add_argument("--rewrite", default=os.path.join(ROOT, "local", "66", "d", "rewrite_output.json"))
    ap.add_argument("--cgroup", default=os.path.join(ROOT, "local", "66", "d", "c_group_output.json"))
    ap.add_argument("--hn", default=os.path.join(ROOT, "local", "66", "d", "hn_final.json"),
                    help="忠実性の確認の後に決めた hard negative a（無ければ仮の分で走らせ、表は出さない）")
    ap.add_argument("--hn-provisional", default=os.path.join(ROOT, "local", "66", "d", "hn_a_output.json"))
    ap.add_argument("--pa", default=os.path.join(ROOT, "local", "pa_v4_public.json"))
    ap.add_argument("--base", default=os.path.join(ROOT, "local", "66", "b", "element_probe_v2_result.json"))
    ap.add_argument("--cache", default=os.path.join(ROOT, "local", "66", "d", "cache"))
    ap.add_argument("--out", default=os.path.join(ROOT, "local", "66", "d"))
    args = ap.parse_args(argv)
    t0 = time.time()

    def read(path):
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None

    emb_raw, _, info = DP.make_embedder(None, None, encode=encode, strict=encode is None)
    cache = E2.VecCache(args.cache)
    emb = cache.embedder(emb_raw)
    kaoru = EP.load_kaoru(args.kaoru)
    rewrite, cgroup = read(args.rewrite), read(args.cgroup)
    hn_final = read(args.hn)
    hn_a = (hn_final or {}).get("a") or (read(args.hn_provisional) or {}).get("a") or []
    sets = sets or E2.load_sets()
    kr = rewritten_kaoru(kaoru, rewrite)
    sr = rewritten_sets(sets, cgroup, hn_a)
    pa = pa_from_public(args.pa, emb)
    orig_pa = EP.pa_from_audit
    EP.pa_from_audit = lambda audit, e: pa if audit is PA_SENTINEL else orig_pa(audit, e)
    try:
        E2.log("書き直した要素で計測（element_probe_v2.run・クロスエンコーダなし）")
        rew = E2.run(kr, PA_SENTINEL if pa else None, sr, emb, None, cache=cache, schemes=SCHEMES)
        base = read(args.base)
        if base is None:
            E2.log("66B の結果が無いので、元の要素でも走らせる")
            base = E2.run(kaoru, PA_SENTINEL if pa else None, sets, emb, None, cache=cache, schemes=SCHEMES)
        E2.log("AUC・1 文で複数の要素")
        Xb, Xr = E2.prepare(kaoru, sets, emb), E2.prepare(kr, sr, emb)
        ex_b, ex_r = extras(Xb, base["chosen"]), extras(Xr, rew["chosen"])
        E2.log("上層（いまの形・別の形）")
        ud_b = UD.run(kaoru, sets, emb, PA_SENTINEL if pa else None, None)
        ud_r = UD.run(kr, sr, emb, PA_SENTINEL if pa else None, None)
    finally:
        EP.pa_from_audit = orig_pa
    cache.save(force=True)
    res = {"info": info, "versions": EP.versions(), "base": base, "rew": rew, "extras_base": ex_b, "extras_rew": ex_r,
           "ud_base": ud_b, "ud_rew": ud_r, "rewrite_summary": summary_of_rewrite(rewrite, kaoru),
           "hn_final": bool(hn_final), "hn_a_n": len(hn_a), "seconds": round(time.time() - t0, 1)}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "rewrite_probe_66d.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(res))
    with open(os.path.join(args.out, "rewrite_probe_66d.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(f"保存しました: {os.path.join(args.out, 'rewrite_probe_66d.md')}（{res['seconds']} 秒）")
    return res


if __name__ == "__main__":
    main()
