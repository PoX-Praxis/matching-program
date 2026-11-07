#!/usr/bin/env python3
"""指示書66（PR-66B）— 要素単位の接続の計測・第2段（判定は変えない）。Code のセッションで実行する。

事前登録 scripts/probe_data/registration_v2.md（確定 2026-10-24）の §4〜§7 のうち、埋め込みとクロスエンコーダの案を測る。
L（含意の判定）の案は PR-66C で足す（この版では測らない）。**本番に触れない**（DB・台帳・トークンを使わない）。
**判定・表示・台帳・正準形・閾値・門は変えない**（計測だけ）。

- 決定の単位は「人 × 要素」（1 人 5 文。どれか 1 文が通れば通過）。決定は役割の 4 要素をまとめて行う
- 背景は固定の文集 bg-v2（scripts/probe_data/background_v2.json）だけ。正解集合は el-v2（element_sets_v2.json）
- 埋め込みは本番と同じ経路（discrimination_probe.make_embedder: 文 → PII のマスク → 役割ごとの接頭辞 → L2 正規化）
- KAORU・PA の文は手元のファイル（local/）から読む。**リポジトリに入れない**。結果も local/66/ に置く

入力（既定は local/）:
  --kaoru   local/kaoru_v5_public.json      KAORU の v5 の JSON（目的・必要像・与え像）
  --audit   local/audit_sentences.json      監査の応答（PA の現状と PA の必要像）
  --labels  local/65a/labels_v1.json        65-A の 48 対の最終ラベル（文単位で別に報告する）
  --spot    local/66/spot_check_v1.json     KAORU の抜き取りの結果（hard negative の「判断できない」を数えない）
出力: local/66/b/element_probe_v2_result.md・.json・passing_background.md（生テキスト・根拠・門の申告値は書かない。
      PA との値は通過の有無だけ）
"""
import argparse
import json
import math
import os
import random
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import discrimination_probe as DP  # noqa: E402
import element_probe as EP  # noqa: E402

DATA = DP.DATA
REG = "registration_v2（scripts/probe_data/registration_v2.md・確定 2026-10-24）"
SEED_NEG, SEED_POS, SEED_DET, SEED_SPOT = 6601, 6602, 6603, 6604
M_PER = 5                                   # 1 人の文の数
PI = 0.03                                   # 想定の割合（いまは実データのない仮定）
SE_MIN = 0.70
PREC = 0.90
DRAWS = 200
POOL_SIZES = (10, 100)
LIST_N = (10, 30, 100, 300)
R_TH = 0.70                                 # 上層の作動点（match_config.UPPER_OPERATING_POINT と同じ値。ここでは変えない）
LOWER_TH = 0.70                             # 下層の現行の作動点（match_config.LOWER_OPERATING_POINT）
SOFT_STEP = 0.01                            # b-2 の軟らかい版: 同時に通る要素が 1 つ増えるごとに上げる幅
NOMIC, NOMIC_CODE = EP.NOMIC, EP.NOMIC_CODE
CE_NAME = "C6m"
CE = EP.RERANKERS[CE_NAME]                  # BAAI/bge-reranker-v2-m3（Apache-2.0）revision 953dc6f6…
CE_KEY = "C6m_logit"                        # 保存する値は logit（sentence-transformers の既定のシグモイドを通さない）


def fp_max(pi, se=SE_MIN, prec=PREC):
    """規則の偽陽性の上限: π·Se·(1−precision) / (precision·(1−π))。π＝3% で 0.2405%。"""
    return pi * se * (1 - prec) / (prec * (1 - pi))


FP_MAX = fp_max(PI)


def _grid(lo, hi, step, nd):
    n = int(round((hi - lo) / step))
    return [round(lo + i * step, nd) for i in range(n + 1)]


GRIDS = {"g": _grid(-0.050, 0.150, 0.005, 3),          # g の差（C2・b-1・b-2・b-3）
         "R": _grid(0.600, 0.900, 0.005, 3),           # g そのもの（0.70 を含む）
         "ce": _grid(-2.0, 8.0, 0.1, 1)}               # クロスエンコーダの logit の差
SCHEMES = ("R", "C2", "C2+b1", "C2+b2h", "C2+b2s", "C2+b3", "C2+b123", "C6m", "C6m+b1")
SCHEME_GRID = {"R": "R", "C6m": "ce", "C6m+b1": "ce"}
SCHEME_NAMES = {"R": "R（g ≥ δ）", "C2": "C2（g − q99_n > δ）", "C2+b1": "C2+b-1（min(g − q99_n, g − q99_s) > δ）",
                "C2+b2h": "C2+b-2 硬い（最も近い 1 要素にだけ）", "C2+b2s": "C2+b-2 軟らかい（同時に通る数で上げる）",
                "C2+b3": "C2+b-3（自分の他の役割の要素より近い）", "C2+b123": "C2+b-1+b-2+b-3",
                "C6m": "C6m（bge-reranker-v2-m3 の logit − q99_n^CE > δ）", "C6m+b1": "C6m+b-1（相手の側は g で 0 に固定）"}


def grid_of(s):
    return GRIDS[SCHEME_GRID.get(s, "g")]


# ── 統計 ─────────────────────────────────────────────────────────────────────────
def _binom_cdf(x, n, p):
    """P(X ≤ x)、X ~ Bin(n, p)（項を順に掛けて足す）。"""
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if x >= n else 0.0
    t = (1 - p) ** n if n < 700 else math.exp(n * math.log1p(-p))
    if t == 0.0:                                         # 下に溢れるときは対数で
        lt = n * math.log1p(-p)
        acc = 0.0
        for k in range(0, x + 1):
            acc += math.exp(lt)
            lt += math.log((n - k) / (k + 1)) + math.log(p / (1 - p))
        return min(acc, 1.0)
    acc = 0.0
    for k in range(0, x + 1):
        acc += t
        t *= (n - k) / (k + 1) * p / (1 - p)
    return min(acc, 1.0)


def _solve(f, target, increasing):
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if (f(mid) < target) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def cp_upper(x, n, conf=0.95):
    """片側の Clopper–Pearson 上限（scipy があれば beta 分布、無ければ二項分布の累積を二分法で解く）。"""
    if n == 0:
        return None
    if x >= n:
        return 1.0
    try:
        from scipy.stats import beta
        return float(beta.ppf(conf, x + 1, n - x))
    except ImportError:
        return _solve(lambda p: _binom_cdf(x, n, p), 1 - conf, increasing=False)


def cp_interval(x, n, conf=0.95):
    """両側の Clopper–Pearson 区間。"""
    if n == 0:
        return (None, None)
    a = (1 - conf) / 2
    try:
        from scipy.stats import beta
        lo = 0.0 if x == 0 else float(beta.ppf(a, x, n - x + 1))
        hi = 1.0 if x == n else float(beta.ppf(1 - a, x + 1, n - x))
    except ImportError:
        lo = 0.0 if x == 0 else _solve(lambda p: 1 - _binom_cdf(x - 1, n, p), a, increasing=True)
        hi = 1.0 if x == n else _solve(lambda p: _binom_cdf(x, n, p), a, increasing=False)
    return (lo, hi)


def exp_precision(pi, se, fp):
    if se is None or fp is None:
        return None
    den = pi * se + (1 - pi) * fp
    return None if den == 0 else pi * se / den


def q99(v, axis=None):
    return np.quantile(np.asarray(v, dtype=float), 0.99, axis=axis)


def draw(r, n, k):
    """n 人から k 人を選ぶ（重ねずに。k が n を超えるとき（テストの小さな文集）だけ重ねて選ぶ）。"""
    return r.sample(range(n), k) if k <= n else [r.randrange(n) for _ in range(k)]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── 途中結果の保存（再起動しても続きから）───────────────────────────────────────────────
class VecCache:
    """埋め込みは役割ごとに .npy ＋ 文の一覧（.json）、クロスエンコーダの値は .json に保存する。"""

    def __init__(self, folder):
        self.folder = folder
        self.idx, self.vecs, self.dirty = {}, {}, 0
        self.ce = {}
        if folder:
            os.makedirs(folder, exist_ok=True)
            for role in ("query", "passage", "symmetric"):
                tp, vp = self._paths(role)
                if os.path.exists(tp) and os.path.exists(vp):
                    with open(tp, encoding="utf-8") as f:
                        texts = json.load(f)
                    arr = np.load(vp)
                    self.idx[role] = {t: i for i, t in enumerate(texts)}
                    self.vecs[role] = [arr[i] for i in range(len(texts))]
            cp = os.path.join(folder, "ce.json")
            if os.path.exists(cp):
                with open(cp, encoding="utf-8") as f:
                    self.ce = json.load(f)

    def _paths(self, role):
        return os.path.join(self.folder, f"emb_{role}.json"), os.path.join(self.folder, f"emb_{role}.npy")

    def size(self):
        return sum(len(v) for v in self.vecs.values()), sum(len(v) for v in self.ce.values())

    def save(self, force=False):
        if not self.folder or not (force or self.dirty >= 500):
            return
        for role, d in self.idx.items():
            texts = sorted(d, key=d.get)
            tp, vp = self._paths(role)
            np.save(vp + ".tmp.npy", np.asarray(self.vecs[role], dtype=np.float32))
            os.replace(vp + ".tmp.npy", vp)
            with open(tp + ".tmp", "w", encoding="utf-8") as f:
                json.dump(texts, f, ensure_ascii=False)
            os.replace(tp + ".tmp", tp)
        with open(os.path.join(self.folder, "ce.json.tmp"), "w", encoding="utf-8") as f:
            json.dump(self.ce, f, ensure_ascii=False)
        os.replace(os.path.join(self.folder, "ce.json.tmp"), os.path.join(self.folder, "ce.json"))
        self.dirty = 0

    def embedder(self, emb):
        def f(text, role):
            d = self.idx.setdefault(role, {})
            if text not in d:
                d[text] = len(d)
                self.vecs.setdefault(role, []).append(np.asarray(emb(text, role), dtype=np.float32))
                self.dirty += 1
                self.save()
            return self.vecs[role][d[text]]
        return f


def mat(emb, texts, role):
    return np.asarray([emb(t, role) for t in texts], dtype=float)


def gmat(Q, P):
    return (1.0 + Q @ P.T) / 2.0


def ce_matrix(cache, scorer, needs, texts, log_every=2000):
    """必要像 × 相手の文のクロスエンコーダの logit（PII をマスクした文で）。保存済みの対は計算しない。"""
    from pii_redaction import redact_text
    store = cache.ce.setdefault(CE_KEY, {})
    key = "\x1f".join
    todo = sorted({(n, t) for n in needs for t in texts if key((n, t)) not in store})
    done = 0
    for i in range(0, len(todo), 256):                       # 256 対ごとに保存する（再起動しても続きから）
        part = todo[i:i + 256]
        scores = scorer([(redact_text(n), redact_text(t)) for n, t in part])
        store.update(zip((key(x) for x in part), (float(v) for v in scores)))
        cache.dirty += len(part)
        cache.save(force=True)
        done += len(part)
        if done % log_every < 256:
            log(f"クロスエンコーダ {done}/{len(todo)}")
    return np.asarray([[store[key((n, t))] for t in texts] for n in needs], dtype=float)


def load_ce():
    """クロスエンコーダ（bge-reranker-v2-m3）を、logit をそのまま返す形で読み込む（registration_v2 §6 は logit の差で掃引する）。
    sentence-transformers の CrossEncoder は出力が 1 つのモデルに既定でシグモイドを当てるので、恒等関数を指定する。"""
    import torch
    from sentence_transformers import CrossEncoder
    model = CrossEncoder(CE[0], revision=CE[1], max_length=512, activation_fn=torch.nn.Identity())
    return lambda pairs: model.predict(pairs, batch_size=32, show_progress_bar=False)


# ── 入力 ─────────────────────────────────────────────────────────────────────────
def load_sets():
    with open(os.path.join(DATA, "background_v2.json"), encoding="utf-8") as f:
        bg = json.load(f)
    with open(os.path.join(DATA, "element_sets_v2.json"), encoding="utf-8") as f:
        el = json.load(f)
    with open(os.path.join(DATA, "element_sets_v1.json"), encoding="utf-8") as f:
        el1 = json.load(f)
    return bg, el, el1


def make_persons(n_val, n_c):
    """負例の人物（検証用を seed 6601 で並べ替えて 5 文ずつ。端数は使わない）と、正例の人物の背景 4 文（seed 6602）。"""
    idx = list(range(n_val))
    random.Random(SEED_NEG).shuffle(idx)
    neg = [idx[i:i + M_PER] for i in range(0, n_val - n_val % M_PER, M_PER)]
    r = random.Random(SEED_POS)
    pos_bg = [r.sample(range(n_val), M_PER - 1) for _ in range(n_c)]
    return neg, pos_bg, n_val % M_PER


# ── 案ごとの通過 ─────────────────────────────────────────────────────────────────────
class Group:
    """必要像（6）× ある相手の文の群の値。g・C2 の差・b-1・クロスエンコーダの差を持つ。"""

    def __init__(self, g, q99n, q99s, ce=None, q99ce=None):
        self.g = g
        self.d = g - q99n[:, None]
        self.ds = g - q99s[None, :]
        self.b1 = np.minimum(self.d, self.ds)
        self.c = None if ce is None else ce - q99ce[:, None]


def comp_sets(nkeys, role_idx):
    """要素 n の比べる相手（b-2・b-3）: 閲覧者の役割の要素。関心型の要素（参考値）はそこに自分を足す。"""
    return [sorted(set(role_idx) | {i}) for i in range(len(nkeys))]


def passes(s, G, delta, sets):
    """案 s・作動点 δ での通過（必要像 × 文の bool 行列）。"""
    if s == "R":
        return G.g >= delta
    if s == "C2":
        return G.d > delta
    if s == "C2+b1":
        return G.b1 > delta
    if s in ("C6m", "C6m+b1"):
        out = G.c > delta
        return out & (G.ds > 0) if s == "C6m+b1" else out
    out = np.zeros_like(G.d, dtype=bool)
    for n, S in enumerate(sets):
        if s == "C2+b2s":
            # k(s) は n を含む（registration_v2 §6）: k − 1 は n のほかに δ を超える要素の数。作動点を下げる向きには使わない
            others = [j for j in S if j != n]
            k1 = (G.d[others] > delta).sum(axis=0) if others else 0
            out[n] = G.d[n] > delta + SOFT_STEP * k1
            continue
        base = G.b1 if s == "C2+b123" else G.d
        ok = base[n] > delta
        if s in ("C2+b2h", "C2+b123"):
            ok &= np.asarray(S)[np.argmax(base[S], axis=0)] == n      # 同点は番号の若い要素
        if s in ("C2+b3", "C2+b123"):
            others = [j for j in S if j != n]
            if others:
                ok &= G.g[n] - G.g[others].max(axis=0) > 0
        out[n] = ok
    return out


# ── 計測 ─────────────────────────────────────────────────────────────────────────
def eval_persons(Pv, Pcm, neg, pos_bg, nkeys, ctarget, csrc, keys_idx, delta):
    """人 × 要素の偽陽性（負例の人物）と通過（正例の人物）。Pv＝必要像 × 検証用、Pcm＝必要像 × C 群の通過（bool）。"""
    neg_hit = np.stack([Pv[:, p].any(axis=1) for p in neg], axis=1)                 # 要素 × 人
    per = {}
    fp = trials = 0
    for n in keys_idx:
        f = int(neg_hit[n].sum())
        per[nkeys[n]] = {"fp": f, "trials": len(neg), "rate": f / len(neg)}
        fp, trials = fp + f, trials + len(neg)
    pos = pos_c = pos_n = 0
    added = added_c = added_n = 0
    for j, k in enumerate(ctarget):
        n = nkeys.index(k)
        if n not in keys_idx:
            continue
        hit_c = bool(Pcm[n, j])
        hit = hit_c or bool(Pv[n, pos_bg[j]].any())
        pos_n, pos, pos_c = pos_n + 1, pos + hit, pos_c + hit_c
        if csrc[j] == "el-v2":
            added_n, added, added_c = added_n + 1, added + hit, added_c + hit_c
    sent_fp = int(Pv[keys_idx].sum())
    sent_c = int(sum(Pcm[nkeys.index(k), j] for j, k in enumerate(ctarget) if nkeys.index(k) in keys_idx))
    return {"delta": delta, "fp": fp, "trials": trials, "fp_rate": fp / trials if trials else None,
            "fp_upper": cp_upper(fp, trials), "pos": pos, "pos_n": pos_n,
            "se": pos / pos_n if pos_n else None, "se_ci": cp_interval(pos, pos_n),
            "pos_by_c": pos_c, "added": added, "added_n": added_n, "added_by_c": added_c,
            "sent_fp": sent_fp, "sent_n": Pv.shape[1] * len(keys_idx), "sent_c": sent_c, "per": per}


def choose(curve, tie=lambda r: (r["delta"],)):
    """報告する作動点: 規則を満たすうち通過率が最も高いもの（同じなら tie が大きいもの）。満たさなければ参考の作動点
    （通過率 70% 以上で偽陽性が最も少ない点。同じなら tie が大きいもの。それも無ければ通過率が最も高い点）。"""
    ok = [r for r in curve if r["fp_upper"] is not None and r["fp_upper"] <= FP_MAX and (r["se"] or 0) >= SE_MIN]
    if ok:
        return max(ok, key=lambda r: (r["se"],) + tuple(tie(r))), True
    meet_se = [r for r in curve if (r["se"] or 0) >= SE_MIN]
    if meet_se:
        return min(meet_se, key=lambda r: (r["fp"],) + tuple(-x for x in tie(r))), False
    return max(curve, key=lambda r: (r["se"] or 0,) + tuple(tie(r))), False


def prepare(kaoru, sets, emb, scorer=None, cache=None):
    """文集・必要像・埋め込み（と、あればクロスエンコーダ）の値・人物を用意する（PR-66B と PR-66C で共通）。"""
    bg, el, el1 = sets
    off = bg["sections"]["offer"]
    X = {"est": [x["text"] for x in off if x["split"] == "est"], "val": [x["text"] for x in off if x["split"] == "val"],
         "nform": [x["text"] for x in bg["sections"]["need"] if x["split"] == "est"],
         "iest": [x["text"] for x in bg["sections"]["interest"] if x["split"] == "est"],
         "ival": [x["text"] for x in bg["sections"]["interest"] if x["split"] == "val"]}
    needs = DP.need_keys(kaoru)
    nkeys = [k for k, _ in needs]
    C = [c for c in el["C"] if c["target"] in nkeys]
    ntext = [n["text"] for _, n in needs]
    role = [i for i, t in enumerate(ntext) if not EP.is_interest(t)]
    X.update(needs=needs, nkeys=nkeys, ntext=ntext, role=role, inter=[i for i in range(len(nkeys)) if i not in role],
             S=comp_sets(nkeys, role), C=C, ctext=[c["text"] for c in C], ctarget=[c["target"] for c in C],
             csrc=[c["src"] for c in C],
             hn=[(x["text"], "a", x["target"]) for x in el["HN"]["a"]] + [(t, "b", None) for t in el["HN"]["b"]])
    est, val, hn = X["est"], X["val"], X["hn"]
    log(f"埋め込み（与え像の形 {len(est) + len(val)}・必要像の形 {len(X['nform'])}・C {len(C)}・hard negative {len(hn)}）")
    Qn = mat(emb, ntext, "query")
    Pest, Pval = mat(emb, est, "passage"), mat(emb, val, "passage")
    Pc, Ph = mat(emb, X["ctext"], "passage"), mat(emb, [t for t, _, _ in hn], "passage")
    Nf = mat(emb, X["nform"], "query")
    log("埋め込み: 与え像の形・必要像の形・C・hard negative")
    q99n = q99(gmat(Qn, Pest), axis=1)

    def q99s_of(P):
        return q99(gmat(Nf, P), axis=0)

    ce_est = ce_val = ce_c = ce_h = q99ce = None
    if scorer:
        ce_est = ce_matrix(cache, scorer, ntext, est)
        q99ce = q99(ce_est, axis=1)
        ce_val = ce_matrix(cache, scorer, ntext, val)
        ce_c = ce_matrix(cache, scorer, ntext, X["ctext"])
        ce_h = ce_matrix(cache, scorer, ntext, [t for t, _, _ in hn])
        log("クロスエンコーダ: 推定用・検証用・C・hard negative")
    X.update(Qn=Qn, Pest=Pest, Nf=Nf, q99n=q99n, q99s_of=q99s_of, q99ce=q99ce, ce_est=ce_est,
             Gv=Group(gmat(Qn, Pval), q99n, q99s_of(Pval), ce_val, q99ce),
             Gc=Group(gmat(Qn, Pc), q99n, q99s_of(Pc), ce_c, q99ce),
             Gh=Group(gmat(Qn, Ph), q99n, q99s_of(Ph), ce_h, q99ce),
             Ge=Group(gmat(Qn, Pest), q99n, q99s_of(Pest), ce_est, q99ce))
    X["neg"], X["pos_bg"], X["leftover"] = make_persons(len(val), len(C))
    X["cidx"] = {k: [j for j, t in enumerate(X["ctarget"]) if t == k] for k in nkeys}
    return X


def run(kaoru, audit, sets, emb, scorer, labels=None, spot=None, cache=None, emb_raw=None, schemes=SCHEMES):
    t0 = time.time()
    pa = EP.pa_from_audit(audit, emb) if audit else None
    X = prepare(kaoru, sets, emb, scorer, cache)
    el1 = sets[2]
    est, val, nform, iest, ival = X["est"], X["val"], X["nform"], X["iest"], X["ival"]
    needs, nkeys, ntext, role, inter, S = X["needs"], X["nkeys"], X["ntext"], X["role"], X["inter"], X["S"]
    C, ctext, ctarget, csrc, hn = X["C"], X["ctext"], X["ctarget"], X["csrc"], X["hn"]
    Qn, Pest, q99n, q99s_of = X["Qn"], X["Pest"], X["q99n"], X["q99s_of"]
    Gv, Gc, Gh = X["Gv"], X["Gc"], X["Gh"]
    neg, pos_bg, leftover, cidx = X["neg"], X["pos_bg"], X["leftover"], X["cidx"]
    spot_hn = {h["index"]: h["label"] for h in (spot or {}).get("HN", [])}
    schemes = [s for s in schemes if scorer or not s.startswith("C6m")]

    def evaluate(s, delta, keys_idx):
        return eval_persons(passes(s, Gv, delta, S), passes(s, Gc, delta, S), neg, pos_bg, nkeys, ctarget, csrc, keys_idx, delta)

    curves, chosen, interest_ref = {}, {}, {}
    for s in schemes:
        curves[s] = [evaluate(s, d, role) for d in grid_of(s)]
        best, ok = choose(curves[s])
        chosen[s] = {"delta": best["delta"], "ok": ok, "row": best}
        if inter:
            interest_ref[s] = evaluate(s, best["delta"], inter)
        log(f"曲線: {s}")

    # 報告する作動点（または参考の作動点）での詳細
    detail = {}
    val_hits = {}
    for s in schemes:
        d = chosen[s]["delta"]
        Pv, Ph_ = passes(s, Gv, d, S), passes(s, Gh, d, S)
        r = chosen[s]["row"]
        prec = {f"{int(p * 100)}%": {"point": exp_precision(p, r["se"], r["fp_rate"]),
                                     "upper": exp_precision(p, r["se"], r["fp_upper"])} for p in (0.01, 0.03, 0.10)}
        other_rule = {f"{int(p * 100)}%": {"fp_max": fp_max(p), "ok": r["fp_upper"] <= fp_max(p) and (r["se"] or 0) >= SE_MIN}
                      for p in (0.01, 0.10)}
        flagged = [k for k, v in r["per"].items() if v["rate"] > FP_MAX]
        for n in role:
            for j in np.nonzero(Pv[n])[0]:
                val_hits.setdefault(int(j), {}).setdefault(s, []).append(nkeys[n])
        # hard negative（決定の分母に入れない。「判断できない」は数えない）
        hrow = {"a_own": 0, "a_any_role": 0, "a_n": 0, "b_any_role": 0, "b_n": 0, "undecided": 0}
        for j, (t, kind, tgt) in enumerate(hn):
            if spot_hn.get(j) == "判断できない":
                hrow["undecided"] += 1
                continue
            any_role = bool(Ph_[role, j].any())
            if kind == "a" and tgt in nkeys:
                hrow["a_n"] += 1
                hrow["a_own"] += int(bool(Ph_[nkeys.index(tgt), j]))
                hrow["a_any_role"] += int(any_role)
            elif kind == "b":
                hrow["b_n"] += 1
                hrow["b_any_role"] += int(any_role)
        detail[s] = {"prec": prec, "other_rule": other_rule, "flagged": flagged, "hn": hrow}

    # 通過した背景の文の一覧（与え像の形の検証用。KAORU が「実際は応えている」とした文を除いた参考値に使う）
    passing = [{"index": j, "text": val[j], "by": hits} for j, hits in sorted(val_hits.items())]

    # 65-A のラベルつきの対（48 対。文単位で別に報告。PA の全文は 1 つの文）
    labeled = None
    if labels:
        pairs = [p for p in labels.get("pairs", [])]
        need_txt = sorted({p["need"] for p in pairs})
        oth_txt = sorted({p["other"] for p in pairs})
        Qx, Px = mat(emb, need_txt, "query"), mat(emb, oth_txt, "passage")
        gx = gmat(Qx, Px)
        q99x = q99(gmat(Qx, Pest), axis=1)
        cex = q99cex = None
        if scorer:
            cex = ce_matrix(cache, scorer, need_txt, oth_txt)
            q99cex = q99(ce_matrix(cache, scorer, need_txt, est), axis=1)
        Gx = Group(gx, q99x, q99s_of(Px), cex, q99cex)
        Sx = [[i] for i in range(len(need_txt))]          # 文単位（b-2・b-3 は閲覧者の要素の組が無いので、その対だけで判定）
        kaoru_need = set(ntext)
        labeled = {}
        for s in schemes:
            Px_ = passes(s, Gx, chosen[s]["delta"], Sx)
            rows = []
            for p in pairs:
                lab = p.get("final") or p.get("provisional")
                i, j = need_txt.index(p["need"]), oth_txt.index(p["other"])
                hit = bool(Px_[i, j])
                if s in ("C2+b2h", "C2+b2s", "C2+b3", "C2+b123") and p["need"] in kaoru_need:
                    # KAORU の必要像との対は、閲覧者（KAORU）の役割の要素の組で b-2・b-3 を当てる
                    G1 = Group(gmat(Qn, mat(emb, [p["other"]], "passage")), q99n, q99s_of(mat(emb, [p["other"]], "passage")))
                    hit = bool(passes(s, G1, chosen[s]["delta"], S)[ntext.index(p["need"]), 0])
                rows.append({"id": p["id"], "kind": p["other_kind"], "need_key": p["need_key"], "label": lab, "pass": hit})
            cnt = {"応える": [0, 0], "応えない": [0, 0], "判断できない": [0, 0]}
            for r in rows:
                c = cnt.setdefault(r["label"], [0, 0])
                c[1] += 1
                c[0] += int(r["pass"])
            labeled[s] = {"counts": cnt, "rows": rows}

    # 較正曲線（スコアの帯ごとの応える・応えない）
    bands = {"g": [-1, -0.05, 0.0, 0.05, 0.10, 0.15, 9], "R": [0, 0.60, 0.65, 0.70, 0.75, 0.80, 9],
             "ce": [-99, -2.0, 0.0, 2.0, 4.0, 6.0, 99]}
    score_of = {"R": lambda G: G.g, "C2": lambda G: G.d, "C2+b1": lambda G: G.b1, "C6m": lambda G: G.c}
    calib = {}
    for s, f in score_of.items():
        if s not in schemes:
            continue
        edges = bands[SCHEME_GRID.get(s, "g")]
        rows = []
        for lo, hi in zip(edges, edges[1:]):
            resp = sum(1 for j, k in enumerate(ctarget) if nkeys.index(k) in role and lo <= f(Gc)[nkeys.index(k), j] < hi)
            negs = int(((f(Gv)[role] >= lo) & (f(Gv)[role] < hi)).sum())
            rows.append({"band": [lo, hi], "resp": resp, "neg": negs})
        calib[s] = rows

    # 要素の層の「該当なし」(i)(ii)(iii)（参考値）・一覧の長さ（N の関数）
    def elem_none(s):
        d = chosen[s]["delta"]
        Pv, Pcm = passes(s, Gv, d, S), passes(s, Gc, d, S)
        neg_hit = np.stack([Pv[:, p].any(axis=1) for p in neg], axis=1)
        out = {}
        for N in POOL_SIZES:
            r = random.Random(SEED_NEG + N)
            i_none = i_tot = ii_found = ii_self = ii_tot = 0
            for n in role:
                for dd in range(DRAWS):
                    pool = draw(r, len(neg), N)
                    i_tot += 1
                    i_none += int(not neg_hit[n, pool].any())
                    js = cidx[nkeys[n]]
                    if js:
                        j = js[dd % len(js)]
                        self_hit = bool(Pcm[n, j] or Pv[n, pos_bg[j]].any())
                        ii_tot += 1
                        ii_self += int(self_hit)
                        ii_found += int(self_hit or neg_hit[n, pool[:-1]].any())
            out[N] = {"i": i_none / i_tot, "ii": ii_found / ii_tot if ii_tot else None, "ii_self": ii_self / ii_tot if ii_tot else None}
        lists = {}
        for N in LIST_N:
            r = random.Random(SEED_NEG + 3 * N)
            shown = []
            for n in role:
                c = 0
                for _ in range(DRAWS // 4):
                    c += int(neg_hit[n, [r.randrange(len(neg)) for _ in range(N)]].sum())
                shown.append(c / (DRAWS // 4))
            lists[N] = {"per_need": {nkeys[n]: round(x, 2) for n, x in zip(role, shown)}, "mean": round(float(np.mean(shown)), 2)}
        return {"none": out, "lists": lists}

    elem_ref = {s: elem_none(s) for s in schemes}
    log("要素の層の参考値")

    # 上層（§5-4・§7）: R 0.70（現行）と C2（δ＝0。bg-v2 の推定用の 99% 点）
    purposes = [[i for i, k in enumerate(nkeys) if k.startswith(f"{pi}-")] for pi in range(1, len(kaoru["purposes"]) + 1)]
    actual_musts = [[i for i in keys if needs[i][1]["must"]] for keys in purposes]
    topic_persons = {k: [j for j, (t, kind, tgt) in enumerate(hn) if kind == "a" and tgt == k] for k in nkeys}
    upper_sets = {"R 0.70": ("R", R_TH), "C2 δ=0": ("C2", 0.0)}
    upper, upper_none = {}, {}
    for uname, (s, d) in upper_sets.items():
        Pv, Pcm, Ph_ = passes(s, Gv, d, S), passes(s, Gc, d, S), passes(s, Gh, d, S)

        def person_hits(person):
            h = np.zeros(len(nkeys), dtype=bool)
            for g, j in person:
                h |= (Pv if g == "v" else Pcm if g == "c" else Ph_)[:, j]
            return h

        def purpose_ok(h, keys, musts):
            return bool(all(h[m] for m in musts) and h[keys].any()) if musts else bool(h[keys].any())

        neg_p = [[("v", j) for j in p] for p in neg]
        neg_h = [person_hits(p) for p in neg_p]
        top_h = {k: person_hits([("h", j) for j in js]) for k, js in topic_persons.items() if js}
        rows = []
        for pi, keys in enumerate(purposes, 1):
            variants = [(m, keys[:m], f"必須 {m} 個（先頭から）") for m in range(0, min(2, len(keys)) + 1)]
            variants.append(("actual", actual_musts[pi - 1], "本番の登録の必須"))
            for tag, musts, label in variants:
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
                got = [purpose_ok(person_hits(p), keys, musts) for p in fits]
                rows.append({"purpose": pi, "variant": tag, "label": label, "musts": [nkeys[m] for m in musts],
                             "pos": sum(truth), "fn": sum(1 for t, g in zip(truth, got) if t and not g),
                             "fit_neg": len(fits) - sum(truth), "fit_fp": sum(1 for t, g in zip(truth, got) if not t and g),
                             "unrel_pass": sum(purpose_ok(h, keys, musts) for h in neg_h), "unrel": len(neg_h),
                             "topic_pass": sum(purpose_ok(h, keys, musts) for k, h in top_h.items()), "topic": len(top_h)})
        upper[uname] = rows
        # 「該当なし」（上層についてのみ。v1 と同じ水準）
        nn = []
        for pi, keys in enumerate(purposes, 1):
            musts = actual_musts[pi - 1]
            fitk = musts or keys[:1]
            res_n = {}
            for N in POOL_SIZES:
                r = random.Random(SEED_NEG + 7 * N + pi)
                i_none = ii_found = ii_self = 0
                for dd in range(DRAWS):
                    pool = draw(r, len(neg_h), N)
                    any_neg = any(purpose_ok(neg_h[x], keys, musts) for x in pool)
                    any_neg_but_last = any(purpose_ok(neg_h[x], keys, musts) for x in pool[:-1])
                    i_none += int(not any_neg)
                    fit = []
                    for m in fitk:
                        js = cidx[nkeys[m]]
                        j = js[dd % len(js)]
                        fit += [("c", j)] + ([("v", b) for b in pos_bg[j]] if m == fitk[0] else [])
                    self_hit = purpose_ok(person_hits(fit), keys, musts)
                    ii_self += int(self_hit)
                    ii_found += int(self_hit or any_neg_but_last)
                res_n[N] = {"i": i_none / DRAWS, "ii": ii_found / DRAWS, "ii_self": ii_self / DRAWS}
            nn.append({"purpose": pi, "musts": [nkeys[m] for m in musts], "by_pool": res_n,
                       "ok": res_n[100]["i"] >= 0.95 and res_n[100]["ii"] >= 0.70 and res_n[10]["i"] >= 0.95})
        upper_none[uname] = nn
    log("上層")

    # §7-3: 上層を C2（δ＝0）にしたとき、関わっている組（KAORU × PA）の根拠（下層・現行の作動点 0.70）が出るか
    engaged = None
    if pa:
        Ppa = mat(emb, [pa["whole"]], "passage")
        Gpa = Group(gmat(Qn, Ppa), q99n, q99s_of(Ppa))
        up = passes("C2", Gpa, 0.0, S)[:, 0]
        low = (Gpa.g >= LOWER_TH)[:, 0]
        p2 = purposes[1] if len(purposes) > 1 else []
        must21 = [i for i in p2 if nkeys[i] == "2-1"]
        engaged = {"purpose2_upper_c2_must_2_1": bool(all(up[m] for m in must21) and up[p2].any()) if p2 else None,
                   "lower_pairs": {nkeys[i]: bool(low[i]) for i in p2}}

    # 関心の欄（参考値）: (i) 対称・(ii) query × document。関心の文の節の 99% 点
    E = el1["E"]
    viewers = [(a, b, True) for a, b in E["match"]] + [(b, a, True) for a, b in E["match"]] + \
              [(a, b, False) for a, b in E["nonmatch"]] + [(b, a, False) for a, b in E["nonmatch"]]
    vt = sorted({v for v, _, _ in viewers})
    interest = {}
    for mode, (rq, rp) in (("i", ("symmetric", "symmetric")), ("ii", ("query", "passage"))):
        V = mat(emb, vt, rq)
        Pi_est, Pi_val = mat(emb, iest, rp), mat(emb, ival, rp)
        th = q99(gmat(V, Pi_est), axis=1)
        gv = gmat(V, Pi_val)
        rec = fpn = 0
        for v, o, m in viewers:
            x = gmat(V[[vt.index(v)]], mat(emb, [o], rp))[0, 0] > th[vt.index(v)]
            rec, fpn = rec + int(m and x), fpn + int((not m) and x)
        fpb = int((gv > th[:, None]).sum())
        n_m = sum(1 for *_, m in viewers if m)
        n_nm = len(viewers) - n_m
        interest[mode] = {"recall": rec, "resp": n_m, "fp_nonmatch": fpn, "nonmatch": n_nm, "fp_bg": fpb, "bg": gv.size,
                          "fp_rate": (fpn + fpb) / (n_nm + gv.size), "recall_rate": rec / n_m if n_m else None}
    ineed = {}
    for i in inter:
        fn = E["for_need"].get(nkeys[i]) or []
        Pi_est, Pi_val = mat(emb, iest, "passage"), mat(emb, ival, "passage")
        th = q99(gmat(Qn[[i]], Pi_est), axis=1)[0]
        ineed[nkeys[i]] = {"recall": int((gmat(Qn[[i]], mat(emb, fn, "passage"))[0] > th).sum()) if fn else 0,
                           "resp": len(fn), "fp_bg": int((gmat(Qn[[i]], Pi_val)[0] > th).sum()), "bg": len(ival)}
    log("関心の欄")

    # 母集団非依存性（無関係な人を 0／10／100 人足しても判定が変わらないか。対照は母集団の 99% 点）
    fixed = [(n, "c", j) for n in role for j in cidx[nkeys[n]]] + [(n, "v", j) for n in role for j in range(10)]
    indep = {}
    for s in [x for x in ("R", "C2", "C2+b1", "C6m") if x in schemes] + ["母集団の99%点（対照）"]:
        ctl = s not in schemes
        base, d = ("C2", 0.0) if ctl else (s, 0.0 if s != "R" else R_TH)
        r = random.Random(SEED_NEG + 11)
        pools = {N: [neg[r.randrange(len(neg))] for _ in range(N)] for N in (0, 10, 100)}
        res_p = {}
        for N, pool in pools.items():
            Pv, Pcm = passes(base, Gv, d, S), passes(base, Gc, d, S)
            outs = []
            for n, g, j in fixed:
                if not ctl:
                    outs.append(bool((Pcm if g == "c" else Pv)[n, j]))
                    continue
                vals = [Gv.g[n, x] for p in pool for x in p]
                v = (Gc if g == "c" else Gv).g[n, j]
                outs.append(bool(vals) and bool(v > q99(vals)))
            res_p[N] = outs
        flips = sum(1 for a, b, c in zip(res_p[0], res_p[10], res_p[100]) if not (a == b == c))
        indep[s] = {"pairs": len(fixed), "flips": flips}

    # 決定性（同じ入力を 2 回）
    det = {}
    if emb_raw:
        sample = ntext + est[:12] + val[:12]
        a = np.asarray([emb(t, "passage") for t in sample], dtype=float)
        b = np.asarray([emb_raw(t, "passage") for t in sample], dtype=float)
        det["embedding"] = {"texts": len(sample), "max_abs_diff": float(np.abs(a - b).max())}
    if scorer:
        from pii_redaction import redact_text
        rr = random.Random(SEED_DET)
        pick = [(ntext[rr.randrange(len(ntext))], val[rr.randrange(len(val))]) for _ in range(32)]
        a = np.asarray(scorer([(redact_text(n), redact_text(t)) for n, t in pick]), dtype=float)
        b = np.asarray([cache.ce[CE_KEY]["\x1f".join(p)] for p in pick], dtype=float) if cache else a
        det["cross_encoder"] = {"pairs": len(pick), "max_abs_diff": float(np.abs(a - b).max())}

    return {"needs": [{"key": k, "must": n["must"], "interest": EP.is_interest(n["text"])} for k, n in needs],
            "role_keys": [nkeys[i] for i in role], "interest_keys": [nkeys[i] for i in inter],
            "schemes": list(schemes), "grids": {s: [grid_of(s)[0], grid_of(s)[-1], len(grid_of(s))] for s in schemes},
            "q99n": {k: round(float(v), 4) for k, v in zip(nkeys, q99n)},
            "sizes": {"est": len(est), "val": len(val), "need_form": len(nform), "C": len(C), "HN": len(hn),
                      "neg_persons": len(neg), "leftover": leftover, "interest_est": len(iest), "interest_val": len(ival)},
            "curves": curves, "chosen": chosen, "interest_ref": interest_ref, "detail": detail, "passing": passing,
            "labeled": labeled, "calib": calib, "elem_ref": elem_ref, "upper": upper, "upper_none": upper_none,
            "engaged": engaged, "interest": interest, "ineed": ineed, "indep": indep, "det": det,
            "pa_repro": pa["repro"] if pa else None, "seconds": round(time.time() - t0, 1)}


# ── 規則に照らす ────────────────────────────────────────────────────────────────────
def verdicts(res):
    out = {"lower": {}, "upper": {}, "none_upper": {}}
    for s in res["schemes"]:
        c = res["chosen"][s]
        out["lower"][s] = {"ok": c["ok"], "delta": c["delta"], "fp": c["row"]["fp"], "trials": c["row"]["trials"],
                           "se": c["row"]["se"]}
    for u, rows in res["upper"].items():
        act = [r for r in rows if r["variant"] == "actual"]
        out["upper"][u] = {"trigger": any(r["unrel_pass"] or r["topic_pass"] for r in act),
                           "rows": [(r["purpose"], r["musts"], r["unrel_pass"], r["topic_pass"]) for r in act]}
    for u, rows in res["upper_none"].items():
        out["none_upper"][u] = [(r["purpose"], r["ok"]) for r in rows]
    return out


# ── 出力 ─────────────────────────────────────────────────────────────────────────
def pct(x, nd=3):
    return "—" if x is None else f"{x * 100:.{nd}f}%"


def to_markdown(res, info, ver):
    V = verdicts(res)
    sz = res["sizes"]
    L = ["# 指示書66（PR-66B）要素単位の接続の計測・第2段 — 結果", "",
         f"規則: {REG}。L（含意の判定）の案は PR-66C で測る（この結果には入っていない）。", "",
         "## 条件", "",
         f"- 埋め込み: `{info['model_tag']}`（{info['mode']}）。revision `{ver.get('nomic_revision')}`"
         f"（固定値 `{NOMIC[1]}`）・remote code `{ver.get('nomic_code_revision')}`",
         f"- クロスエンコーダ: `{CE[0]}` revision `{CE[1]}`（{CE[2]}）" if "C6m" in res["schemes"] else "- クロスエンコーダ: 使わない",
         f"- transformers {ver.get('transformers')}・sentence-transformers {ver.get('sentence_transformers')}・torch {ver.get('torch')}",
         f"- 接頭辞: query=`{info['prefix'].get('query')}` / passage=`{info['prefix'].get('passage')}` / "
         f"symmetric=`{info['prefix'].get('symmetric')}`。L2 正規化。g = (1 + cos) / 2",
         f"- 背景: bg-v2（与え像の形 推定用 {sz['est']}・検証用 {sz['val']}／必要像の形 {sz['need_form']}／関心の文 推定用 "
         f"{sz['interest_est']}・検証用 {sz['interest_val']}）。正解集合: el-v2（C {sz['C']}・hard negative {sz['HN']}）",
         f"- 負例の人物: 検証用を seed {SEED_NEG} で並べ替えて 5 文ずつ → **{sz['neg_persons']} 人**（端数 {sz['leftover']} 文は使わない）。"
         f"正例の人物: C 群の文 1 つ ＋ 検証用 4 文（seed {SEED_POS}）",
         f"- 決定に使う要素: 役割の {len(res['role_keys'])} 要素（{'・'.join(res['role_keys'])}）。"
         f"関心型（{'・'.join(res['interest_keys'])}）は参考値",
         f"- 規則: 人 × 要素の偽陽性の片側 95% 上限（Clopper–Pearson）≤ {FP_MAX * 100:.4f}%（π＝{int(PI * 100)}%）かつ "
         f"正例の人物の通過率（点推定）≥ {int(SE_MIN * 100)}%",
         f"- 計算時間: {res['seconds']} 秒", ""]
    L += ["## 1. 規則に照らした判定（要素の層・役割の 4 要素・人単位）", "",
          "「報告する作動点」＝規則を満たす作動点のうち通過率が最も高いもの（同じなら δ が大きいもの）。"
          "満たす作動点が無い案は「参考の作動点」＝通過率 70% を満たす中で偽陽性が最も少ない点（それも無ければ通過率が最も高い点）。", "",
          "| 案 | 判定 | 作動点 δ | 偽陽性（人×要素） | 率 | 片側 95% 上限 | 通過（正例の人物） | 率 | 両側 95% 区間 | C の文そのもので通過 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for s in res["schemes"]:
        c = res["chosen"][s]
        r = c["row"]
        lo, hi = r["se_ci"]
        L.append(f"| {SCHEME_NAMES[s]} | {'**満たす**' if c['ok'] else '満たさない（参考の作動点）'} | {c['delta']} | "
                 f"{r['fp']}/{r['trials']} | {pct(r['fp_rate'])} | {pct(r['fp_upper'])} | {r['pos']}/{r['pos_n']} | "
                 f"{pct(r['se'], 1)} | {pct(lo, 1)}〜{pct(hi, 1)} | {r['pos_by_c']}/{r['pos_n']} |")
    L += ["", "### 1-1. 足した C 群（66 で足した 24 文。役割の要素は 16 人）だけの通過率（決定は全体で行う）", "",
          "| 案 | 作動点 δ | 足した文の人物の通過 | C の文そのもので通過 |", "|---|---|---|---|"]
    for s in res["schemes"]:
        r = res["chosen"][s]["row"]
        L.append(f"| {s} | {r['delta']} | {r['added']}/{r['added_n']} | {r['added_by_c']}/{r['added_n']} |")
    L += ["", "### 1-2. 文単位（併記。決定には使わない）", "",
          "| 案 | 作動点 δ | 検証用の文 × 役割の要素の通過 | C 群の文の通過（target の要素） |", "|---|---|---|---|"]
    for s in res["schemes"]:
        r = res["chosen"][s]["row"]
        L.append(f"| {s} | {r['delta']} | {r['sent_fp']}/{r['sent_n']}（{pct(r['sent_fp'] / r['sent_n'])}） | "
                 f"{r['sent_c']}/{r['pos_n']} |")
    L += ["", "### 1-3. 要素ごとの偽陽性（人 × 要素。点推定）", "",
          "| 案 | 作動点 δ | " + " | ".join(res["role_keys"]) + " | 0.24% を超える要素 |", "|---|---|" + "---|" * len(res["role_keys"]) + "---|"]
    for s in res["schemes"]:
        r = res["chosen"][s]["row"]
        L.append(f"| {s} | {r['delta']} | " + " | ".join(f"{r['per'][k]['fp']}/{r['per'][k]['trials']}" for k in res["role_keys"])
                 + f" | {'・'.join(res['detail'][s]['flagged']) or 'なし'} |")
    L += ["", "### 1-4. 期待 precision と、想定の割合を変えた参考", "",
          "| 案 | π＝1%（点／上限） | π＝3%（点／上限） | π＝10%（点／上限） | π＝1% の規則（上限 ≤ 0.0786%） | π＝10% の規則（上限 ≤ 0.8642%） |",
          "|---|---|---|---|---|---|"]
    for s in res["schemes"]:
        d = res["detail"][s]
        p = d["prec"]
        L.append(f"| {s} | " + " | ".join(f"{pct(p[k]['point'], 1)}／{pct(p[k]['upper'], 1)}" for k in ("1%", "3%", "10%"))
                 + f" | {'満たす' if d['other_rule']['1%']['ok'] else '満たさない'} | {'満たす' if d['other_rule']['10%']['ok'] else '満たさない'} |")
    L += ["", "### 1-5. 関心型の要素（参考値。同じ作動点で）", "",
          "| 案 | 作動点 δ | 偽陽性（人×要素） | 片側 95% 上限 | 通過（正例の人物） |", "|---|---|---|---|---|"]
    for s, r in res["interest_ref"].items():
        L.append(f"| {s} | {r['delta']} | {r['fp']}/{r['trials']} | {pct(r['fp_upper'])} | {r['pos']}/{r['pos_n']} |")
    L += ["", "## 2. 曲線（作動点ごと。役割の 4 要素・人単位）", ""]
    for s in res["schemes"]:
        L += [f"### {SCHEME_NAMES[s]}", "", "| δ | 偽陽性 | 上限 | 通過 | 期待 precision（π＝3%・点） | 規則 |", "|---|---|---|---|---|---|"]
        for r in res["curves"][s]:
            ok = r["fp_upper"] <= FP_MAX and (r["se"] or 0) >= SE_MIN
            L.append(f"| {r['delta']} | {r['fp']}/{r['trials']} | {pct(r['fp_upper'])} | {r['pos']}/{r['pos_n']} | "
                     f"{pct(exp_precision(PI, r['se'], r['fp_rate']), 1)} | {'✓' if ok else ''} |")
        L.append("")
    L += ["## 3. hard negative（決定の分母に入れない。報告する／参考の作動点で）", "",
          "a＝話題だけ近い文（要素ごと）、b＝汎用の役割文。KAORU が「判断できない」とした文は数えない。", "",
          "| 案 | 作動点 δ | a: 自分の要素で通過 | a: 役割のどれかで通過 | b: 役割のどれかで通過 | 数えない |", "|---|---|---|---|---|---|"]
    for s in res["schemes"]:
        h = res["detail"][s]["hn"]
        L.append(f"| {s} | {res['chosen'][s]['delta']} | {h['a_own']}/{h['a_n']} | {h['a_any_role']}/{h['a_n']} | "
                 f"{h['b_any_role']}/{h['b_n']} | {h['undecided']} |")
    if res["labeled"]:
        L += ["", "## 4. 65-A のラベルつきの対（48 対・最終ラベル。文単位）", "",
              "| 案 | 作動点 δ | 応える: 通過 | 応えない: 通過 | 判断できない（数えない） |", "|---|---|---|---|---|"]
        for s, x in res["labeled"].items():
            c = x["counts"]
            L.append(f"| {s} | {res['chosen'][s]['delta']} | {c['応える'][0]}/{c['応える'][1]} | {c['応えない'][0]}/{c['応えない'][1]} | "
                     f"{c.get('判断できない', [0, 0])[1]} |")
        L += ["", "対ごと（通過の有無だけ）:", "", "| id | 種類 | 要素 | ラベル | " + " | ".join(res["schemes"]) + " |",
              "|---|---|---|---|" + "---|" * len(res["schemes"])]
        first = res["labeled"][res["schemes"][0]]["rows"]
        for i, r in enumerate(first):
            L.append(f"| {r['id']} | {r['kind'][:18]} | {r['need_key']} | {r['label']} | "
                     + " | ".join("通る" if res["labeled"][s]["rows"][i]["pass"] else "—" for s in res["schemes"]) + " |")
    L += ["", "## 5. 較正曲線（スコアの帯ごと。役割の要素）", ""]
    for s, rows in res["calib"].items():
        L += [f"- {s}: " + "、".join(f"[{a}, {b}) 応える {r['resp']}・応えない {r['neg']}" for r in rows for a, b in [r["band"]])]
    L += ["", "## 6. 要素の層の「該当なし」(i)(ii)(iii) と一覧の長さ（参考値。決定には使わない）", "",
          "| 案 | (i) 10 人 | (i) 100 人 | (ii) 100 人 | (iii) | 一覧に出る偽りの人の平均（N＝10／30／100／300） |", "|---|---|---|---|---|---|"]
    for s, x in res["elem_ref"].items():
        n = x["none"]
        L.append(f"| {s} | {pct(n[10]['i'], 1)} | {pct(n[100]['i'], 1)} | {pct(n[100]['ii'], 1)} | {pct(n[100]['ii_self'], 1)} | "
                 + "／".join(str(x["lists"][N]["mean"]) for N in LIST_N) + " |")
    L += ["", "## 7. 上層（§5-4・§7）", "",
          f"無関係な人＝負例の人物（{sz['neg_persons']} 人）。話題だけの人＝hard negative (a) の 5 文の人物（要素ごと）。"
          "必須の無い目的は、どれかの要素が通れば成立として数える（v1 と同じ）。", "",
          "| 上層の案 | 目的 | 条件 | 必須 | 当てはまる人の取りこぼし | 当てはまらない組の通過 | 無関係な人の通過 | 話題だけの人の通過 |",
          "|---|---|---|---|---|---|---|---|"]
    for u, rows in res["upper"].items():
        for r in rows:
            L.append(f"| {u} | {r['purpose']} | {r['label']} | {'・'.join(r['musts']) or 'なし'} | {r['fn']}/{r['pos']} | "
                     f"{r['fit_fp']}/{r['fit_neg']} | {r['unrel_pass']}/{r['unrel']} | {r['topic_pass']}/{r['topic']} |")
    L += ["", "見直しの条件（本番の登録の必須で、無関係・話題だけの人が 1 件でも通る）:", ""]
    for u, v in V["upper"].items():
        L.append(f"- {u}: {'**当たる**' if v['trigger'] else '当たらない'}")
    L += ["", "「該当なし」（上層についてのみ。i100 ≥ 95%・ii100 ≥ 70%・i10 ≥ 95%）:", "",
          "| 上層の案 | 目的 | 必須 | (i) 10 人 | (i) 100 人 | (ii) 100 人 | (iii) | 規則 |", "|---|---|---|---|---|---|---|---|"]
    for u, rows in res["upper_none"].items():
        for r in rows:
            b = r["by_pool"]
            L.append(f"| {u} | {r['purpose']} | {'・'.join(r['musts']) or 'なし'} | {pct(b[10]['i'], 1)} | {pct(b[100]['i'], 1)} | "
                     f"{pct(b[100]['ii'], 1)} | {pct(b[100]['ii_self'], 1)} | {'満たす' if r['ok'] else '満たさない'} |")
    if res["engaged"]:
        e = res["engaged"]
        L += ["", "§7-3 上層を C2（δ＝0）にしたとき、関わっている組（KAORU × PA）の根拠が残るか（通過の有無だけ）:", "",
              f"- 目的2（2-1 を必須にした条件）の上層: {'通る' if e['purpose2_upper_c2_must_2_1'] else '通らない'}",
              "- 要素の対（下層・現行の作動点）: " + "・".join(f"{k} {'出る' if v else '出ない'}" for k, v in e["lower_pairs"].items())]
    L += ["", "## 8. 関心の欄（参考値）", "", "| 方式 | 一致の組の通過 | 不一致の組の通過 | 背景（検証用）の通過 | 偽陽性率 |", "|---|---|---|---|---|"]
    for mode, x in res["interest"].items():
        L.append(f"| ({mode}) {'対称' if mode == 'i' else 'query × document'} | {x['recall']}/{x['resp']} | "
                 f"{x['fp_nonmatch']}/{x['nonmatch']} | {x['fp_bg']}/{x['bg']} | {pct(x['fp_rate'])} |")
    for k, x in res["ineed"].items():
        L.append(f"- 関心型の必要像 {k} × 相手の関心: 応える {x['recall']}/{x['resp']}・背景 {x['fp_bg']}/{x['bg']}")
    L += ["", "## 9. 母集団非依存性・決定性", "", "| 案 | 対 | 0／10／100 人で判定が変わった対 |", "|---|---|---|"]
    L += [f"| {s} | {x['pairs']} | {x['flips']} |" for s, x in res["indep"].items()]
    for k, x in res["det"].items():
        L.append(f"- 決定性（{k}）: {x.get('texts', x.get('pairs'))} 件・同じ入力の 2 回の最大差 {x['max_abs_diff']:.2e}")
    if res["pa_repro"]:
        L.append(f"- PA の現状の欄の割り当て: 監査の g を {res['pa_repro']['matched']}/{res['pa_repro']['rows']} 行で再現")
    L += ["", f"通過した背景の文の一覧は passing_background.md（{len(res['passing'])} 文）。", ""]
    return "\n".join(L)


def passing_markdown(res):
    L = ["# 通過した背景の文（与え像の形の検証用）— KAORU の確認用", "",
         "各案の「報告する作動点（満たさない案は参考の作動点）」で、役割の要素に通った文です。"
         "**実際は応えている**と思う文に、その要素の番号を書いてください。除いた値を参考として出します（決定は変えません）。", "",
         "| # | 文 | 通った案と要素 | 実際は応えている要素 |", "|---|---|---|---|"]
    for i, p in enumerate(res["passing"], 1):
        by = "；".join(f"{s}: {'・'.join(ks)}" for s, ks in p["by"].items())
        L.append(f"| P{i:03d} | {p['text']} | {by} | |")
    return "\n".join(L) + "\n"


def main(argv=None, encode=None, scorer=None, sets=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kaoru", default=os.path.join(ROOT, "local", "kaoru_v5_public.json"))
    ap.add_argument("--audit", default=os.path.join(ROOT, "local", "audit_sentences.json"))
    ap.add_argument("--labels", default=os.path.join(ROOT, "local", "65a", "labels_v1.json"))
    ap.add_argument("--spot", default=os.path.join(ROOT, "local", "66", "spot_check_v1.json"))
    ap.add_argument("--no-ce", action="store_true", help="クロスエンコーダの案（C6m）を測らない")
    ap.add_argument("--out", default=os.path.join(ROOT, "local", "66", "b"))
    args = ap.parse_args(argv)
    emb, _, info = DP.make_embedder(None, None, encode=encode, strict=encode is None)
    from necessities import _default_embed
    ver = EP.versions()
    if encode is None and str(ver.get("transformers") or "").split(".")[0] != "4":
        raise SystemExit(f"transformers は 4.x に固定してください（いま {ver.get('transformers')}）")
    if scorer is None and not args.no_ce:
        scorer = load_ce()
        ver.update({"ce_model": CE[0], "ce_revision": CE[1], "ce_license": CE[2], "ce_revision_cached": EP.hf_revision(CE[0]),
                    "ce_output": "logit（恒等関数。シグモイドを通さない）"})

    def read(path):
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None

    kaoru = EP.load_kaoru(args.kaoru)
    os.makedirs(args.out, exist_ok=True)
    cache = VecCache(os.path.join(args.out, "cache"))
    log("開始（保存済み: 埋め込み %d 件・クロスエンコーダ %d 件）" % cache.size())
    res = run(kaoru, read(args.audit), sets or load_sets(), cache.embedder(emb), scorer, labels=read(args.labels),
              spot=read(args.spot), cache=cache, emb_raw=_default_embed)
    cache.save(force=True)
    with open(os.path.join(args.out, "element_probe_v2_result.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(res, info, ver))
    with open(os.path.join(args.out, "passing_background.md"), "w", encoding="utf-8") as f:
        f.write(passing_markdown(res))
    with open(os.path.join(args.out, "element_probe_v2_result.json"), "w", encoding="utf-8") as f:
        json.dump({"info": info, "versions": ver, "registration": REG, "verdicts": verdicts(res), **res}, f,
                  ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(f"保存しました: {os.path.join(args.out, 'element_probe_v2_result.md')}（{res['seconds']} 秒）")
    return res


if __name__ == "__main__":
    main()
