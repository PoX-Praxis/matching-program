#!/usr/bin/env python3
"""指示書66（PR-66C）— L（含意の判定）の計測。registration_v2 §8・§5-2・§6（L 単独・C2+b-1+L）。判定は変えない。

- 問い: 「この与え像の文は、この必要像の文の役割を満たすか」。生成させず、回答の最初のトークンの位置で
  「YES の logit − NO の logit」をスコアにする（温度 0 と同じく決定的）。理由文は書かせない。送るのは必要像の文と相手の文だけ
  （生テキスト・根拠は送らない。文は PII をマスクしてから送る）。L の出力を画面や API に出さない（計測だけ）
- モデル: llm-jp/llm-jp-3-1.8b-instruct（fp32）。Qwen/Qwen3-1.7B は決定性を測る部分集合でだけ参考として測る
- 対: **役割の 4 要素だけ**（関心型の要素には当てない）。全対は 1 回、決定性の部分集合は 5 回
- 段階: `--phase score`（モデルを読み込んでスコアを出す。途中から再開できる）と `--phase analyze`（保存したスコアと
  PR-66B の埋め込みから、規則に照らす）。**ほかの計測と同時に動かさない**
- 本番に触れない（DB・台帳・トークンを使わない）。KAORU・PA の文は local/ から読み、結果も local/66/c/ に置く
"""
import argparse
import hashlib
import json
import os
import random
import resource
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (os.path.join(ROOT, "scripts"), ROOT, os.path.join(ROOT, "src")) if p not in sys.path]
import discrimination_probe as DP  # noqa: E402
import element_probe as EP  # noqa: E402
import element_probe_v2 as E2  # noqa: E402

# プロンプト（registration_v2 §8）。登録したハッシュは {n}・{o} を埋める前のこの文字列の UTF-8。
# registration_v2 の本文は {必要像の文}・{相手の文} と書いているが、埋めたあとの文字列は同じ
PROMPT = ("次の2つの文を読んで答えてください。\n必要としている人の像: {n}\n相手の自己紹介の文: {o}\n"
          "この自己紹介の文は、必要としている人の像の役割を満たしていますか。YES か NO の一語だけで答えてください。")
PROMPT_SHA256 = "d26a2c76734cb7911d01506e67f026a6c4bbaf846dab224c9a1b8c263afd4cec"
# llm-jp の登録した id（38891・7937）は「▁YES」「▁NO」（語頭の印つき）。回答の位置（改行の直後）で "YES"・"NO" と
# 続けたときのトークンは「YES」36452・「NO」4041（印なし）なので、同じ 1 回の計算で両方のスコアを残す（どちらで数えるかは KAORU が決める）
MODELS = {"llm-jp": {"repo": "llm-jp/llm-jp-3-1.8b-instruct", "revision": "413b7929df092690b0f9449f3045baaf8deba0a5",
                     "license": "Apache-2.0", "yes": 38891, "no": 7937, "alt": (36452, 4041), "chat_kwargs": {}},
          "qwen3": {"repo": "Qwen/Qwen3-1.7B", "revision": "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
                    "license": "Apache-2.0", "yes": 14004, "no": 8996, "chat_kwargs": {"enable_thinking": False}}}
MAIN_MODEL = "llm-jp"
GRID_L = E2._grid(-4.0, 4.0, 0.1, 1)               # 81 点（0 を含む）
GRID_D = E2.GRIDS["g"]                             # C2+b-1 の δ（41 点）
DET_RUNS = 5
DET_BG = 200
OUT = os.path.join(ROOT, "local", "66", "c")


def prompt_sha():
    return hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()


def key(n, o):
    return n + "\x1f" + o


# ── 対 ───────────────────────────────────────────────────────────────────────────
def build_pairs(kaoru, sets, labels=None):
    """全対（役割の 4 要素）と決定性の部分集合。どちらも (必要像の文, 相手の文) の並び。"""
    bg, el, _ = sets
    needs = DP.need_keys(kaoru)
    nkeys = [k for k, _ in needs]
    ntext = [n["text"] for _, n in needs]
    role = [i for i, t in enumerate(ntext) if not EP.is_interest(t)]
    val = [x["text"] for x in bg["sections"]["offer"] if x["split"] == "val"]
    C = [c for c in el["C"] if c["target"] in nkeys]
    hn = [x["text"] for x in el["HN"]["a"]] + list(el["HN"]["b"])
    rk = {nkeys[i] for i in role}
    lab = [(p["need"], p["other"]) for p in (labels or {}).get("pairs", []) if p["need_key"] in rk or p["need_key"] == "PA"]
    bgp = [(ntext[n], t) for n in role for t in val]
    cp = [(ntext[nkeys.index(c["target"])], c["text"]) for c in C if nkeys.index(c["target"]) in role]
    hp = [(ntext[n], t) for n in role for t in hn]
    full = list(dict.fromkeys(bgp + cp + hp + lab))
    r = random.Random(E2.SEED_DET)
    det = list(dict.fromkeys(lab + hp + r.sample(bgp, min(DET_BG, len(bgp)))))
    return {"full": full, "det": det, "counts": {"bg": len(bgp), "C": len(cp), "HN": len(hp), "labeled": len(lab),
                                                 "full": len(full), "det": len(det)}}


# ── スコア ───────────────────────────────────────────────────────────────────────
def load_model(name):
    """モデルを fp32 で読み込み、score(必要像の文, 相手の文) を返す。YES／NO の token id とチャットの型を確かめる。"""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    m = MODELS[name]
    torch.set_num_threads(os.cpu_count())
    tok = AutoTokenizer.from_pretrained(m["repo"], revision=m["revision"])
    model = AutoModelForCausalLM.from_pretrained(m["repo"], revision=m["revision"], dtype=torch.float32)
    model.eval()
    assert tok.decode([m["yes"]]) == "YES" and tok.decode([m["no"]]) == "NO", "YES／NO の token id が違う"
    probe = tok.apply_chat_template([{"role": "user", "content": PROMPT.format(n="x", o="y")}], tokenize=False,
                                    add_generation_prompt=True, **m["chat_kwargs"])
    assert probe.endswith("\n"), "回答の位置の直前が改行でない"
    base = tok(probe, add_special_tokens=False)["input_ids"]
    natural = {w: tok(probe + w, add_special_tokens=False)["input_ids"][len(base):] for w in ("YES", "NO")}
    alt = m.get("alt")
    if alt:
        assert tok.convert_ids_to_tokens(list(alt)) == ["YES", "NO"] and natural == {"YES": [alt[0]], "NO": [alt[1]]}
    from pii_redaction import redact_text

    def score(n, o):
        ids = tok.apply_chat_template([{"role": "user", "content": PROMPT.format(n=redact_text(n), o=redact_text(o))}],
                                      tokenize=True, add_generation_prompt=True, return_tensors="pt", **m["chat_kwargs"])
        if not hasattr(ids, "shape"):                     # transformers の版によっては BatchEncoding を返す
            ids = ids["input_ids"]
        with torch.no_grad():
            logits = model(input_ids=ids).logits[0, -1]
        main = float(logits[m["yes"]] - logits[m["no"]])
        return (main, float(logits[alt[0]] - logits[alt[1]])) if alt else main

    info = {"model": m["repo"], "revision": m["revision"], "license": m["license"], "dtype": "float32",
            "yes_id": m["yes"], "no_id": m["no"], "yes_piece": tok.convert_ids_to_tokens(m["yes"]),
            "no_piece": tok.convert_ids_to_tokens(m["no"]), "natural_answer_tokens": natural,
            "alt_ids": list(alt) if alt else None, "chat": "apply_chat_template（add_generation_prompt=True"
            + ("・enable_thinking=False" if m["chat_kwargs"] else "") + "）", "prompt_sha256": prompt_sha(),
            "score": "回答の最初のトークンの位置の logit(YES) − logit(NO)（生成しない）", "pii": "文は PII をマスクしてから送る",
            "revision_cached": EP.hf_revision(m["repo"])}
    return score, info


def score_pairs(score, pairs, path, log_every=200):
    """保存済みの対は計算しない（再起動しても続きから）。1 対ごとの時間も記録する。"""
    store = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            store = json.load(f)
    sc, sec, alt = store.setdefault("scores", {}), store.setdefault("seconds", {}), store.setdefault("scores_alt", {})
    todo = [p for p in pairs if key(*p) not in sc]
    E2.log(f"スコア: 保存済み {len(pairs) - len(todo)}・残り {len(todo)}（{os.path.basename(path)}）")
    t_last = time.time()
    for i, (n, o) in enumerate(todo, 1):
        t = time.time()
        v = score(n, o)
        if isinstance(v, tuple):                          # 登録した id のスコアと、印なしの id のスコア
            sc[key(n, o)], alt[key(n, o)] = v
        else:
            sc[key(n, o)] = v
        sec[key(n, o)] = round(time.time() - t, 3)
        if i % 25 == 0 or i == len(todo):
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False)
            os.replace(tmp, path)
        if i % log_every == 0:
            E2.log(f"スコア {i}/{len(todo)}（直近 {log_every} 対で {time.time() - t_last:.0f} 秒）")
            t_last = time.time()
    store["peak_rss_gb"] = max(store.get("peak_rss_gb", 0), round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 2))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    return store


# ── 規則に照らす ────────────────────────────────────────────────────────────────────
def lmat(scores, rows, texts):
    """必要像（行）× 文の L のスコア。無い対は −inf（通らない）。"""
    return np.asarray([[scores.get(key(n, t), -np.inf) if n is not None else -np.inf for t in texts] for n in rows], dtype=float)


def analyze(kaoru, sets, emb, scores, labels=None, spot=None, det_runs=None, qwen=None, costs=None):
    t0 = time.time()
    X = E2.prepare(kaoru, sets, emb)
    nkeys, ntext, role, S = X["nkeys"], X["ntext"], X["role"], X["S"]
    neg, pos_bg, ctarget, csrc = X["neg"], X["pos_bg"], X["ctarget"], X["csrc"]
    hn = X["hn"]
    spot_hn = {h["index"]: h["label"] for h in (spot or {}).get("HN", [])}
    rows = [ntext[i] if i in role else None for i in range(len(nkeys))]   # 関心型の要素には L を当てない
    Lv = lmat(scores, rows, X["val"])
    Lc = lmat(scores, rows, X["ctext"])
    Lh = lmat(scores, rows, [t for t, _, _ in hn])
    Gv, Gc, Gh = X["Gv"], X["Gc"], X["Gh"]

    def ev(Pv, Pc, d):
        return E2.eval_persons(Pv, Pc, neg, pos_bg, nkeys, ctarget, csrc, role, d)

    # L 単独（δ_L の掃引）
    curve_l = []
    for dl in GRID_L:
        r = ev(Lv > dl, Lc > dl, dl)
        r["delta_L"] = dl
        curve_l.append(r)
    best_l, ok_l = E2.choose(curve_l, tie=lambda r: (r["delta_L"],))
    zero_l = next(r for r in curve_l if r["delta_L"] == 0.0)
    # C2+b-1+L（δ 41 点 × δ_L 81 点。全部の組み合わせ）
    curve_bl = []
    for d in GRID_D:
        b1v, b1c = Gv.b1 > d, Gc.b1 > d
        for dl in GRID_L:
            r = ev(b1v & (Lv > dl), b1c & (Lc > dl), d)
            r["delta_L"] = dl
            curve_bl.append(r)
    best_bl, ok_bl = E2.choose(curve_bl, tie=lambda r: (r["delta"], r["delta_L"]))
    zero_bl = [r for r in curve_bl if r["delta_L"] == 0.0]
    best_zero_bl, ok_zero_bl = E2.choose(zero_bl)
    E2.log("曲線: L 単独・C2+b-1+L")

    def details(Pv, Ph, r):
        prec = {f"{int(p * 100)}%": {"point": E2.exp_precision(p, r["se"], r["fp_rate"]),
                                     "upper": E2.exp_precision(p, r["se"], r["fp_upper"])} for p in (0.01, 0.03, 0.10)}
        hrow = {"a_own": 0, "a_own_n": 0, "a_any_role": 0, "a_n": 0, "b_any_role": 0, "b_n": 0, "undecided": 0}
        for j, (t, kind, tgt) in enumerate(hn):
            if spot_hn.get(j) == "判断できない":
                hrow["undecided"] += 1
                continue
            any_role = bool(Ph[role, j].any())
            if kind == "a" and tgt in nkeys:
                hrow["a_n"] += 1
                hrow["a_any_role"] += int(any_role)
                if nkeys.index(tgt) in role:                    # 自分の要素は役割の要素のときだけ（関心型には L を当てない）
                    hrow["a_own_n"] += 1
                    hrow["a_own"] += int(bool(Ph[nkeys.index(tgt), j]))
            elif kind == "b":
                hrow["b_n"] += 1
                hrow["b_any_role"] += int(any_role)
        hits = {}
        for n in role:
            for j in np.nonzero(Pv[n])[0]:
                hits.setdefault(int(j), []).append(nkeys[n])
        return {"prec": prec, "hn": hrow, "flagged": [k for k, v in r["per"].items() if v["rate"] > E2.FP_MAX],
                "passing": [{"index": j, "text": X["val"][j], "elements": ks} for j, ks in sorted(hits.items())]}

    det_l = details(Lv > best_l["delta_L"], Lh > best_l["delta_L"], best_l)
    det_l0 = details(Lv > 0, Lh > 0, zero_l)
    det_bl = details((Gv.b1 > best_bl["delta"]) & (Lv > best_bl["delta_L"]),
                     (Gh.b1 > best_bl["delta"]) & (Lh > best_bl["delta_L"]), best_bl)

    # 65-A の 48 対のうち役割の要素と PA の必要像の対（文単位。通過の有無だけ）
    labeled = None
    if labels:
        rk = {nkeys[i] for i in role}
        out = []
        for p in labels.get("pairs", []):
            if not (p["need_key"] in rk or p["need_key"] == "PA"):
                continue
            s = scores.get(key(p["need"], p["other"]))
            out.append({"id": p["id"], "kind": p["other_kind"], "need_key": p["need_key"],
                        "label": p.get("final") or p.get("provisional"),
                        "pass_best": s is not None and s > best_l["delta_L"], "pass_0": s is not None and s > 0})
        cnt = {}
        for r in out:
            c = cnt.setdefault(r["label"], [0, 0, 0])
            c[0] += int(r["pass_best"])
            c[1] += int(r["pass_0"])
            c[2] += 1
        labeled = {"rows": out, "counts": cnt}

    # 偏り（YES 寄り・NO 寄り）
    allv = np.asarray(list(scores.values()), dtype=float)
    bias = {"pairs": int(allv.size), "share_yes": float((allv > 0).mean()) if allv.size else None,
            "quantiles": {q: round(float(np.quantile(allv, q)), 2) for q in (0.01, 0.1, 0.5, 0.9, 0.99)} if allv.size else {}}

    # 決定性（同じ部分集合を 5 回）
    det = None
    if det_runs:
        common = sorted(set.intersection(*[set(r) for r in det_runs]))
        M = np.asarray([[r[k] for k in common] for r in det_runs], dtype=float)
        agree0 = float(((M > 0).all(axis=0) | (M <= 0).all(axis=0)).mean()) if common else None
        dl = best_l["delta_L"]
        agree_best = float(((M > dl).all(axis=0) | (M <= dl).all(axis=0)).mean()) if common else None
        det = {"runs": len(det_runs), "pairs": len(common), "max_abs_diff": float((M.max(axis=0) - M.min(axis=0)).max()) if common else None,
               "agree_at_0": agree0, "agree_at_best": agree_best}
    # Qwen3（参考。決定性の部分集合だけ）
    qref = None
    if qwen:
        q = qwen[0]
        ks = [k for k in q if k in scores]
        a = np.asarray([scores[k] for k in ks], dtype=float)
        b = np.asarray([q[k] for k in ks], dtype=float)
        lab_rows = {key(p["need"], p["other"]): p.get("final") or p.get("provisional") for p in (labels or {}).get("pairs", [])}
        by = {}
        for k2, v in zip(ks, b):
            lb = lab_rows.get(k2)
            if lb:
                c = by.setdefault(lb, [0, 0])
                c[0] += int(v > 0)
                c[1] += 1
        qref = {"pairs": len(ks), "share_yes": float((b > 0).mean()) if ks else None,
                "sign_agree_with_main": float(((a > 0) == (b > 0)).mean()) if ks else None,
                "spearman_like": float(np.corrcoef(np.argsort(np.argsort(a)), np.argsort(np.argsort(b)))[0, 1]) if len(ks) > 2 else None,
                "labeled_yes": by,
                "runs": len(qwen), "max_abs_diff": float(max(abs(qwen[0][k] - r[k]) for r in qwen[1:] for k in qwen[0] if k in r))
                if len(qwen) > 1 else None}
    return {"role_keys": [nkeys[i] for i in role], "grid_L": [GRID_L[0], GRID_L[-1], len(GRID_L)],
            "grid_D": [GRID_D[0], GRID_D[-1], len(GRID_D)], "combos": len(curve_bl),
            "L": {"curve": curve_l, "best": best_l, "ok": ok_l, "zero": zero_l, "detail": det_l, "detail_zero": det_l0},
            "BL": {"best": best_bl, "ok": ok_bl, "best_at_L0": best_zero_bl, "ok_at_L0": ok_zero_bl, "detail": det_bl,
                   "curve_L0": zero_bl},
            "labeled": labeled, "bias": bias, "det": det, "qwen": qref, "costs": costs,
            "sizes": {"val": len(X["val"]), "neg_persons": len(neg), "C_role": sum(1 for k in ctarget if nkeys.index(k) in role)},
            "missing": int(np.isinf(Lv[role]).sum() + sum(1 for j, k in enumerate(ctarget) if nkeys.index(k) in role and np.isinf(Lc[nkeys.index(k), j]))),
            "seconds": round(time.time() - t0, 1)}


def pct(x, nd=3):
    return E2.pct(x, nd)


def row_md(name, r, ok, extra=""):
    lo, hi = r["se_ci"]
    return (f"| {name} | {'**満たす**' if ok else '満たさない'} | {extra} | {r['fp']}/{r['trials']} | {pct(r['fp_upper'])} | "
            f"{r['pos']}/{r['pos_n']}（{pct(r['se'], 1)}） | {pct(lo, 1)}〜{pct(hi, 1)} | {r['pos_by_c']}/{r['pos_n']} |")


def to_markdown(res, info):
    L = ["# 指示書66（PR-66C）L（含意の判定）の計測 — 結果", "",
         "規則: registration_v2（確定 2026-10-24）§5・§6・§8。決定は役割の 4 要素・人 × 要素（m＝5）。", "",
         "## 条件", "",
         f"- モデル: `{info['model']}` revision `{info['revision']}`（{info['license']}）・{info['dtype']}・量子化なし",
         f"- スコア: {info['score']}。使った id: "
         + (f"YES {info['yes_id']}・NO {info['no_id']}（登録した id）" if res.get("tokens") != "natural"
            else f"YES {(info.get('alt_ids') or ['?', '?'])[0]}・NO {(info.get('alt_ids') or ['?', '?'])[1]}（回答の位置のトークン）")
         + f"。チャットの型: {info['chat']}",
         f"- プロンプトの sha256: `{info['prompt_sha256']}`（registration_v2 の登録値と同じ）。{info['pii']}",
         f"- 対: 全対 {res['counts']['full']}（検証用 {res['counts']['bg']}・C {res['counts']['C']}・hard negative {res['counts']['HN']}・"
         f"48 対のうち {res['counts']['labeled']}）。決定性の部分集合 {res['counts']['det']} 対 × {DET_RUNS} 回",
         f"- 格子: δ_L −4.0〜+4.0・0.1 刻み（81 点）。C2+b-1+L は δ 41 点 × δ_L 81 点＝{res['combos']} の組み合わせ",
         "- **この結論は 2B 級のモデルについてのものであり、L という方法そのものを否定するものではない**", ""]
    if res["costs"]:
        c = res["costs"]
        L += [f"- 費用: 1 対あたり中央値 {c['median_s']} 秒・合計 {c['total_h']} 時間・最大メモリ {c['peak_rss_gb']} GB", ""]
    L += ["## 1. 規則に照らした判定（役割の 4 要素・人単位）", "",
          "| 案 | 判定 | 作動点 | 偽陽性（人×要素） | 片側 95% 上限 | 通過（正例の人物） | 両側 95% 区間 | C の文そのもので通過 |",
          "|---|---|---|---|---|---|---|---|"]
    l, bl = res["L"], res["BL"]
    L.append(row_md("L 単独（報告する／参考の作動点）", l["best"], l["ok"], f"δ_L {l['best']['delta_L']}"))
    L.append(row_md("L 単独（δ_L＝0）", l["zero"], l["zero"]["fp_upper"] <= E2.FP_MAX and (l["zero"]["se"] or 0) >= E2.SE_MIN, "δ_L 0.0"))
    L.append(row_md("C2+b-1+L（報告する／参考の作動点）", bl["best"], bl["ok"], f"δ {bl['best']['delta']}・δ_L {bl['best']['delta_L']}"))
    L.append(row_md("C2+b-1+L（δ_L＝0 で δ を選ぶ）", bl["best_at_L0"], bl["ok_at_L0"], f"δ {bl['best_at_L0']['delta']}・δ_L 0.0"))
    L += ["", "満たす作動点が無い案は「参考の作動点」（通過率 70% 以上で偽陽性が最も少ない点。同点は δ、δ_L の大きいほう）を出している。", ""]
    for name, r, d in (("L 単独", l["best"], l["detail"]), ("C2+b-1+L", bl["best"], bl["detail"])):
        L += [f"### {name}（作動点 {('δ ' + str(r['delta']) + '・') if name != 'L 単独' else ''}δ_L {r['delta_L']}）", "",
              "| 要素 | " + " | ".join(res["role_keys"]) + " |", "|---|" + "---|" * len(res["role_keys"]),
              "| 偽陽性 | " + " | ".join(f"{r['per'][k]['fp']}/{r['per'][k]['trials']}" for k in res["role_keys"]) + " |", "",
              f"- 足した C 群（16 人）: {r['added']}/{r['added_n']}（C の文そのもので {r['added_by_c']}）",
              f"- 文単位: 検証用の文 × 役割の要素の通過 {r['sent_fp']}/{r['sent_n']}・C 群 {r['sent_c']}/{r['pos_n']}",
              "- 期待 precision（点／上限）: " + "・".join(f"π＝{k} {pct(v['point'], 1)}／{pct(v['upper'], 1)}" for k, v in d["prec"].items()),
              f"- 0.24% を超える要素: {'・'.join(d['flagged']) or 'なし'}",
              f"- hard negative: a 自分の要素（役割） {d['hn']['a_own']}/{d['hn']['a_own_n']}・a 役割のどれか {d['hn']['a_any_role']}/{d['hn']['a_n']}・"
              f"b 役割のどれか {d['hn']['b_any_role']}/{d['hn']['b_n']}（数えない {d['hn']['undecided']}）",
              f"- 通過した背景の文: {len(d['passing'])} 文", ""]
    L += ["## 2. L 単独の曲線", "", "| δ_L | 偽陽性 | 上限 | 通過 | 規則 |", "|---|---|---|---|---|"]
    for r in l["curve"]:
        ok = r["fp_upper"] <= E2.FP_MAX and (r["se"] or 0) >= E2.SE_MIN
        L.append(f"| {r['delta_L']} | {r['fp']}/{r['trials']} | {pct(r['fp_upper'])} | {r['pos']}/{r['pos_n']} | {'✓' if ok else ''} |")
    L += ["", "## 3. C2+b-1+L（δ_L＝0 で δ ごと）", "", "| δ | 偽陽性 | 上限 | 通過 |", "|---|---|---|---|"]
    for r in bl["curve_L0"]:
        L.append(f"| {r['delta']} | {r['fp']}/{r['trials']} | {pct(r['fp_upper'])} | {r['pos']}/{r['pos_n']} |")
    if res["labeled"]:
        L += ["", "## 4. 65-A のラベルつきの対（役割の要素と PA の必要像の対。文単位・通過の有無だけ）", "",
              "| ラベル | 報告する／参考の作動点で通過 | δ_L＝0 で通過 | 対 |", "|---|---|---|---|"]
        for lb, c in res["labeled"]["counts"].items():
            L.append(f"| {lb} | {c[0]} | {c[1]} | {c[2]} |")
        L += ["", "| id | 種類 | 要素 | ラベル | 報告する／参考の作動点 | δ_L＝0 |", "|---|---|---|---|---|---|"]
        for r in res["labeled"]["rows"]:
            L.append(f"| {r['id']} | {r['kind'][:18]} | {r['need_key']} | {r['label']} | {'通る' if r['pass_best'] else '—'} | "
                     f"{'通る' if r['pass_0'] else '—'} |")
    b = res["bias"]
    L += ["", "## 5. 偏り・決定性・参考のモデル", "",
          f"- 全対でスコアが 0 を超える（YES 寄り）割合: {pct(b['share_yes'], 1)}。分位点: "
          + "・".join(f"{int(q * 100)}% {v}" for q, v in ((float(k), v) for k, v in b["quantiles"].items()))]
    if res["det"]:
        d = res["det"]
        L.append(f"- 決定性: {d['runs']} 回 × {d['pairs']} 対。最大差 {d['max_abs_diff']:.2e}。通過の一致率 δ_L＝0 で "
                 f"{pct(d['agree_at_0'], 2)}・報告する／参考の作動点で {pct(d['agree_at_best'], 2)}")
    if res["qwen"]:
        q = res["qwen"]
        L.append(f"- Qwen3-1.7B（参考・部分集合 {q['pairs']} 対）: YES 寄りの割合 {pct(q['share_yes'], 1)}・llm-jp との符号の一致 "
                 f"{pct(q['sign_agree_with_main'], 1)}・順位の相関 {q['spearman_like']:.2f}・ラベルつきの対で 0 を超えた数 "
                 + "・".join(f"{k} {v[0]}/{v[1]}" for k, v in q["labeled_yes"].items())
                 + (f"・2 回の最大差 {q['max_abs_diff']:.2e}" if q["max_abs_diff"] is not None else ""))
    L += ["", f"欠けている対（スコアが無い）: {res['missing']}", ""]
    return "\n".join(L)


def main(argv=None, encode=None, scorer=None, sets=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--phase", choices=("score", "analyze"), required=True)
    ap.add_argument("--model", choices=tuple(MODELS), default=MAIN_MODEL)
    ap.add_argument("--set", choices=("full", "det"), default="full")
    ap.add_argument("--run", type=int, default=1, help="決定性の何回目か（全対の 1 回目は full のスコアを使う）")
    ap.add_argument("--tokens", choices=("registered", "natural"), default="registered",
                    help="llm-jp のスコアに使う id（registered＝登録した ▁YES・▁NO、natural＝回答の位置のトークン YES・NO）")
    ap.add_argument("--kaoru", default=os.path.join(ROOT, "local", "kaoru_v5_public.json"))
    ap.add_argument("--labels", default=os.path.join(ROOT, "local", "65a", "labels_v1.json"))
    ap.add_argument("--spot", default=os.path.join(ROOT, "local", "66", "spot_check_v1.json"))
    ap.add_argument("--emb-cache", default=os.path.join(ROOT, "local", "66", "b", "cache"))
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    assert prompt_sha() == PROMPT_SHA256, "プロンプトが登録と違う"

    def read(path):
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None

    kaoru = EP.load_kaoru(args.kaoru)
    sets = sets or E2.load_sets()
    labels, spot = read(args.labels), read(args.spot)
    P = build_pairs(kaoru, sets, labels)
    os.makedirs(args.out, exist_ok=True)

    def spath(model, s, r):
        return os.path.join(args.out, f"scores_{model}_{s}" + (f"_run{r}" if s == "det" else "") + ".json")

    if args.phase == "score":
        if scorer is None:
            scorer, info = load_model(args.model)
            with open(os.path.join(args.out, f"model_{args.model}.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=1)
        pairs = P["full"] if args.set == "full" else P["det"]
        store = score_pairs(scorer, pairs, spath(args.model, args.set, args.run))
        E2.log(f"保存しました: {len(store['scores'])} 対")
        return store
    # analyze
    emb, _, _ = DP.make_embedder(None, None, encode=encode, strict=encode is None)
    cache = E2.VecCache(args.emb_cache)
    field = "scores" if args.tokens == "registered" else "scores_alt"
    full = (read(spath(MAIN_MODEL, "full", 1)) or {}).get(field, {})
    det_runs = [{key(*p): full[key(*p)] for p in P["det"] if key(*p) in full}] if full else []
    det_runs += [(read(spath(MAIN_MODEL, "det", r)) or {}).get(field, {}) for r in range(2, DET_RUNS + 1)]
    det_runs = [r for r in det_runs if r]
    qwen = [s for s in ((read(spath("qwen3", "det", r)) or {}).get("scores", {}) for r in (1, 2)) if s]
    secs = list(((read(spath(MAIN_MODEL, "full", 1)) or {}).get("seconds") or {}).values())
    costs = None
    if secs:
        rss = (read(spath(MAIN_MODEL, "full", 1)) or {}).get("peak_rss_gb")
        costs = {"median_s": round(float(np.median(secs)), 2), "total_h": round(sum(secs) / 3600, 2), "peak_rss_gb": rss}
    res = analyze(kaoru, sets, cache.embedder(emb), full, labels=labels, spot=spot, det_runs=det_runs if len(det_runs) > 1 else None,
                  qwen=qwen or None, costs=costs)
    res["counts"] = P["counts"]
    res["tokens"] = args.tokens
    info = read(os.path.join(args.out, f"model_{MAIN_MODEL}.json")) or {
        "model": MODELS[MAIN_MODEL]["repo"], "revision": MODELS[MAIN_MODEL]["revision"], "license": MODELS[MAIN_MODEL]["license"],
        "dtype": "float32", "yes_id": MODELS[MAIN_MODEL]["yes"], "no_id": MODELS[MAIN_MODEL]["no"], "chat": "（注入）",
        "prompt_sha256": prompt_sha(), "score": "logit(YES) − logit(NO)", "pii": "文は PII をマスクしてから送る"}
    with open(os.path.join(args.out, "element_probe_l_result.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(res, info))
    with open(os.path.join(args.out, "element_probe_l_result.json"), "w", encoding="utf-8") as f:
        json.dump({"info": info, "registration": E2.REG, **res}, f, ensure_ascii=False, indent=1,
                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
    E2.log(f"保存しました: {os.path.join(args.out, 'element_probe_l_result.md')}（{res['seconds']} 秒）")
    return res


if __name__ == "__main__":
    main()
