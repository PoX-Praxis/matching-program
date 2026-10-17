#!/usr/bin/env python3
"""指示書65-A — 要素単位の接続の計測（判定は変えない）。Code のセッションで実行する。

単位は「要素の対」（必要像の 1 文 × 相手の 1 文）。下層（この要素に応える相手）・上層（目的を満たす相手）・関心の欄
（同じ関心を持つ人）の作動点を、**事前登録した決定規則**（docs/measure/65A_decision_rules_v1.md）に照らして出す。
**本番に触れない**（DB・台帳・トークンを使わない）。**判定・閾値・門は変えない**（計測だけ）。

- 埋め込みは本番と同じ経路（scripts/discrimination_probe.make_embedder: 文 → PII のマスク → 役割ごとの接頭辞 →
  L2 正規化）。モデルは nomic-ai/nomic-embed-text-v2-moe（revision を固定して記録する）。transformers は 4.x
- 背景は固定の文集 scripts/probe_data/background_v1.json（bg-v1）だけから取る（母集団から推定しない）
- 群は scripts/probe_data/element_sets_v1.json（el-v1）と、指示書64 で Code が書いた probe_sentences.json（併記）
- KAORU・PA の文は手元のファイル（local/）から読む。**リポジトリに入れない**
- 案: R（現行 0.70）・Rg（全体で一つの 99% 点）・C2（必要像ごとの 99% 点）・C4（関心型を下層に入れない）・
  C5（平均除去 + 必要像ごとの 99% 点）・C6（クロスエンコーダ + 必要像ごとの 99% 点）

入力（既定は local/）:
  --kaoru   local/kaoru_v5_from_audit.json   KAORU の v5 の JSON（目的・必要像・与え像。関心があれば使う）
  --audit   local/audit_sentences.json       監査の応答（PA の現状と PA の必要像）
  --labels  local/65a/labels_v1.json         KAORU が付けた最終ラベル（無ければ仮ラベルで作り、ここに書き出す）
出力: local/65a/element_probe_result.md と .json（生テキスト・根拠・門の申告値は書かない。PA との値は通過の有無だけ）
"""
import argparse
import json
import os
import random
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import discrimination_probe as DP  # noqa: E402

DATA = DP.DATA
SEED = 6501
DRAWS = 200
POOL_SIZES = (10, 100)
LIST_N = (10, 30, 100, 300)
Q = 0.99
R_TH = 0.70
NOMIC = ("nomic-ai/nomic-embed-text-v2-moe", "1066b6599d099fbb93dfcb64f9c37a7c9e503e85")
NOMIC_CODE = ("nomic-ai/nomic-bert-2048", "7710840340a098cfb869c4f65e87cf2b1b70caca")
RERANKERS = {"C6": ("hotchpotch/japanese-reranker-cross-encoder-small-v1", "d7462bce0b065e0624028b412693dec055d719a0", "MIT"),
             "C6m": ("BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e", "Apache-2.0")}
INTEREST_END = "関心がある人。"
# C 群の文末だけを常体に直す規則（運用の細目 14。上から順に 1 つだけ当てる）
PLAIN = (("もらいました。", "もらった。"), ("なりました。", "なった。"), ("きました。", "きた。"), ("げました。", "げた。"),
         ("できます。", "できる。"), ("います。", "いる。"))
SCHEMES = ("R", "Rg", "C2", "C5", "C6", "C6m")
SCHEME_NAMES = {"R": "R（現行 0.70）", "Rg": "Rg（全体で一つの 99% 点）", "C2": "C2（必要像ごとの 99% 点）",
                "C5": "C5（平均除去 + 必要像ごとの 99% 点）", "C6": "C6（クロスエンコーダ small + 必要像ごとの 99% 点）",
                "C6m": "C6m（クロスエンコーダ bge-m3 + 必要像ごとの 99% 点）"}
PROVISIONAL = {("1-1", "PA全文"): "応えない", ("1-2", "PA全文"): "応えない",     # 監査の 目的1①・②（②は偽陽性寄り）
               ("2-1", "PA全文"): "応える", ("2-2", "PA全文"): "応える",        # 目的2①②
               ("PA", "KAORU与え像1"): "応えない",                              # 話題で選ばれた（哲学の比較）
               ("PA", "KAORU与え像2"): "応える"}                                # 役割での本命


def plain_style(text):
    for a, b in PLAIN:
        if text.endswith(a):
            return text[:-len(a)] + b
    return text


def is_interest(need_text):
    return need_text.rstrip().endswith(INTEREST_END)


def split_halves(items):
    return [x for i, x in enumerate(items) if i % 2 == 0], [x for i, x in enumerate(items) if i % 2 == 1]


def q99(values):
    return float(np.quantile(np.asarray(values, dtype=float), Q))


# ── モデルの版 ─────────────────────────────────────────────────────────────────────
def hf_revision(repo):
    """手元のキャッシュで main が指している revision（読み込みに使われるもの）。"""
    base = os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")), "hub",
                        "models--" + repo.replace("/", "--"))
    try:
        with open(os.path.join(base, "refs", "main"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        snaps = os.path.join(base, "snapshots")
        return ",".join(sorted(os.listdir(snaps))) if os.path.isdir(snaps) else None


def versions():
    out = {"python": sys.version.split()[0], "numpy": np.__version__}
    for mod in ("torch", "transformers", "sentence_transformers"):
        try:
            out[mod] = __import__(mod).__version__
        except ImportError:
            out[mod] = None
    out["nomic_revision"] = hf_revision(NOMIC[0])
    out["nomic_revision_pinned"] = NOMIC[1]
    out["nomic_code_revision"] = hf_revision(NOMIC_CODE[0])
    out["nomic_code_revision_pinned"] = NOMIC_CODE[1]
    return out


# ── スコア（案ごと）──────────────────────────────────────────────────────────────
class Scorer:
    """必要像の文 × 相手の文の行列を、案ごとに返す。値は手元の計測にだけ使う（画面・API に出さない）。"""

    def __init__(self, emb, est_texts, rerankers=None, role_q="query", role_p="passage"):
        self.emb, self.role_q, self.role_p = emb, role_q, role_p
        self.rerankers = rerankers or {}
        self.mu_q = self._mat(est_texts, role_q).mean(axis=0)
        self.mu_p = self._mat(est_texts, role_p).mean(axis=0)
        self._ce = {}

    def _mat(self, texts, role):
        return np.asarray([self.emb(t, role) for t in texts], dtype=float)

    @staticmethod
    def _norm(m):
        return m / np.linalg.norm(m, axis=1, keepdims=True)

    def g(self, needs, texts):
        return (1.0 + self._mat(needs, self.role_q) @ self._mat(texts, self.role_p).T) / 2.0

    def g5(self, needs, texts):
        a = self._norm(self._mat(needs, self.role_q) - self.mu_q)
        b = self._norm(self._mat(texts, self.role_p) - self.mu_p)
        return (1.0 + a @ b.T) / 2.0

    def ce(self, name, needs, texts):
        from pii_redaction import redact_text
        cache = self._ce.setdefault(name, {})
        todo = sorted({(n, t) for n in needs for t in texts} - set(cache))
        if todo:
            scores = self.rerankers[name]([(redact_text(n), redact_text(t)) for n, t in todo])
            cache.update(zip(todo, (float(s) for s in scores)))
        return np.asarray([[cache[(n, t)] for t in texts] for n in needs], dtype=float)

    def matrix(self, scheme, needs, texts):
        if scheme in ("R", "Rg", "C2"):
            return self.g(needs, texts)
        if scheme == "C5":
            return self.g5(needs, texts)
        return self.ce(scheme, needs, texts)


def load_rerankers(names):
    from sentence_transformers import CrossEncoder
    out = {}
    for name in names:
        repo, rev, _ = RERANKERS[name]
        model = CrossEncoder(repo, revision=rev, max_length=512)
        out[name] = (lambda m: (lambda pairs: m.predict(pairs, batch_size=32, show_progress_bar=False)))(model)
    return out


# ── 入力 ─────────────────────────────────────────────────────────────────────────
def load_sets():
    with open(os.path.join(DATA, "background_v1.json"), encoding="utf-8") as f:
        bg = json.load(f)
    with open(os.path.join(DATA, "element_sets_v1.json"), encoding="utf-8") as f:
        el = json.load(f)
    code, people = DP.load_data()
    return bg, el, code, people


def load_kaoru(path):
    k = DP.load_kaoru(path)
    from profile_view import parse_registration_text_report
    with open(path, encoding="utf-8") as f:
        doc, _ = parse_registration_text_report(f.read())
    k["interests"] = [str(x.get("文") or "") for x in (doc.get("関心") or []) if str(x.get("文") or "").strip()]
    return k


def pa_from_audit(audit, emb):
    """監査の応答から PA の現状（全文・各欄の文）と PA の必要像を取り出す。欄の割り当ては監査の g を再現するもの。"""
    from embedding_service import state_concat
    from pii_redaction import redact_text
    rows = DP.audit_rows(audit)
    cands = DP.slot_candidates(rows)
    if not cands:
        return None
    best = None
    for slots in cands:
        prof = {k: redact_text(slots.get(jp) or "") for k, jp, _ in DP.STATE_SLOTS}
        text = state_concat(prof)
        hits = sum(1 for r in rows if abs(r["g"] - round(DP.g_of(emb(r["need"], "query"), emb(text, "passage")), 2)) < 0.0051)
        if best is None or hits > best[0]:
            best = (hits, slots, text)
    hits, slots, text = best
    need = next((s["need"] for e in ((audit or {}).get("v5") or {}).get("sentences") or []
                 if e.get("direction") == "b_to_a" for s in e.get("sentences") or []), None)
    return {"whole": text, "slots": [(jp, slots[jp]) for _, jp, _ in DP.STATE_SLOTS if slots.get(jp)],
            "need": need, "repro": {"matched": hits, "rows": len(rows)}}


# ── 要素の対の一覧（KAORU が最終ラベルを付ける）─────────────────────────────────────────
def build_label_list(kaoru, pa, el, needs, top):
    """必要像の文・相手の文・仮ラベル（数値は載せない）。top[(need_key, group)] = その要素に最も近い文（選び方の記録）。"""
    out = []

    def add(key, need, kind, other, label, note=""):
        out.append({"id": f"L{len(out) + 1:02d}", "need_key": key, "need": need, "other_kind": kind, "other": other,
                    "provisional": label, "note": note, "final": None})

    for k, n in needs:
        if pa:
            add(k, n["text"], "PA の現状（全文）", pa["whole"], PROVISIONAL.get((k, "PA全文"), "未定"),
                "監査（10/13）の仮ラベル" if (k, "PA全文") in PROVISIONAL else "監査で仮ラベルの無い対")
    if pa and pa["need"]:
        for i, o in enumerate(kaoru["offers"], 1):
            add("PA", pa["need"], "KAORU の与え像", o, PROVISIONAL.get(("PA", f"KAORU与え像{i}"), "未定"),
                {1: "話題で選ばれた（1位）", 2: "役割での本命（2位）"}.get(i, ""))
    for k, n in needs:
        for c in el["C"]:
            if c["target"] == k:
                add(k, n["text"], "C 役割が合う（新しい会話が書いた文）", c["text"], "応える")
    for k, n in needs:
        b = top.get((k, "B"))
        if b:
            add(k, n["text"], "B 話題が近く役割が違う（最も近い文）", b,
                "判断できない" if is_interest(n["text"]) else "応えない")
        a = top.get((k, "A"))
        if a:
            add(k, n["text"], "A 無関係（最も近い文）", a, "応えない")
        g = top.get((k, "bg"))
        if g:
            add(k, n["text"], "背景の文集（最も近い文）", g, "応えない", "背景の文が実は応えていないかの確認")
    for i, o in enumerate(kaoru["offers"], 1):
        k, n = max(needs, key=lambda kn: top.get(("self", kn[0], i), -9))
        add(k, n["text"], "KAORU 自身の与え像（サニティ。C 群に入れない）", o, "未定", "自己照合")
    for k in ("1-3", "2-2"):
        n = dict(needs).get(k)
        for t in (el["E"]["for_need"].get(k) or []) if n else []:
            add(k, n["text"], "関心の文（関心型の必要像 × 相手の関心）", t, "応える")
    return out


def merge_labels(fresh, path):
    """既にある最終ラベル（final）は残す。同じ（必要像・相手の文）の対に引き継ぐ。"""
    if not (path and os.path.exists(path)):
        return fresh, False
    with open(path, encoding="utf-8") as f:
        old = {(x["need"], x["other"]): x.get("final") for x in json.load(f).get("pairs", [])}
    for x in fresh:
        x["final"] = old.get((x["need"], x["other"]))
    return fresh, any(x["final"] for x in fresh)


def label_of(x):
    return x["final"] or x["provisional"]


# ── 計測 ─────────────────────────────────────────────────────────────────────────
def run(kaoru, audit, sets, emb, rerankers, labels_path=None, emb_raw=None):
    bg, el, code, people = sets
    t0 = time.time()
    rng = random.Random(SEED)
    offer_bg = [x["text"] for x in bg["sections"]["offer"]]
    int_bg = [x["text"] for x in bg["sections"]["interest"]]
    est, val = split_halves(offer_bg)
    iest, ival = split_halves(int_bg)
    needs = DP.need_keys(kaoru)
    nkeys = [k for k, _ in needs]
    ntext = {k: n["text"] for k, n in needs}
    role_keys = [k for k in nkeys if not is_interest(ntext[k])]
    pa = pa_from_audit(audit, emb) if audit else None

    # 相手の文の全体（下層）
    groups = {"bg_est": est, "bg_val": val, "A": el["A"], "B": el["B"],
              "C": [c["text"] for c in el["C"]], "Cplain": [plain_style(c["text"]) for c in el["C"]],
              "A_code": code["A"], "B_code": code["B"], "C_code": [c["text"] for c in code["C"]],
              "self": kaoru["offers"]}
    if pa:
        groups["D"] = [pa["whole"]] + [t for _, t in pa["slots"]]
    ctarget = {"C": [c["target"] for c in el["C"]], "Cplain": [c["target"] for c in el["C"]],
               "C_code": [c["target"] for c in code["C"]]}
    names = [s for s in SCHEMES if s in ("R", "Rg", "C2", "C5") or s in rerankers]
    sc = Scorer(emb, est, rerankers)
    M = {s: {g: sc.matrix(s, [ntext[k] for k in nkeys], texts) for g, texts in groups.items()} for s in names}

    # 作動点（固定の文集の推定用だけから）
    tau = {}
    for s in names:
        if s == "R":
            tau[s] = {k: R_TH for k in nkeys}
        elif s == "Rg":
            v = q99(M["R"]["bg_est"].ravel())
            tau[s] = {k: v for k in nkeys}
        else:
            tau[s] = {k: q99(M[s]["bg_est"][i]) for i, k in enumerate(nkeys)}

    def passes(s, g):
        m = M[s][g]
        t = np.asarray([tau[s][k] for k in nkeys])[:, None]
        return m >= t if s == "R" else m > t

    P = {s: {g: passes(s, g) for g in groups} for s in names}

    # 1. 背景の分布
    dist = {}
    for s in ("R", "C5"):
        full = sc.matrix(s, [ntext[k] for k in nkeys], offer_bg)
        dist[s] = {k: {"mean": round(float(full[i].mean()), 3),
                       **{f"q{int(p * 100)}": round(float(np.quantile(full[i], p)), 3) for p in (0.5, 0.9, 0.95, 0.99)}}
                   for i, k in enumerate(nkeys)}

    # 2. 要素 1 組あたりの偽陽性・取りこぼし
    lab_pairs = []
    if pa:
        for i, k in enumerate(nkeys):
            if (k, "PA全文") in PROVISIONAL:
                lab_pairs.append((k, "D", 0))

    def element_metrics(s, keys, resp_group="C"):
        fp_n = fp_d = 0
        per = {}
        for k in keys:
            i = nkeys.index(k)
            neg = ["A", "bg_val"] + ([] if is_interest(ntext[k]) else ["B"])
            n_pass = sum(int(P[s][g][i].sum()) for g in neg)
            n_all = sum(len(groups[g]) for g in neg)
            ins = int(P[s]["bg_est"][i].sum())
            cp = [j for j, t in enumerate(ctarget[resp_group]) if t == k]
            per[k] = {"fp": n_pass, "neg": n_all, "fp_est": ins, "est": len(est),
                      "recall": sum(int(P[s][resp_group][i][j]) for j in cp), "resp": len(cp)}
            fp_n, fp_d = fp_n + n_pass, fp_d + n_all
        resp = sum(per[k]["resp"] for k in keys)
        rec = sum(per[k]["recall"] for k in keys)
        d_hits = d_n = 0
        d_fp = d_neg = 0
        for k, g, j in lab_pairs:
            if k not in keys:
                continue
            lab = label_of(next((x for x in LABELS if x["need_key"] == k and x["other_kind"].startswith("PA")),
                                {"provisional": PROVISIONAL[(k, "PA全文")], "final": None}))
            hit = bool(P[s][g][nkeys.index(k)][j])
            if lab == "応える":
                d_n, d_hits = d_n + 1, d_hits + int(hit)
            elif lab == "応えない":
                d_neg, d_fp = d_neg + 1, d_fp + int(hit)
        return {"per": per, "fp": fp_n, "neg": fp_d, "fp_rate": fp_n / fp_d if fp_d else None,
                "recall": rec, "resp": resp, "D_recall": d_hits, "D_resp": d_n, "D_fp": d_fp, "D_neg": d_neg,
                "recall_rate": (rec + d_hits) / (resp + d_n) if (resp + d_n) else None}

    # 一覧（ラベル）は閾値に依らない選び方で作る（各要素に最も近い文。g で選ぶ）
    top = {}
    for i, k in enumerate(nkeys):
        for g in ("A", "B"):
            top[(k, g)] = groups[g][int(np.argmax(M["R"][g][i]))]
        top[(k, "bg")] = val[int(np.argmax(M["R"]["bg_val"][i]))]
        for j in range(len(kaoru["offers"])):
            top[("self", k, j + 1)] = float(M["R"]["self"][i][j])
    global LABELS
    LABELS, has_final = merge_labels(build_label_list(kaoru, pa, el, needs, top), labels_path)

    elem = {s: {"all": element_metrics(s, nkeys), "role": element_metrics(s, role_keys),
                "plain": element_metrics(s, nkeys, "Cplain"), "code_C": element_metrics(s, nkeys, "C_code")}
            for s in names}
    extra_neg = {s: {g: int(P[s][g].sum()) for g in ("A_code", "B_code")} for s in names}
    c_cross = {s: int(sum(P[s]["C"][i][j] for i, k in enumerate(nkeys) for j, t in enumerate(ctarget["C"]) if t != k))
               for s in names}

    # 較正曲線（背景の推定用に対する位置の帯）
    def band_of(s, i, v):
        b = M[s]["bg_est"][i]
        pct = float((b < v).mean() * 100)
        return ("上限より上" if v > b.max() else "99〜100" if pct >= 99 else "95〜99" if pct >= 95
                else "90〜95" if pct >= 90 else "90 未満")

    bands = ("90 未満", "90〜95", "95〜99", "99〜100", "上限より上")
    calib = {}
    for s in names:
        if s == "Rg":
            continue
        rows = {b: {"resp": 0, "neg": 0, "bg": 0} for b in bands}
        for i, k in enumerate(nkeys):
            for j, t in enumerate(ctarget["C"]):
                if t == k:
                    rows[band_of(s, i, M[s]["C"][i][j])]["resp"] += 1
            for g in ["A"] + ([] if is_interest(ntext[k]) else ["B"]):
                for v in M[s][g][i]:
                    rows[band_of(s, i, v)]["neg"] += 1
            for v in M[s]["bg_val"][i]:
                rows[band_of(s, i, v)]["bg"] += 1
        calib[s] = rows
    graw = {}
    edges = [0.0, 0.62, 0.66, 0.70, 0.74, 0.78, 0.82, 1.01]
    for lo, hi in zip(edges, edges[1:]):
        r = {"resp": 0, "neg": 0, "bg": 0}
        for i, k in enumerate(nkeys):
            for j, t in enumerate(ctarget["C"]):
                if t == k and lo <= M["R"]["C"][i][j] < hi:
                    r["resp"] += 1
            for g in ["A"] + ([] if is_interest(ntext[k]) else ["B"]):
                r["neg"] += int(((M["R"][g][i] >= lo) & (M["R"][g][i] < hi)).sum())
            r["bg"] += int(((M["R"]["bg_val"][i] >= lo) & (M["R"]["bg_val"][i] < hi)).sum())
        graw[f"{lo:.2f}〜{min(hi, 1.0):.2f}"] = r

    # 人の作り方（運用の細目 12）
    unrel = [("bg_val", j) for j in range(len(val))] + [("A", j) for j in range(len(el["A"]))]

    def person_unrel(r):
        return r.sample(unrel, 3)

    persons_neg = [person_unrel(rng) for _ in range(100)]
    persons_topic = [[("B", a), ("B", a + 1)] for a in range(0, len(el["B"]) - 1, 2)]
    cidx = {k: [j for j, t in enumerate(ctarget["C"]) if t == k] for k in nkeys}

    def person_pass(s, k, person):
        i = nkeys.index(k)
        return any(bool(P[s][g][i][j]) for g, j in person)

    def purpose_pass(s, keys, musts, person):
        hit = {k: person_pass(s, k, person) for k in keys}
        return all(hit[k] for k in musts) and any(hit.values())

    # 3. 必須の数ごとの目的の成立
    purposes = [[k for k in nkeys if k.startswith(f"{pi}-")] for pi in range(1, len(kaoru["purposes"]) + 1)]
    actual_musts = [[f"{pi}-{j}" for j, n in enumerate(p["needs"], 1) if n["must"]]
                    for pi, p in enumerate(kaoru["purposes"], 1)]
    upper = {}
    for s in names:
        upper[s] = []
        for pi, keys in enumerate(purposes, 1):
            for m in range(0, min(2, len(keys)) + 1):
                musts = keys[:m]
                fits = []
                for k1 in keys:
                    for a in cidx[k1]:
                        fits.append([("C", a)] + person_unrel(random.Random(SEED + a))[:2])
                for a in cidx[keys[0]]:
                    for b in cidx[keys[1]] if len(keys) > 1 else []:
                        fits.append([("C", a), ("C", b)])
                truth = [all(any(g == "C" and ctarget["C"][j] == k for g, j in p) for k in musts)
                         and any(g == "C" and ctarget["C"][j] in keys for g, j in p) for p in fits]
                got = [purpose_pass(s, keys, musts, p) for p in fits]
                pos = sum(truth)
                fn = sum(1 for t, g in zip(truth, got) if t and not g)
                fp_fit = sum(1 for t, g in zip(truth, got) if not t and g)
                neg_unrel = sum(purpose_pass(s, keys, musts, p) for p in persons_neg)
                neg_topic = sum(purpose_pass(s, keys, musts, p) for p in persons_topic)
                upper[s].append({"purpose": pi, "musts": m, "must_keys": musts, "actual": musts == actual_musts[pi - 1],
                                 "pos": pos, "fn": fn, "fit_neg": len(fits) - pos, "fit_fp": fp_fit,
                                 "unrel_pass": neg_unrel, "unrel": len(persons_neg),
                                 "topic_pass": neg_topic, "topic": len(persons_topic)})

    # 4・5. 「該当なし」と母集団非依存性
    def none_rates(s, pool_n, keys):
        r = random.Random(SEED + pool_n)
        i_none = i_tot = ii_found = ii_self = ii_tot = 0
        for k in keys:
            for d in range(DRAWS):
                pool = [person_unrel(r) for _ in range(pool_n)]
                i_tot += 1
                i_none += int(not any(person_pass(s, k, p) for p in pool))
                if cidx[k]:
                    resp = [("C", cidx[k][d % len(cidx[k])])] + person_unrel(r)[:2]
                    pool2 = pool[:-1] + [resp]
                    ii_tot += 1
                    ii_found += int(any(person_pass(s, k, p) for p in pool2))
                    ii_self += int(person_pass(s, k, resp))
        return {"i": i_none / i_tot, "ii": ii_found / ii_tot if ii_tot else None,
                "ii_self": ii_self / ii_tot if ii_tot else None}

    def none_rates_purpose(s, pool_n):
        r = random.Random(SEED + 7 * pool_n)
        out = []
        for pi, keys in enumerate(purposes, 1):
            musts = actual_musts[pi - 1]
            fitk = musts or keys[:1]
            i_none = ii_found = ii_self = 0
            for d in range(DRAWS):
                pool = [person_unrel(r) for _ in range(pool_n)]
                i_none += int(not any(purpose_pass(s, keys, musts, p) for p in pool))
                fit = [("C", cidx[k][d % len(cidx[k])]) for k in fitk]
                ii_found += int(any(purpose_pass(s, keys, musts, p) for p in pool[:-1] + [fit]))
                ii_self += int(purpose_pass(s, keys, musts, fit))
            out.append({"purpose": pi, "i": i_none / DRAWS, "ii": ii_found / DRAWS, "ii_self": ii_self / DRAWS})
        return out

    none = {s: {n: {"all": none_rates(s, n, nkeys), "role": none_rates(s, n, role_keys),
                    "purpose": none_rates_purpose(s, n)} for n in POOL_SIZES} for s in names}

    def list_len(s, n_pool):
        r = random.Random(SEED + 3 * n_pool)
        shown = []
        for k in nkeys:
            c = 0
            for _ in range(DRAWS // 4):
                c += sum(person_pass(s, k, person_unrel(r)) for _ in range(n_pool))
            shown.append(c / (DRAWS // 4))
        return {"per_need": dict(zip(nkeys, (round(x, 2) for x in shown))), "mean": round(float(np.mean(shown)), 2)}

    lists = {s: {n: list_len(s, n) for n in LIST_N} for s in names}

    fixed = [(k, "C", j) for k in nkeys for j in cidx[k]] + [(k, "A", j) for k in nkeys for j in range(5)]
    if pa:
        fixed += [(k, "D", 0) for k in nkeys]

    def decide(s, k, g, j, pool, control=False):
        i = nkeys.index(k)
        v = M[s][g][i][j]
        if not control:
            return bool(P[s][g][i][j])
        if not pool:
            return False
        vals = [M[s][gg][i][jj] for person in pool for gg, jj in person]
        return bool(v > q99(vals))

    indep = {}
    for s in names + ["母集団の99%点（対照）"]:
        base = s if s in names else "R"
        ctl = s not in names
        r = random.Random(SEED + 11)
        pools = {n: [person_unrel(r) for _ in range(n)] for n in (0, 10, 100)}
        res = {n: [decide(base, k, g, j, pools[n], ctl) for k, g, j in fixed] for n in pools}
        flips = sum(1 for a, b, c in zip(res[0], res[10], res[100]) if not (a == b == c))
        indep[s] = {"pairs": len(fixed), "flips": flips}

    # 6. 関心の欄（(i) 対称・(ii) query × document）
    E = el["E"]
    viewers = [(a, b, True) for a, b in E["match"]] + [(b, a, True) for a, b in E["match"]] + \
              [(a, b, False) for a, b in E["nonmatch"]] + [(b, a, False) for a, b in E["nonmatch"]]
    vt = sorted({v for v, _, _ in viewers})
    interest = {}
    for mode, (rq, rp) in (("i", ("symmetric", "symmetric")), ("ii", ("query", "passage"))):
        isc = Scorer(emb, iest, role_q=rq, role_p=rp)
        th = {v: q99(isc.g([v], iest)[0]) for v in vt}
        th_offer = {v: q99(isc.g([v], est)[0]) for v in vt}
        out = {}
        for tname, tt in (("関心の文の節", th), ("与え像の形の節", th_offer)):
            rec = sum(1 for v, o, m in viewers if m and isc.g([v], [o])[0][0] > tt[v])
            fpn = sum(1 for v, o, m in viewers if not m and isc.g([v], [o])[0][0] > tt[v])
            bgv = isc.g(vt, ival)
            fpb = int(sum((bgv[i] > tt[v]).sum() for i, v in enumerate(vt)))
            out[tname] = {"recall": rec, "resp": sum(1 for *_, m in viewers if m), "fp_nonmatch": fpn,
                          "nonmatch": sum(1 for *_, m in viewers if not m), "fp_bg": fpb, "bg": len(vt) * len(ival)}
        # 「該当なし」（関心の欄）
        r = random.Random(SEED + 13)
        ivals = list(range(len(ival)))
        bgm = isc.g(vt, ival)
        nr = {}
        for n in POOL_SIZES:
            i_none = ii_found = ii_self = tot = 0
            for v, o, _ in [x for x in viewers if x[2]]:
                row = bgm[vt.index(v)]
                self_hit = isc.g([v], [o])[0][0] > th[v]
                for d in range(DRAWS // 4):
                    pool = [r.sample(ivals, 2) for _ in range(n)]
                    tot += 1
                    i_none += int(not any(row[a] > th[v] or row[b] > th[v] for a, b in pool))
                    ii_self += int(self_hit)
                    ii_found += int(self_hit or any(row[a] > th[v] or row[b] > th[v] for a, b in pool[:-1]))
            nr[n] = {"i": i_none / tot, "ii": ii_found / tot, "ii_self": ii_self / tot}
        out["none"] = nr
        interest[mode] = out
    # 関心型の必要像 × 相手の関心（65-B §3 の (2)）
    ineed = {}
    isc2 = Scorer(emb, iest, role_q="query", role_p="passage")
    for k in [k for k in nkeys if is_interest(ntext[k])]:
        fn = E["for_need"].get(k) or []
        th = q99(isc2.g([ntext[k]], iest)[0])
        ineed[k] = {"recall": int((isc2.g([ntext[k]], fn)[0] > th).sum()) if fn else 0, "resp": len(fn),
                    "fp_bg": int((isc2.g([ntext[k]], ival)[0] > th).sum()), "bg": len(ival),
                    "r070": int((isc2.g([ntext[k]], fn)[0] >= R_TH).sum()) if fn else 0,
                    "r070_bg": int((isc2.g([ntext[k]], ival)[0] >= R_TH).sum())}

    # 7. 方向B・v5 × v5（架空の人の必要像。指示書64 で Code が書いた人）
    fneeds = [(p["id"], p["type"], qi, ni, n["文"], bool(n.get("必須")))
              for p in people for qi, q in enumerate(p["purposes"]) for ni, n in enumerate(q["必要像"])]
    ftexts = sorted({x[4] for x in fneeds} | ({pa["need"]} if pa and pa["need"] else set()))
    foffers = [(p["id"], p["type"], t) for p in people for t in p["与え像"]]
    dirb, v5v5 = {}, {}
    for s in [x for x in names if x != "C6m"]:
        Mt = sc.matrix(s, ftexts, est)
        sc.matrix(s, ftexts, [o for _, _, o in foffers] + kaoru["offers"])      # クロスエンコーダの値をまとめて計算
        th = {t: (R_TH if s == "R" else q99(Mt[i]) if s != "Rg" else tau["Rg"][nkeys[0]]) for i, t in enumerate(ftexts)}

        def ok(t, o):
            v = sc.matrix(s, [t], [o])[0][0]
            return v >= th[t] if s == "R" else v > th[t]

        rows = {}
        for pid, typ, qi, ni, t, must in fneeds:
            rows.setdefault(typ, [0, 0])
            rows[typ][1] += 1
            rows[typ][0] += int(any(ok(t, o) for o in kaoru["offers"]))
        pa_rows = [ok(pa["need"], o) for o in kaoru["offers"]] if pa and pa["need"] else []
        dirb[s] = {"by_type": rows, "pa": pa_rows}
        cells = {}
        for p in people:
            for q in people:
                if p["id"] == q["id"]:
                    continue
                key = f"{p['type']}→{q['type']}"
                c = cells.setdefault(key, {"elem": 0, "elem_n": 0, "purpose": 0, "purpose_n": 0})
                for pur in p["purposes"]:
                    hits = [any(ok(n["文"], o) for o in q["与え像"]) for n in pur["必要像"]]
                    c["elem"] += sum(hits)
                    c["elem_n"] += len(hits)
                    c["purpose_n"] += 1
                    c["purpose"] += int(any(hits) and all(h for h, n in zip(hits, pur["必要像"]) if n.get("必須")))
        v5v5[s] = cells

    # 決定性（同じ入力を 2 回計算する）
    det = None
    if emb_raw:
        sample = [ntext[k] for k in nkeys] + est[:20]
        a = np.asarray([emb(t, "passage") for t in sample])
        b = np.asarray([emb_raw(t, "passage") for t in sample])
        det = {"texts": len(sample), "max_abs_diff": float(np.abs(a - b).max())}
    fingerprint = {s: np.round(M[s]["bg_est"], 6).tolist() for s in names}

    return {"needs": [{"key": k, "must": n["must"], "interest": is_interest(n["text"]), "head": n["text"][:24]}
                      for k, n in needs],
            "schemes": names, "tau": {s: {k: round(v, 4) for k, v in tau[s].items()} for s in names},
            "dist": dist, "elem": elem, "extra_neg": extra_neg, "c_cross": c_cross, "calib": calib, "graw": graw,
            "upper": upper, "none": none, "lists": lists, "indep": indep, "interest": interest, "ineed": ineed,
            "dirb": dirb, "v5v5": v5v5, "det": det, "fingerprint": fingerprint,
            "labels": LABELS, "has_final": has_final, "pa_repro": pa["repro"] if pa else None,
            "sizes": {g: len(v) for g, v in groups.items()} | {"interest_est": len(iest), "interest_val": len(ival)},
            "kaoru_interests": len(kaoru.get("interests") or []), "seconds": round(time.time() - t0, 1)}


LABELS = []


# ── 規則に照らす ────────────────────────────────────────────────────────────────────
def verdicts(res):
    out = {"lower": {}, "interest": {}, "none": {}, "upper": None}
    for s in res["schemes"]:
        for scope in ("all", "role"):
            e = res["elem"][s][scope]
            out["lower"][f"{s}/{scope}"] = {"fp_rate": e["fp_rate"], "recall_rate": e["recall_rate"],
                                            "ok": e["fp_rate"] is not None and e["fp_rate"] <= 0.01
                                            and e["recall_rate"] is not None and e["recall_rate"] >= 0.70}
        for layer in ("all", "role"):
            a, b = res["none"][s][10][layer], res["none"][s][100][layer]
            out["none"][f"{s}/要素/{layer}"] = {"i100": b["i"], "ii100": b["ii"], "i10": a["i"],
                                              "ok": b["i"] >= 0.95 and (b["ii"] or 0) >= 0.70 and a["i"] >= 0.95}
        for pa, pb in zip(res["none"][s][10]["purpose"], res["none"][s][100]["purpose"]):
            out["none"][f"{s}/目的{pa['purpose']}"] = {"i100": pb["i"], "ii100": pb["ii"], "i10": pa["i"],
                                                      "ok": pb["i"] >= 0.95 and pb["ii"] >= 0.70 and pa["i"] >= 0.95}
    for mode in ("i", "ii"):
        x = res["interest"][mode]["関心の文の節"]
        fp = (x["fp_nonmatch"] + x["fp_bg"]) / (x["nonmatch"] + x["bg"])
        rec = x["recall"] / x["resp"]
        n = res["interest"][mode]["none"]
        out["interest"][mode] = {"fp_rate": fp, "recall_rate": rec, "ok": fp <= 0.01 and rec >= 0.70,
                                 "none_ok": n[100]["i"] >= 0.95 and n[100]["ii"] >= 0.70 and n[10]["i"] >= 0.95}
    r = [u for u in res["upper"]["R"] if u["actual"]]
    out["upper"] = {"trigger": any(u["unrel_pass"] or u["topic_pass"] for u in r),
                    "rows": [(u["purpose"], u["unrel_pass"], u["topic_pass"]) for u in r]}
    return out


# ── 出力 ─────────────────────────────────────────────────────────────────────────
def pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def to_markdown(res, info, ver):
    tag = "（最終ラベルでの結果）" if res["has_final"] else "（**仮ラベルでの結果**）"
    V = verdicts(res)
    L = [f"# 指示書65-A 要素単位の接続の計測 — 結果 {tag}", "",
         "## 条件", "",
         f"- 埋め込み: `{info['model_tag']}`（{info['mode']}）。revision `{ver['nomic_revision']}`"
         f"（固定値 `{ver['nomic_revision_pinned']}`）・remote code `{ver['nomic_code_revision']}`",
         f"- transformers {ver['transformers']}・sentence-transformers {ver['sentence_transformers']}・torch {ver['torch']}",
         f"- 接頭辞: query=`{info['prefix'].get('query')}` / passage=`{info['prefix'].get('passage')}`"
         f" / symmetric=`{info['prefix'].get('symmetric')}`。L2 正規化。g = (1 + cos) / 2",
         "- 背景: bg-v1（与え像の形 300・関心の文 150。偶数＝推定用・奇数＝検証用）。群: el-v1",
         f"- 件数: {json.dumps(res['sizes'], ensure_ascii=False)}", f"- 計算時間: {res['seconds']} 秒", ""]
    L += ["## 必要像（番号）", "", "| 番号 | 欠かせない | 関心型 | 文（先頭 24 字） |", "|---|---|---|---|"]
    L += [f"| {n['key']} | {'✓' if n['must'] else ''} | {'✓' if n['interest'] else ''} | {n['head']}… |" for n in res["needs"]]
    L += ["", "## 1. 背景の分布（与え像の形の節 300 文全体）", "",
          "| 必要像 | 平均 | 50% | 90% | 95% | 99% | C5: 平均 | 50% | 90% | 95% | 99% |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n in res["needs"]:
        a, b = res["dist"]["R"][n["key"]], res["dist"]["C5"][n["key"]]
        L.append(f"| {n['key']} | {a['mean']} | {a['q50']} | {a['q90']} | {a['q95']} | {a['q99']} | "
                 f"{b['mean']} | {b['q50']} | {b['q90']} | {b['q95']} | {b['q99']} |")
    L += ["", "作動点（推定用の 99% 点。R は 0.70）:", "", "| 案 | " + " | ".join(n["key"] for n in res["needs"]) + " |",
          "|---|" + "---|" * len(res["needs"])]
    L += [f"| {s} | " + " | ".join(str(res["tau"][s][n["key"]]) for n in res["needs"]) + " |" for s in res["schemes"]]
    L += ["", "## 2. 要素 1 組あたりの偽陽性・取りこぼし（下層）", "",
          "偽陽性＝応えない文（A・背景の検証用・役割の必要像に対する B）のうち通った数。取りこぼし＝応える文（C 群の target・"
          "PA の全文で「応える」の対）のうち通らなかった数。", "",
          "| 案 | 範囲 | 偽陽性 | 率 | 推定用での通過 | 応える文の通過 | 率 | 規則 |", "|---|---|---|---|---|---|---|---|"]
    for s in res["schemes"]:
        for scope, label in (("all", "全要素"), ("role", "役割の要素だけ（C4）")):
            e = res["elem"][s][scope]
            ins = sum(p["fp_est"] for p in e["per"].values())
            ok = V["lower"][f"{s}/{scope}"]["ok"]
            L.append(f"| {s} | {label} | {e['fp']}/{e['neg']} | {pct(e['fp_rate'])} | {ins}/{len(e['per']) * res['sizes']['bg_est']} | "
                     f"{e['recall'] + e['D_recall']}/{e['resp'] + e['D_resp']} | {pct(e['recall_rate'])} | {'満たす' if ok else '満たさない'} |")
    L += ["", "必要像ごと（全要素）:", "", "| 案 | " + " | ".join(n["key"] for n in res["needs"]) + " |",
          "|---|" + "---|" * len(res["needs"])]
    for s in res["schemes"]:
        per = res["elem"][s]["all"]["per"]
        L.append(f"| {s} 偽陽性 | " + " | ".join(f"{per[n['key']]['fp']}/{per[n['key']]['neg']}" for n in res["needs"]) + " |")
        L.append(f"| {s} 応える | " + " | ".join(f"{per[n['key']]['recall']}/{per[n['key']]['resp']}" for n in res["needs"]) + " |")
    L += ["", "併記:", "", "| 案 | PA の全文: 応えるの通過 | PA の全文: 応えないの通過 | C の常体版の通過 | 指示書64 の C（Code）の通過 | "
          "指示書64 の A・B（Code）の通過 | C × 別の要素の通過 |", "|---|---|---|---|---|---|---|"]
    for s in res["schemes"]:
        e, p, c = res["elem"][s]["all"], res["elem"][s]["plain"], res["elem"][s]["code_C"]
        x = res["extra_neg"][s]
        L.append(f"| {s} | {e['D_recall']}/{e['D_resp']} | {e['D_fp']}/{e['D_neg']} | {p['recall']}/{p['resp']} | "
                 f"{c['recall']}/{c['resp']} | A {x['A_code']}/{6 * 20}・B {x['B_code']}/{6 * 10} | {res['c_cross'][s]} |")
    L += ["", "### 較正曲線（背景の推定用に対する位置の帯ごとの件数と、応えている割合）", ""]
    for s, rows in res["calib"].items():
        L += [f"**{SCHEME_NAMES[s]}**", "", "| 帯（背景での分位） | 応える | 応えない（A・B） | 背景（検証用） | 応えている割合 |",
              "|---|---|---|---|---|"]
        for b, r in rows.items():
            tot = r["resp"] + r["neg"] + r["bg"]
            L.append(f"| {b} | {r['resp']} | {r['neg']} | {r['bg']} | {pct(r['resp'] / tot) if tot else '—'} |")
        L.append("")
    L += ["**R の g の帯**", "", "| g | 応える | 応えない（A・B） | 背景（検証用） | 応えている割合 |", "|---|---|---|---|---|"]
    for b, r in res["graw"].items():
        tot = r["resp"] + r["neg"] + r["bg"]
        L.append(f"| {b} | {r['resp']} | {r['neg']} | {r['bg']} | {pct(r['resp'] / tot) if tot else '—'} |")
    L += ["", "## 3. 必須の数ごとの目的の成立（上層）", "",
          "応える人＝C 群の文を持つ架空の人。無関係＝背景・A から 3 文の人 100 人。話題だけ＝B の 2 文の人 5 人。", "",
          "| 案 | 目的 | 欠かせない | 実際の設定 | 取りこぼし | 応えない人の通過（C の人） | 無関係の通過 | 話題だけの通過 |",
          "|---|---|---|---|---|---|---|---|"]
    for s in res["schemes"]:
        for u in res["upper"][s]:
            L.append(f"| {s} | {u['purpose']} | {u['musts']}（{'・'.join(u['must_keys']) or 'なし'}） | {'✓' if u['actual'] else ''} | "
                     f"{u['fn']}/{u['pos']} | {u['fit_fp']}/{u['fit_neg']} | {u['unrel_pass']}/{u['unrel']} | {u['topic_pass']}/{u['topic']} |")
    L += ["", "## 4. 母集団非依存性（無関係な人を 0／10／100 人足したときに、判定が変わった対の数）", "",
          "| 案 | 対 | 変わった対 |", "|---|---|---|"]
    L += [f"| {s} | {v['pairs']} | {v['flips']} |" for s, v in res["indep"].items()]
    L += ["", "## 5. 「該当なし」（(i) 応える人のいないプールで該当なし・(ii) 応える人のいるプールで該当なしを返さない・"
          "(iii) プール 10 人での (i)）", "",
          "| 案 | 層 | (i) 100 人 | (ii) 100 人 | うち応える人自身が通る | (iii) 10 人での (i) | 規則 |", "|---|---|---|---|---|---|---|"]
    for s in res["schemes"]:
        for layer, label in (("all", "要素（全要素）"), ("role", "要素（役割だけ）")):
            a, b = res["none"][s][10][layer], res["none"][s][100][layer]
            ok = V["none"][f"{s}/要素/{layer}"]["ok"]
            L.append(f"| {s} | {label} | {pct(b['i'])} | {pct(b['ii'])} | {pct(b['ii_self'])} | {pct(a['i'])} | {'満たす' if ok else '満たさない'} |")
        for pa, pb in zip(res["none"][s][10]["purpose"], res["none"][s][100]["purpose"]):
            ok = V["none"][f"{s}/目的{pa['purpose']}"]["ok"]
            L.append(f"| {s} | 目的{pa['purpose']} | {pct(pb['i'])} | {pct(pb['ii'])} | {pct(pb['ii_self'])} | {pct(pa['i'])} | {'満たす' if ok else '満たさない'} |")
    for mode in ("i", "ii"):
        n = res["interest"][mode]["none"]
        L.append(f"| — | 関心の欄 ({mode}) | {pct(n[100]['i'])} | {pct(n[100]['ii'])} | {pct(n[100]['ii_self'])} | {pct(n[10]['i'])} | "
                 f"{'満たす' if V['interest'][mode]['none_ok'] else '満たさない'} |")
    L += ["", "## 6. 下層の一覧の長さ（無関係な人だけのプールで、要素あたりに出る人数＝すべて偽り）", "",
          "| 案 | " + " | ".join(f"N={n}" for n in LIST_N) + " |", "|---|" + "---|" * len(LIST_N)]
    L += [f"| {s} | " + " | ".join(str(res["lists"][s][n]["mean"]) for n in LIST_N) + " |" for s in res["schemes"]]
    L += ["", "## 7. 方向B・v5 × v5（指示書64 で Code が書いた架空の 18 人）", "",
          "方向B: 架空の人の必要像のうち、KAORU の与え像のどれかで通る数（型ごと）。PA の必要像 × KAORU の与え像 1〜3 の通過。", "",
          "| 案 | 合う | 話題だけ | 無関係 | PA の必要像 × 与え像 1・2・3 |", "|---|---|---|---|---|"]
    for s, d in res["dirb"].items():
        bt = d["by_type"]
        L.append(f"| {s} | {bt.get('fit', [0, 0])[0]}/{bt.get('fit', [0, 0])[1]} | {bt.get('topic', [0, 0])[0]}/{bt.get('topic', [0, 0])[1]} | "
                 f"{bt.get('unrelated', [0, 0])[0]}/{bt.get('unrelated', [0, 0])[1]} | {'・'.join('通る' if x else '通らない' for x in d['pa']) or '—'} |")
    L += ["", "v5 × v5（求める側の型→応える側の型。要素の通過・目的の成立。門は当てない）:", "",
          "| 案 | " + " | ".join(sorted(next(iter(res["v5v5"].values())))) + " |", "|---|" + "---|" * len(next(iter(res["v5v5"].values())))]
    for s, cells in res["v5v5"].items():
        L.append(f"| {s} | " + " | ".join(f"要素 {c['elem']}/{c['elem_n']}・目的 {c['purpose']}/{c['purpose_n']}"
                                          for _, c in sorted(cells.items())) + " |")
    L += ["", "## 関心の欄（(i) 対称・(ii) query × document）", "",
          "| 比べ方 | 作動点の背景 | 一致の通過 | 不一致の通過 | 背景（検証用）の通過 | 規則 |", "|---|---|---|---|---|---|"]
    for mode in ("i", "ii"):
        for tname, x in ((k, v) for k, v in res["interest"][mode].items() if k != "none"):
            ok = V["interest"][mode]["ok"] if tname == "関心の文の節" else None
            L.append(f"| ({mode}) | {tname} | {x['recall']}/{x['resp']} | {x['fp_nonmatch']}/{x['nonmatch']} | {x['fp_bg']}/{x['bg']} | "
                     f"{'' if ok is None else '満たす' if ok else '満たさない'} |")
    L += ["", "関心型の必要像 × 相手の関心（query × document・関心の文の節の 99% 点。括弧内は現行 0.70）:", "",
          "| 必要像 | 応える関心の文の通過 | 背景（検証用）の通過 |", "|---|---|---|"]
    L += [f"| {k} | {v['recall']}/{v['resp']}（{v['r070']}） | {v['fp_bg']}/{v['bg']}（{v['r070_bg']}） |" for k, v in res["ineed"].items()]
    if res["det"]:
        L += ["", f"## 決定性: 同じ文 {res['det']['texts']} 件を 2 回埋め込んだ差の最大 {res['det']['max_abs_diff']:.2e}"]
    L += ["", "## 規則に照らした判定", ""]
    L += [f"- 上層: 現行 0.70 で無関係・話題だけの人が通った例 {'あり' if V['upper']['trigger'] else 'なし'}"
          f"（目的ごと〔無関係・話題だけ〕: {V['upper']['rows']}）"]
    L += [f"- 下層 {k}: {'満たす' if v['ok'] else '満たさない'}（偽陽性 {pct(v['fp_rate'])}・通過 {pct(v['recall_rate'])}）"
          for k, v in V["lower"].items()]
    L += [f"- 関心の欄 ({k}): {'満たす' if v['ok'] else '満たさない'}（偽陽性 {pct(v['fp_rate'])}・通過 {pct(v['recall_rate'])}）"
          for k, v in V["interest"].items()]
    L += [f"- 該当なし {k}: {'満たす' if v['ok'] else '満たさない'}" for k, v in V["none"].items()]
    L += ["", f"## 8. 要素の対の一覧（KAORU が最終ラベルを付ける。{len(res['labels'])} 対。数値は載せない）", "",
          "| id | 要素 | 必要像の文 | 相手の文の種類 | 相手の文 | 仮ラベル | 最終 | 備考 |", "|---|---|---|---|---|---|---|---|"]
    for x in res["labels"]:
        L.append(f"| {x['id']} | {x['need_key']} | {x['need']} | {x['other_kind']} | {x['other'].replace(chr(10), ' / ')} | "
                 f"{x['provisional']} | {x['final'] or ''} | {x['note']} |")
    L += ["", "（この結果には、生テキスト・根拠・門の申告値は入っていない。PA との対は通過の有無だけ）", ""]
    return "\n".join(L)


def compare(res, other_path):
    """別の実行（版・dtype を替えたもの）と、推定用の背景に対する値のずれと C2 の判定の変化を比べる。"""
    with open(other_path, encoding="utf-8") as f:
        other = json.load(f)
    out = {}
    for s in res["schemes"]:
        if s not in other.get("fingerprint", {}):
            continue
        a, b = np.asarray(res["fingerprint"][s]), np.asarray(other["fingerprint"][s])
        out[s] = {"max_abs_diff": float(np.abs(a - b).max()), "mean_abs_diff": float(np.abs(a - b).mean())}
    return out


def main(argv=None, encode=None, rerankers=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kaoru", default=os.path.join(ROOT, "local", "kaoru_v5_from_audit.json"))
    ap.add_argument("--audit", default=os.path.join(ROOT, "local", "audit_sentences.json"))
    ap.add_argument("--labels", default=os.path.join(ROOT, "local", "65a", "labels_v1.json"))
    ap.add_argument("--dtype", default=None, help="bfloat16（既定）／float32")
    ap.add_argument("--rerankers", default="C6,C6m", help="使うクロスエンコーダ（C6・C6m。空なら使わない）")
    ap.add_argument("--compare", default=None, help="比べる別の実行の element_probe_result.json")
    ap.add_argument("--out", default=os.path.join(ROOT, "local", "65a"))
    args = ap.parse_args(argv)
    emb, _, info = DP.make_embedder(None, args.dtype, encode=encode, strict=encode is None)
    from necessities import _default_embed
    ver = versions()
    if encode is None and str(ver.get("transformers") or "").split(".")[0] != "4":
        raise SystemExit(f"transformers は 4.x に固定してください（いま {ver.get('transformers')}）")
    if rerankers is None:
        names = [x for x in args.rerankers.split(",") if x]
        rerankers = load_rerankers(names) if names else {}
        for n in names:
            ver[f"{n}_model"] = RERANKERS[n][0]
            ver[f"{n}_revision"] = RERANKERS[n][1]
            ver[f"{n}_license"] = RERANKERS[n][2]
    kaoru = load_kaoru(args.kaoru)
    audit = None
    if args.audit and os.path.exists(args.audit):
        with open(args.audit, encoding="utf-8") as f:
            audit = json.load(f)
    res = run(kaoru, audit, load_sets(), emb, rerankers, labels_path=args.labels, emb_raw=_default_embed)
    if args.compare:
        res["compare"] = compare(res, args.compare)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "element_probe_result.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(res, info, ver))
    with open(os.path.join(args.out, "element_probe_result.json"), "w", encoding="utf-8") as f:
        json.dump({"info": info, "versions": ver, "verdicts": verdicts(res),
                   **{k: v for k, v in res.items() if k != "labels"}}, f, ensure_ascii=False, indent=1,
                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
    if not os.path.exists(args.labels) or not res["has_final"]:
        os.makedirs(os.path.dirname(args.labels), exist_ok=True)
        with open(args.labels, "w", encoding="utf-8") as f:
            json.dump({"_note": "final に「応える」「応えない」「判断できない」を書くと、次の実行から最終ラベルで数える",
                       "pairs": res["labels"]}, f, ensure_ascii=False, indent=1)
    print(f"保存しました: {os.path.join(args.out, 'element_probe_result.md')}（{res['seconds']} 秒）")
    return res


if __name__ == "__main__":
    main()
