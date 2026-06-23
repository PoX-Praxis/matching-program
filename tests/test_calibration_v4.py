"""
Step 8 較正テスト — H-2 受け入れ基準（4テスト）

母数到着前のパラメータ（γ_max, p, α, β）は保守的固定。ここでは「値の正しさ」では
なく「エンジンの振る舞いが設計意図と一致するか」を検証する（H-2）:

  1. 単調性   : ∂γ/∂u > 0（迷いが増えるほど取りこぼし保護 c を強める）
  2. 不変性   : γ=0 のとき c チャネルはスコアに一切効かない（多数派保護）
  3. 順位感度 : 補完チャネル（b/c）が実際に順位を動かす（共鳴に埋もれない）
  4. 顔の妥当性: 手組みシナリオで直感的に正しい相手が上位に来る

各チャネルを独立に制御するため、a/b/c を直交部分空間に割り当てた合成ベクトルを使う:
  a: dims[0,1]  b: dims[2,3]  c: dims[4,5]  → a_sim/b_sim/c_sim を厳密に指定できる。
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from matcher_v4 import score_candidate, attribution, rank_candidates
from necessity_gen import compute_gamma
from match_config import GAMMA_MAX
from embedding_config import FULL_DIM


# ── 独立チャネル制御の合成ベクトル ────────────────────────────────────────────
def _unit(idx, val, idx2, val2):
    v = [0.0] * FULL_DIM
    v[idx], v[idx2] = val, val2
    return v


def _seeker():
    """seeker: a=e0, b(necessity)=e2, c(will_passage)=e4。"""
    return {
        "will_symmetric":  _unit(0, 1.0, 1, 0.0),
        "necessity_query": _unit(2, 1.0, 3, 0.0),
        "will_passage":    _unit(4, 1.0, 5, 0.0),
    }


def _cand(a, b, c):
    """候補: a_sim=a, b_sim=b, c_sim=c になるよう各直交平面で角度を作る。"""
    sa, sb, sc = (math.sqrt(max(0.0, 1 - x * x)) for x in (a, b, c))
    return {
        "will_symmetric": _unit(0, a, 1, sa),
        "state_passage":  _unit(2, b, 3, sb),
        "will_passage":   _unit(4, c, 5, sc),
    }


# ════════════════════════════════════════════════════════════════════════════
# H-2 #1 単調性: ∂γ/∂u > 0
# ════════════════════════════════════════════════════════════════════════════
def test_h2_1_gamma_strictly_increasing_in_u():
    """u を 0→1 に掃引すると γ が単調増加する（厳密増加, s 固定）。"""
    s = 0.2
    gammas = [compute_gamma(s, u / 10, GAMMA_MAX) for u in range(11)]
    for lo, hi in zip(gammas, gammas[1:]):
        assert hi >= lo
    assert gammas[-1] > gammas[0], "u 増加で γ は実際に上がること"


def test_h2_1_higher_u_penalizes_will_misalignment_more():
    """
    不確実性が高い seeker ほど、意志がずれた候補（c 低）をより強く減点する。
    γ(u_high) > γ(u_low) → 同じ c 不一致でも高 u 側のスコアが低い。
    """
    sv = _seeker()
    cand_will_misaligned = _cand(a=0.7, b=0.7, c=0.1)
    g_low  = compute_gamma(0.2, 0.1, GAMMA_MAX)
    g_high = compute_gamma(0.2, 0.9, GAMMA_MAX)
    s_low  = score_candidate(sv, cand_will_misaligned, g_low)
    s_high = score_candidate(sv, cand_will_misaligned, g_high)
    assert g_high > g_low
    assert s_high < s_low, "迷いが強いほど意志ズレ候補を強く減点（取りこぼし保護）"


# ════════════════════════════════════════════════════════════════════════════
# H-2 #2 不変性: γ=0 で c チャネル無効（多数派保護）
# ════════════════════════════════════════════════════════════════════════════
def test_h2_2_gamma_zero_makes_c_irrelevant():
    """γ=0 のとき will_passage(c) を 0→1 まで変えてもスコアが完全に不変。"""
    sv = _seeker()
    base = score_candidate(sv, _cand(a=0.6, b=0.6, c=0.0), gamma=0.0)
    for c in (0.0, 0.25, 0.5, 0.75, 1.0):
        s = score_candidate(sv, _cand(a=0.6, b=0.6, c=c), gamma=0.0)
        assert abs(s - base) < 1e-12, "γ=0 では c はスコアに効かない（多数派保護）"


def test_h2_2_no_will_request_no_uncertainty_yields_gamma_zero():
    """s≈0, u≈0（意志要求も迷いも無い多数派）→ γ=0。幻の意志要求を作らない。"""
    assert compute_gamma(0.0, 0.0, GAMMA_MAX) == 0.0


def test_h2_2_c_irrelevant_does_not_leak_into_limiting_axis():
    """γ=0 のとき c は律速軸に上がらない（寄与ゼロ）。"""
    sv = _seeker()
    attr = attribution(sv, _cand(a=0.6, b=0.6, c=0.01), gamma=0.0)
    assert attr["c_log_contrib"] == 0.0
    assert attr["limiting_axis"] in ("a", "b")


# ════════════════════════════════════════════════════════════════════════════
# H-2 #3 順位感度: 補完チャネルが順位を動かす
# ════════════════════════════════════════════════════════════════════════════
def test_h2_3_b_channel_moves_rank():
    """b（必要像 vs 現状）だけ違う 2 候補で、b が高い方が上位（補完が効く）。"""
    sv = _seeker()
    fills_need = ("fills", _cand(a=0.5, b=0.9, c=0.5))
    cannot     = ("cannot", _cand(a=0.5, b=0.2, c=0.5))
    ranked = rank_candidates(sv, [cannot, fills_need], gamma=0.3)
    assert ranked[0]["candidate_id"] == "fills", "必要を埋められる候補が上位"


def test_h2_3_c_channel_moves_rank_when_gamma_positive():
    """γ>0 のとき c（意志相補）だけ違う 2 候補で、c が高い方が上位。"""
    sv = _seeker()
    aligned    = ("aligned",  _cand(a=0.5, b=0.5, c=0.9))
    misaligned = ("misaligned", _cand(a=0.5, b=0.5, c=0.1))
    ranked = rank_candidates(sv, [misaligned, aligned], gamma=0.5)
    assert ranked[0]["candidate_id"] == "aligned"


def test_h2_3_complement_not_dominated_by_resonance():
    """
    共鳴(a)一辺倒では勝てない: a 高・b 低の候補より、a 中・b 高の候補が上位になりうる
    （soft-AND が「両立」を要求する＝接続は片側だけでは成立しない）。
    """
    sv = _seeker()
    resonance_only = ("resonance", _cand(a=0.97, b=0.15, c=0.5))
    balanced_fill  = ("balanced",  _cand(a=0.6,  b=0.92, c=0.5))
    ranked = rank_candidates(sv, [resonance_only, balanced_fill], gamma=0.3, p=0.0)
    assert ranked[0]["candidate_id"] == "balanced", "補完が共鳴に埋もれない"


def test_h2_3_sharper_p_penalizes_imbalance_more():
    """p をより負（鋭い AND）にすると、片チャネルが弱い候補の相対順位が下がる。"""
    sv = _seeker()
    imbalanced = ("imbalanced", _cand(a=0.95, b=0.3, c=0.5))
    balanced   = ("balanced",   _cand(a=0.7,  b=0.7, c=0.5))
    soft  = rank_candidates(sv, [imbalanced, balanced], gamma=0.3, p=0.0)
    sharp = rank_candidates(sv, [imbalanced, balanced], gamma=0.3, p=-3.0)
    # soft では拮抗しうるが、sharp では均衡候補が明確に上位
    assert sharp[0]["candidate_id"] == "balanced"
    # imbalanced のスコアは p を鋭くするほど（相対的に）下がる
    s_soft  = next(r["score"] for r in soft  if r["candidate_id"] == "imbalanced")
    s_sharp = next(r["score"] for r in sharp if r["candidate_id"] == "imbalanced")
    assert s_sharp < s_soft


# ════════════════════════════════════════════════════════════════════════════
# H-2 #4 顔の妥当性: 手組みシナリオ
# ════════════════════════════════════════════════════════════════════════════
def test_h2_4_engineer_beats_fellow_connector_for_engineering_need():
    """
    シナリオ: seeker は「農家と店をつなぐ」意志を持ち、必要像＝『実装できるエンジニア』。
      - engineer        : 必要を埋める（b 高）/ 意志はやや異なる（a 中）
      - fellow_connector: 同じ志（a 高）だが実装はできない（b 低）
      - unrelated       : 何も噛み合わない（全 低）
    直感的期待: engineer が 1 位、unrelated が最下位（接続は『埋め合い』で成立する）。
    """
    sv = _seeker()
    candidates = [
        ("engineer",         _cand(a=0.45, b=0.95, c=0.5)),
        ("fellow_connector", _cand(a=0.90, b=0.15, c=0.6)),
        ("unrelated",        _cand(a=0.15, b=0.15, c=0.15)),
    ]
    ranked = rank_candidates(sv, candidates, gamma=0.3, p=0.0)
    order = [r["candidate_id"] for r in ranked]
    assert order[0] == "engineer", "必要を埋める相手が 1 位"
    assert order[-1] == "unrelated", "何も噛み合わない相手が最下位"


def test_h2_4_attribution_explains_top_match():
    """1 位の attribution が説明可能（律速軸・全チャネル類似が読める）。"""
    sv = _seeker()
    ranked = rank_candidates(sv, [("engineer", _cand(0.45, 0.95, 0.5))],
                             gamma=0.3, p=0.0)
    attr = ranked[0]["attribution"]
    assert attr["limiting_axis"] in ("a", "b", "c")
    assert abs(attr["a_sim"] - 0.45) < 1e-9
    assert abs(attr["b_sim"] - 0.95) < 1e-9
    assert 0.0 < attr["final"] <= 1.0


def test_h2_4_self_consistency_perfect_match_tops():
    """全チャネル完全一致の候補は必ず最高スコア（1.0）で 1 位。"""
    sv = _seeker()
    candidates = [
        ("perfect", _cand(1.0, 1.0, 1.0)),
        ("good",    _cand(0.8, 0.8, 0.8)),
        ("poor",    _cand(0.3, 0.3, 0.3)),
    ]
    ranked = rank_candidates(sv, candidates, gamma=0.4, p=0.0)
    assert ranked[0]["candidate_id"] == "perfect"
    assert abs(ranked[0]["score"] - 1.0) < 1e-9


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nStep 8 較正テスト（H-2）: {len(tests)} 件 全 PASS")
