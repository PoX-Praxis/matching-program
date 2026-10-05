"""
PoX v4 照合エンジン（E章 / Step 5。指示書56 段1 で計算を整理）

チャネル（B-2 混同禁止）:
  a : will_symmetric  対 will_symmetric   — 共鳴（意志どうし）
  b : necessity_query 対 相手の state_passage — 補完A（自分の必要像 × 相手の現状）
  d : 相手の necessity_query 対 自分の state_passage — 補完B（相手の必要像 × 自分の現状）

総合（指示書56）:
  score   （A）= M_0([g(a), g(b)], [α, β])     幾何平均（p = 0 固定）
  score_b （B）= M_0([g(a), g(d)], [α, β])
  - 補完は**方向ごとに別々に**判定する（A と B を平均しない）。入口はどちらかが閾値以上。
  - **γ は廃止**（上限 0.5 で総合にほぼ効かず、意味が二重だった）。
  - **c（will_passage どうし）は判定から外した**。Embedding v4 の設計では c は「必要像 × 意志」
    だったが、実装は意志 passage どうしの対称な類似度で、実測でも両方向が同値だった（設計からのずれ）。
    無関係なペアで最も高く出るなど識別力も無い。段3 では「向かう先」の文どうしの比較が役割を引き継ぐ。
  - **p_sharpness は使わない**（必須文が無いので段2 まで p = 0）。
  - 全文 cos は「文体・話題・書き手の近さ」に反応し、意味の一致を測れていない（56 §0）。
    閾値 0.70 は暫定で、判定の再設計は段2〜3（文単位・与え像）で行う。
"""
import math

from embedding_service import cosine, guard
from match_config import ALPHA_DEFAULT, BETA_DEFAULT, SHORTLIST_K, MATCH_ENTRY_THRESHOLD

P_FIXED = 0.0   # 幾何平均（指示書56 §1-4。p_sharpness は段2 まで使わない）


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


# ── 総合（方向 A・B を別々に）────────────────────────────────────────────────
def _num(x, default):
    return default if x is None else x


def score_candidate(seeker_vecs, candidate_vecs, gamma=None, p=None,
                    alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT):
    """総合A = M_0([g(a), g(b)], [α, β])。gamma・p は**廃止**（互換のため受け取るが使わない）。"""
    ga = guard(cosine(seeker_vecs["will_symmetric"], candidate_vecs["will_symmetric"]))
    gb = guard(cosine(seeker_vecs["necessity_query"], candidate_vecs["state_passage"]))
    return power_mean([ga, gb], [_num(alpha, ALPHA_DEFAULT), _num(beta, BETA_DEFAULT)], P_FIXED)


def attribution(seeker_vecs, candidate_vecs, gamma=None, p=None,
                alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT):
    """各チャネルの値と律速軸（a・b のうち加重 log 寄与が小さい方）。内部でのみ使う。"""
    alpha, beta = _num(alpha, ALPHA_DEFAULT), _num(beta, BETA_DEFAULT)
    a_sim = cosine(seeker_vecs["will_symmetric"], candidate_vecs["will_symmetric"])
    b_sim = cosine(seeker_vecs["necessity_query"], candidate_vecs["state_passage"])
    ga, gb = guard(a_sim), guard(b_sim)
    w = alpha + beta
    a_log = (alpha / w) * math.log(ga)
    b_log = (beta / w) * math.log(gb)
    return {
        "a_sim": a_sim, "b_sim": b_sim, "ga": ga, "gb": gb,
        "final": power_mean([ga, gb], [alpha, beta], P_FIXED),
        "a_log_contrib": a_log, "b_log_contrib": b_log,
        "limiting_axis": "a" if a_log < b_log else "b",
    }


def effective_axis(attr):
    """最も効いた軸（指示書55 §1-2。律速軸の逆）。a・b のうち類似度（g）が高い方。名前だけ使う。"""
    return "a" if attr["ga"] >= attr["gb"] else "b"


def add_direction_b(result, seeker_vecs, candidate_vecs, gamma=None, p=None,
                    alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT):
    """補完B（**相手の必要像 × 自分の現状**）と総合B を足す。既存ベクトルだけで計算する。
    総合B = M_0([g(a), g(d)], [α, β])（α・β は照合した側＝自分の値）。ベクトルが無ければ何もしない。"""
    mine, theirs = seeker_vecs.get("state_passage"), candidate_vecs.get("necessity_query")
    if mine is None or theirs is None:
        return result
    attr = result["attribution"]
    d_sim = cosine(theirs, mine)
    gd = guard(d_sim)
    attr["d_sim"], attr["gd"] = d_sim, gd
    result["score_b"] = power_mean([attr["ga"], gd],
                                   [_num(alpha, ALPHA_DEFAULT), _num(beta, BETA_DEFAULT)], P_FIXED)
    return result


def passes_entry(result, threshold=MATCH_ENTRY_THRESHOLD):
    """「照合の結果」に入れるか。**総合A か総合B のどちらかが閾値以上**（平均しない。指示書56 §1-3）。
    「私はあなたが必要だが、あなたは私を必要としていない」という片方向の組も落とさない。
    閾値は暫定（段2〜3 で再設計）。意志の下限などのゲートは置かない（56 §1-5）。"""
    return max(result["score"], result.get("score_b", 0.0)) >= threshold


def public_axis(attr, level=MATCH_ENTRY_THRESHOLD):
    """利用者に見せる軸。数値は返さない。

    - "mutual": 足りないところを互いに埋める — 補完A（gb）と補完B（gd）の**両方**が水準以上のときだけ
    - "fill_mine" / "fill_theirs": 足りないところを埋める（どちら向きか）
    - "will": 意志が近い（a のみ。c は外した）
    """
    gb, gd = attr["gb"], attr.get("gd")
    if gd is not None and gb >= level and gd >= level:
        return "mutual"
    cands = {"will": attr["ga"], "fill_mine": gb}
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


# ── 候補の総当たり ─────────────────────────────────────────────────────────
def rank_candidates(seeker_vecs, candidate_vecs_list, gamma=None, p=None,
                    alpha=ALPHA_DEFAULT, beta=BETA_DEFAULT, top_k=None):
    """全候補について総合A・総合B を計算して返す（gamma・p は廃止。互換のため受け取るが使わない）。

    戻り値: [{"candidate_id", "score"（総合A）, "score_b"（総合B）, "attribution"}, ...]（総合A の降順。
    並びは内部用で、外に出す順は中立＝id 順）。
    """
    results = []
    for cid, cvecs in candidate_vecs_list:
        r = {"candidate_id": cid,
             "score": score_candidate(seeker_vecs, cvecs, alpha=alpha, beta=beta),
             "attribution": attribution(seeker_vecs, cvecs, alpha=alpha, beta=beta)}
        add_direction_b(r, seeker_vecs, cvecs, alpha=alpha, beta=beta)
        results.append(r)
    results.sort(key=lambda x: x["score"], reverse=True)
    if top_k is not None:
        results = results[:top_k]
    return results
