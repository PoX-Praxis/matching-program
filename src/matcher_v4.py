"""
PoX v4 照合エンジン（E章 / Step 5）

E-1: 256-dim shortlist（Step 6 で DB HNSW クエリに置換）
E-2: 全次元 nested complement power mean でスコア算出
E-3: 律速軸・寄与率 attribution

チャネル定義（B-2 混同禁止）:
  a : will_symmetric 対 will_symmetric  — 共鳴（同じ方向への意志）
  b : necessity_query 対 state_passage  — 主補完（必要像 vs 候補の現状）
  c : will_passage   対 will_passage    — 意志補完（γ でゲート）

ネスト公式（Sakana #5）:
  complement = M_p([guard(b), guard(c)], [1.0, γ], p)
  final      = M_p([guard(a), complement], [α, β],  p)
"""
import math

from embedding_service import cosine, guard
from match_config import (
    GAMMA_EPS, P_SHARPNESS_DEFAULT, ALPHA_DEFAULT, BETA_DEFAULT, SHORTLIST_K,
    MATCH_ENTRY_THRESHOLD,
)


# ── E-2: power mean ─────────────────────────────────────────────────────────
def power_mean(values, weights, p):
    """
    加重べき乗平均 M_p。
    p→0: 幾何平均（ソフトAND）、p<0: min 寄り、p>0: max 寄り。
    values は guard() 済みの正値を期待（p<0 でゼロを渡すと壊れる）。
    """
    if len(values) != len(weights):
        raise ValueError(f"values/weights 長さ不一致: {len(values)} vs {len(weights)}")
    W = sum(weights)
    if W <= 0:
        raise ValueError("weights の和が 0 以下")
    w = [wi / W for wi in weights]

    if abs(p) < 1e-9:
        # p≈0: 幾何平均 exp(Σ w_i * log(v_i))
        return math.exp(sum(wi * math.log(v) for wi, v in zip(w, values)))
    return (sum(wi * (v ** p) for wi, v in zip(w, values))) ** (1.0 / p)


# ── E-2: nested complement スコア ────────────────────────────────────────────
def score_candidate(seeker_vecs, candidate_vecs, gamma,
                    p=P_SHARPNESS_DEFAULT, alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT):
    """
    全次元 nested complement power mean で最終スコアを計算（E-2）。

    seeker_vecs    : {"will_symmetric", "will_passage", "necessity_query", ...}
    candidate_vecs : {"will_symmetric", "will_passage", "state_passage", ...}
    gamma          : c チャネルのゲート重み（necessity_gen が算出）
    """
    a_sim = cosine(seeker_vecs["will_symmetric"], candidate_vecs["will_symmetric"])
    b_sim = cosine(seeker_vecs["necessity_query"], candidate_vecs["state_passage"])
    c_sim = cosine(seeker_vecs["will_passage"],    candidate_vecs["will_passage"])

    ga = guard(a_sim)
    gb = guard(b_sim)
    gc = guard(c_sim)

    complement = power_mean([gb, gc], [1.0, gamma], p)
    return power_mean([ga, complement], [alpha, beta], p)


# ── E-3: 律速軸・寄与率 attribution ─────────────────────────────────────────
def attribution(seeker_vecs, candidate_vecs, gamma,
                p=P_SHARPNESS_DEFAULT, alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT):
    """
    各チャネルの寄与と律速軸を返す（E-3）。

    log 寄与は p=0 幾何平均の分解で解釈する（p 非依存の安定した軸判定）。
    律速軸: 加重 log 寄与が最も小さい（最も足を引っ張る）チャネル。
    gamma <= GAMMA_EPS のとき c チャネルを律速候補から外す。
    """
    a_sim = cosine(seeker_vecs["will_symmetric"], candidate_vecs["will_symmetric"])
    b_sim = cosine(seeker_vecs["necessity_query"], candidate_vecs["state_passage"])
    c_sim = cosine(seeker_vecs["will_passage"],    candidate_vecs["will_passage"])

    ga = guard(a_sim)
    gb = guard(b_sim)
    gc = guard(c_sim)

    complement = power_mean([gb, gc], [1.0, gamma], p)
    final = power_mean([ga, complement], [alpha, beta], p)

    # p=0 対数分解: log(final) = a_log + b_log + c_log
    ab_w   = alpha + beta
    comp_w = 1.0 + gamma
    a_log = (alpha / ab_w) * math.log(ga)
    b_log = (beta  / ab_w) * (1.0   / comp_w) * math.log(gb)
    c_log = (beta  / ab_w) * (gamma / comp_w) * math.log(gc) if gamma > GAMMA_EPS else 0.0

    contribs = {"a": a_log, "b": b_log, "c": c_log}
    limiting = min(contribs, key=lambda k: contribs[k])

    return {
        "a_sim": a_sim, "b_sim": b_sim, "c_sim": c_sim,
        "ga": ga, "gb": gb, "gc": gc,
        "complement": complement,
        "final": final,
        "a_log_contrib": a_log,
        "b_log_contrib": b_log,
        "c_log_contrib": c_log,
        "limiting_axis": limiting,
    }


def effective_axis(attr):
    """最も効いた軸（指示書55 §1-2）。**律速軸（最も足を引っ張る軸）の逆**。

    有効なチャネル（a・b、c は γ が効いているときだけ）のうち、類似度（guard 後の値）が
    最も高いものを返す。重み付き log 寄与の最大で選ぶと、重みの小さい c が常に 0 に近く
    「最大」になってしまうため、重みを掛けない類似度で比べる。数値は外に出さない（名前だけ）。
    """
    cands = {"a": attr["ga"], "b": attr["gb"]}
    if attr.get("c_log_contrib", 0.0) != 0.0:
        cands["c"] = attr["gc"]
    return max(cands, key=lambda k: cands[k])


# ── 方向 B（指示書55 §4-3）─────────────────────────────────────────────────
def add_direction_b(result, seeker_vecs, candidate_vecs, gamma,
                    p=P_SHARPNESS_DEFAULT, alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT):
    """方向 B（**相手の必要像 × 自分の現状**）を結果に足す。既存ベクトルだけで計算する。

    方向 A（b: 自分の必要像 × 相手の現状）の b を d に置き換えた同じ式を score_b とする
    （a・c と自分の α・β・γ・p はそのまま）。どちらかのベクトルが無ければ何もしない。
    数値は内部でのみ使う（入口の判定と軸の要約。外には出さない）。
    """
    mine, theirs = seeker_vecs.get("state_passage"), candidate_vecs.get("necessity_query")
    if mine is None or theirs is None:
        return result
    attr = result["attribution"]
    d_sim = cosine(theirs, mine)
    gd = guard(d_sim)
    attr["d_sim"], attr["gd"] = d_sim, gd
    complement_b = power_mean([gd, attr["gc"]], [1.0, gamma], p)
    result["score_b"] = power_mean([attr["ga"], complement_b], [alpha, beta], p)
    return result


def passes_entry(result, threshold=MATCH_ENTRY_THRESHOLD):
    """「照合の結果」に入れるか（指示書55 §4-1）。**総合が内部閾値以上**。

    総合は自分の α・β に沿う（共鳴型なら意志の近さが、補完型なら補完が効く）。b 単独では切らない。
    方向 B（相手が自分を必要としている）も同じ式の総合で評価し、どちらかが閾値以上なら入れる。
    """
    return max(result["score"], result.get("score_b", 0.0)) >= threshold


def public_axis(attr, level=MATCH_ENTRY_THRESHOLD):
    """利用者に見せる軸（指示書55 §0-4。**2 つに要約**＋相互充足）。数値は返さない。

    - "mutual": 補完が双方向とも効いている（自分の必要像×相手の現状、相手の必要像×自分の現状）
    - "fill_mine" / "fill_theirs": 足りないところを埋める（どちら向きか）
    - "will": 意志が近い（a 共鳴と c 意志補完はどちらも意志どうしなので 1 つにまとめる）
    最も効いた軸（effective_axis と同じく重みを掛けない類似度の最大）で選ぶ。律速軸ではない。
    """
    gb, gd = attr["gb"], attr.get("gd")
    if gd is not None and gb >= level and gd >= level:
        return "mutual"
    will = attr["ga"]
    if attr.get("c_log_contrib", 0.0) != 0.0:
        will = max(will, attr["gc"])
    cands = {"will": will, "fill_mine": gb}
    if gd is not None:
        cands["fill_theirs"] = gd
    return max(cands, key=lambda k: cands[k])


# ── E-1: shortlist（256-dim 近傍 / Step 6 で DB HNSW に置換）────────────────
def shortlist(seeker_q256, candidates_256, k=SHORTLIST_K):
    """
    256-dim コサインで近傍 k 件を絞る（E-1 一次絞り込み）。

    seeker_q256    : seeker の necessity_q_256 ベクトル
    candidates_256 : [(candidate_id, vec_256), ...]
    戻り値         : candidate_id リスト（コサイン降順 k 件）
    """
    scored = [(cid, cosine(seeker_q256, v256)) for cid, v256 in candidates_256]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in scored[:k]]


# ── E-1+E-2: shortlist → full-dim re-rank ─────────────────────────────────
def rank_candidates(seeker_vecs, candidate_vecs_list, gamma,
                    p=P_SHARPNESS_DEFAULT, alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT,
                    top_k=None):
    """
    全次元スコアで候補をランキングして返す（E-1+E-2 統合）。

    seeker_vecs         : build_vectors 戻り dict（necessity_query 含む）
    candidate_vecs_list : [(candidate_id, vecs_dict), ...]
    top_k               : 上位 N 件に絞る（None = 全件）
    戻り値              : [{"candidate_id", "score", "attribution"}, ...] 降順
    """
    results = []
    for cid, cvecs in candidate_vecs_list:
        sc   = score_candidate(seeker_vecs, cvecs, gamma, p, alpha, beta)
        attr = attribution(seeker_vecs, cvecs, gamma, p, alpha, beta)
        r = {"candidate_id": cid, "score": sc, "attribution": attr}
        add_direction_b(r, seeker_vecs, cvecs, gamma, p, alpha, beta)
        results.append(r)
    results.sort(key=lambda x: x["score"], reverse=True)
    if top_k is not None:
        results = results[:top_k]
    return results
