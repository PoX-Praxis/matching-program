"""
Step 5 DoD 検証テスト — 照合エンジン（E章）

DoD: power_mean 数学的性質（幾何/算術/調和/単調性）、
     nested complement スコア（γ ゲート・チャネル寄与）、
     attribution 律速軸判定、shortlist 順序、rank_candidates 統合。
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from matcher_v4 import (
    power_mean, score_candidate, attribution, shortlist, rank_candidates,
)
from embedding_config import FULL_DIM, SHORT_DIM
from match_config import GAMMA_EPS


# ── テストベクトルヘルパ ──────────────────────────────────────────────────────
def _e1(dim=FULL_DIM):
    """標準基底 e1 = [1, 0, 0, ...]"""
    v = [0.0] * dim
    v[0] = 1.0
    return v


def _vec_cos(cos_val, dim=FULL_DIM):
    """e1 との cosine が cos_val の単位ベクトル（第2成分で補完）。"""
    sin_val = math.sqrt(max(0.0, 1.0 - cos_val ** 2))
    return [cos_val, sin_val] + [0.0] * (dim - 2)


def _seeker(will_sym=None, will_pas=None, necessity=None):
    return {
        "will_symmetric":  will_sym   or _e1(),
        "will_passage":    will_pas   or _e1(),
        "necessity_query": necessity  or _e1(),
    }


def _candidate(will_sym=None, state_pas=None, will_pas=None):
    return {
        "will_symmetric": will_sym  or _e1(),
        "state_passage":  state_pas or _e1(),
        "will_passage":   will_pas  or _e1(),
    }


# ── power_mean ────────────────────────────────────────────────────────────────
def test_power_mean_geometric():
    """p=0 → 幾何平均（等重み）"""
    a, b = 0.25, 0.75
    assert abs(power_mean([a, b], [1.0, 1.0], 0.0) - math.sqrt(a * b)) < 1e-9


def test_power_mean_arithmetic():
    """p=1 → 加重算術平均"""
    expected = (2 * 0.4 + 1 * 0.6) / 3.0
    assert abs(power_mean([0.4, 0.6], [2.0, 1.0], 1.0) - expected) < 1e-9


def test_power_mean_harmonic():
    """p=-1 → 加重調和平均（等重み）"""
    a, b = 0.4, 0.6
    expected = 2 * a * b / (a + b)
    assert abs(power_mean([a, b], [1.0, 1.0], -1.0) - expected) < 1e-9


def test_power_mean_monotone_in_p():
    """∂M_p/∂p ≥ 0（異なる値では厳密に単調増加）"""
    vals, w = [0.3, 0.7], [1.0, 1.0]
    assert power_mean(vals, w, -1.0) <= power_mean(vals, w, 0.0) + 1e-12
    assert power_mean(vals, w, 0.0)  <= power_mean(vals, w, 1.0) + 1e-12


def test_power_mean_equal_values():
    """全値が等しければ p によらず同値"""
    for p in (-1.0, 0.0, 1.0, 2.0):
        assert abs(power_mean([0.5, 0.5, 0.5], [1.0, 2.0, 3.0], p) - 0.5) < 1e-9


def test_power_mean_zero_weight():
    """重み 0 の成分は結果に影響しない"""
    assert abs(power_mean([0.4, 0.9], [1.0, 0.0], 0.0) - 0.4) < 1e-9
    assert abs(power_mean([0.4, 0.9], [0.0, 1.0], 0.0) - 0.9) < 1e-9


# ── score_candidate ───────────────────────────────────────────────────────────
def test_score_self_high():
    """完全自己一致（全 cosine=1）→ スコア = 1.0"""
    assert abs(score_candidate(_seeker(), _candidate(), gamma=0.3) - 1.0) < 1e-9


def test_score_gamma_zero_ignores_c():
    """γ=0 のとき c チャネルを変えてもスコアが不変"""
    sv = _seeker()
    s0 = score_candidate(sv, _candidate(will_pas=_vec_cos(0.0)), gamma=0.0)
    s1 = score_candidate(sv, _candidate(will_pas=_e1()),          gamma=0.0)
    assert abs(s0 - s1) < 1e-12


def test_score_gamma_positive_uses_c():
    """γ>0 のとき c が良い候補の方が高スコア"""
    sv = _seeker()
    s_bad  = score_candidate(sv, _candidate(will_pas=_vec_cos(0.0)), gamma=0.4)
    s_good = score_candidate(sv, _candidate(will_pas=_e1()),          gamma=0.4)
    assert s_good > s_bad


def test_score_good_beats_bad():
    """全チャネルで優る候補の方が高スコア"""
    sv = _seeker()
    s_good = score_candidate(sv, _candidate(_vec_cos(0.9), _vec_cos(0.9), _vec_cos(0.9)), gamma=0.3)
    s_bad  = score_candidate(sv, _candidate(_vec_cos(0.2), _vec_cos(0.2), _vec_cos(0.2)), gamma=0.3)
    assert s_good > s_bad


def test_score_bounded():
    """スコアは (0, 1] の範囲"""
    sv = _seeker()
    cv = _candidate(_vec_cos(0.5), _vec_cos(0.3), _vec_cos(0.7))
    s = score_candidate(sv, cv, gamma=0.2)
    assert 0.0 < s <= 1.0 + 1e-12


def test_score_higher_gamma_amplifies_c_effect():
    """γ が高いほど c チャネル不一致のペナルティが大きい"""
    sv  = _seeker()
    cv  = _candidate(will_pas=_vec_cos(0.1))   # c が弱い
    s_low_gamma  = score_candidate(sv, cv, gamma=0.1)
    s_high_gamma = score_candidate(sv, cv, gamma=0.5)
    assert s_low_gamma > s_high_gamma


# ── attribution ───────────────────────────────────────────────────────────────
def test_attribution_keys():
    """attribution dict に E-3 必須キーが揃う"""
    attr = attribution(_seeker(), _candidate(_vec_cos(0.7), _vec_cos(0.5), _vec_cos(0.8)), gamma=0.3)
    for k in ("a_sim", "b_sim", "c_sim", "ga", "gb", "gc",
              "complement", "final", "a_log_contrib", "b_log_contrib",
              "c_log_contrib", "limiting_axis"):
        assert k in attr, f"キー欠落: {k}"


def test_attribution_limiting_axis_a():
    """a チャネルが最弱 → 律速軸 = 'a'"""
    cv = _candidate(will_sym=_vec_cos(0.05), state_pas=_e1(), will_pas=_e1())
    assert attribution(_seeker(), cv, gamma=0.4, alpha=1.0, beta=1.0)["limiting_axis"] == "a"


def test_attribution_limiting_axis_b():
    """b チャネルが最弱 → 律速軸 = 'b'"""
    cv = _candidate(will_sym=_e1(), state_pas=_vec_cos(0.05), will_pas=_e1())
    assert attribution(_seeker(), cv, gamma=0.4, alpha=1.0, beta=1.0)["limiting_axis"] == "b"


def test_attribution_score_matches_score_candidate():
    """attribution の final == score_candidate の戻り値"""
    sv, cv, gamma = _seeker(), _candidate(_vec_cos(0.6), _vec_cos(0.7), _vec_cos(0.5)), 0.3
    assert abs(attribution(sv, cv, gamma)["final"] - score_candidate(sv, cv, gamma)) < 1e-12


def test_attribution_c_log_zero_when_gamma_eps():
    """γ ≤ GAMMA_EPS のとき c_log_contrib = 0（c が律速軸に上がらない）"""
    cv = _candidate(will_sym=_e1(), state_pas=_e1(), will_pas=_vec_cos(0.0))
    attr = attribution(_seeker(), cv, gamma=0.0)
    assert attr["c_log_contrib"] == 0.0


# ── shortlist ─────────────────────────────────────────────────────────────────
def test_shortlist_ordering():
    """256-dim コサイン降順で上位 k 件"""
    q = _e1(SHORT_DIM)
    cands = [
        ("c1", _vec_cos(0.9, SHORT_DIM)),
        ("c2", _vec_cos(0.3, SHORT_DIM)),
        ("c3", _vec_cos(0.7, SHORT_DIM)),
    ]
    assert shortlist(q, cands, k=2) == ["c1", "c3"]


def test_shortlist_fewer_than_k():
    """候補数 < k でも全件返す"""
    q = _e1(SHORT_DIM)
    assert shortlist(q, [("c1", _vec_cos(0.5, SHORT_DIM))], k=10) == ["c1"]


def test_shortlist_empty():
    """候補なし → 空リスト"""
    assert shortlist(_e1(SHORT_DIM), [], k=5) == []


# ── rank_candidates ───────────────────────────────────────────────────────────
def test_rank_candidates_ordering():
    """スコア降順ランキング"""
    sv = _seeker()
    cands = [
        ("low",  _candidate(_vec_cos(0.2), _vec_cos(0.2), _vec_cos(0.2))),
        ("high", _candidate(_vec_cos(0.9), _vec_cos(0.9), _vec_cos(0.9))),
        ("mid",  _candidate(_vec_cos(0.5), _vec_cos(0.5), _vec_cos(0.5))),
    ]
    ids = [r["candidate_id"] for r in rank_candidates(sv, cands, gamma=0.3)]
    assert ids == ["high", "mid", "low"]


def test_rank_candidates_top_k():
    """top_k で件数が制限される"""
    sv = _seeker()
    cands = [(f"c{i}", _candidate(_vec_cos(i / 10), _vec_cos(i / 10))) for i in range(5)]
    assert len(rank_candidates(sv, cands, gamma=0.2, top_k=3)) == 3


def test_rank_candidates_keys():
    """各結果に candidate_id / score / attribution が含まれる"""
    results = rank_candidates(_seeker(), [("x", _candidate())], gamma=0.2)
    assert len(results) == 1
    for k in ("candidate_id", "score", "attribution"):
        assert k in results[0], f"キー欠落: {k}"


def test_rank_candidates_empty():
    """候補なし → 空リスト"""
    assert rank_candidates(_seeker(), [], gamma=0.2) == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nStep 5 DoD テスト: {len(tests)} 件 全 PASS")
