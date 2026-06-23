"""
Step 4 DoD 検証テスト — 必要像生成層（②生成LLM / 仕様書 D章）

DoD: 意志+現状+地の文(redacted) から必要像と s,u,p,α,β を生成、
     γ を不確実性ベースで算出（フロア禁止）、src_input_hash で再生成判定。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from necessity_gen import (
    compute_gamma, compute_src_hash, needs_regeneration, gather_inputs,
    generate_necessity, _validate, _demo_generate, PROMPT_VERSION,
)
from match_config import GAMMA_MAX


PROFILE = {
    "will_text": "農家と飲食店を直接つなぎたい",
    "state_have": "販路の理解",
    "state_can_type": "現場に入り込む型",
    "state_bound": "実装技術がない",
    "state_unsorted": "",
    "supporting_redacted": {
        "求めている": "アプリを実装できるエンジニア",
        "意志要求の素材": "一緒に背負える人がいい",
        "連言選言の素材": "未取得",
        "チャネル重み素材": "未取得",
        "生テキスト": ["現場を回ってきた"],
        "要約文": "農業に長く関わってきた人",
    },
}


# ── compute_gamma（D-2: フロア禁止・不確実性ベース）──────────────────────────
def test_gamma_zero_when_no_will_request_and_no_uncertainty():
    """f(s≈0, u≈0) → 0（多数派保護。フロアを足さない）。"""
    assert compute_gamma(0.0, 0.0, GAMMA_MAX) == 0.0


def test_gamma_monotonic_in_u():
    """∂γ/∂u > 0（迷いで取りこぼし保護を上げる）。"""
    g_low = compute_gamma(0.2, 0.1, GAMMA_MAX)
    g_high = compute_gamma(0.2, 0.9, GAMMA_MAX)
    assert g_high > g_low


def test_gamma_no_fixed_floor_on_s():
    """s に固定フロアを足さない: s=0 のとき u=0 なら厳密に 0。"""
    # s を少し上げても u=0 なら gamma = gamma_max * s（フロア加算なし）
    assert compute_gamma(0.3, 0.0, GAMMA_MAX) == GAMMA_MAX * 0.3


def test_gamma_capped_at_gamma_max():
    """γ は gamma_max を超えない。"""
    assert compute_gamma(1.0, 1.0, GAMMA_MAX) <= GAMMA_MAX + 1e-12
    assert compute_gamma(0.9, 0.9, GAMMA_MAX) <= GAMMA_MAX + 1e-12


def test_gamma_clamps_inputs():
    """s,u は [0,1] にクランプ。"""
    assert compute_gamma(-1.0, -1.0, GAMMA_MAX) == 0.0
    assert compute_gamma(5.0, 5.0, GAMMA_MAX) <= GAMMA_MAX + 1e-12


# ── src_input_hash（B-3 / #6）─────────────────────────────────────────────────
def test_src_hash_deterministic():
    h1 = compute_src_hash(PROFILE)
    h2 = compute_src_hash(PROFILE)
    assert h1 == h2 and len(h1) == 64


def test_src_hash_changes_on_will():
    base = compute_src_hash(PROFILE)
    p2 = dict(PROFILE, will_text="別の意志")
    assert compute_src_hash(p2) != base


def test_src_hash_changes_on_state():
    base = compute_src_hash(PROFILE)
    p2 = dict(PROFILE, state_bound="別の制約")
    assert compute_src_hash(p2) != base


def test_src_hash_changes_on_supporting():
    base = compute_src_hash(PROFILE)
    sup2 = dict(PROFILE["supporting_redacted"], 要約文="更新された地の文")
    p2 = dict(PROFILE, supporting_redacted=sup2)
    assert compute_src_hash(p2) != base


def test_src_hash_changes_on_prompt_version():
    base = compute_src_hash(PROFILE)
    assert compute_src_hash(PROFILE, prompt_version="necessity-v9") != base


def test_src_hash_changes_on_model_tag():
    base = compute_src_hash(PROFILE)
    assert compute_src_hash(PROFILE, model_tag="other-model") != base


def test_needs_regeneration():
    h = compute_src_hash(PROFILE)
    assert needs_regeneration(PROFILE, "stale-hash") is True
    assert needs_regeneration(PROFILE, h) is False


# ── gather_inputs（D-1: redacted / 未取得明示）────────────────────────────────
def test_gather_inputs_redacts():
    p = dict(PROFILE, will_text="連絡 a@b.com")
    inp = gather_inputs(p)
    assert "[EMAIL]" in inp["will"] and "a@b.com" not in inp["will"]


def test_gather_inputs_marks_unfetched():
    inp = gather_inputs(PROFILE)
    assert inp["supporting"]["連言選言の素材"] == "未取得"


# ── generate_necessity（差し替え generator_fn）───────────────────────────────
def test_generate_with_injected_fn():
    def fake(inputs):
        return {"necessity_text": "実装できるエンジニア", "gate_s": 0.6, "gate_u": 0.2,
                "p_sharpness": -0.5, "alpha": 1.0, "beta": 1.5, "evidence_span": "背負える人"}
    out = generate_necessity(PROFILE, generator_fn=fake)
    assert out["necessity_text"] == "実装できるエンジニア"
    assert out["gamma"] == compute_gamma(0.6, 0.2, GAMMA_MAX)
    assert out["is_generated"] is True
    assert out["generator_prompt_version"] == PROMPT_VERSION
    assert out["src_input_hash"] == compute_src_hash(PROFILE)
    # derived_necessity 列に必要なキーが揃っていること
    for k in ("necessity_text", "gate_s", "gate_u", "gamma", "p_sharpness",
              "alpha", "beta", "evidence_span", "src_input_hash",
              "generator_prompt_version", "generator_model_tag", "model_tag"):
        assert k in out


def test_demo_generate_valid():
    """API キー無しの demo 生成が検証を通る構造を返す。"""
    out = generate_necessity(PROFILE)   # generator_fn 無し・キー無し→demo
    assert out["necessity_text"].strip()
    assert 0.0 <= out["gate_s"] <= 1.0
    assert 0.0 <= out["gate_u"] <= 1.0


def test_demo_uses_declared_want():
    raw = _demo_generate(gather_inputs(PROFILE))
    assert raw["necessity_text"] == "アプリを実装できるエンジニア"


def test_demo_high_uncertainty_when_materials_missing():
    """素材が未取得中心なら u が高い（D-3 既定の素朴版）。"""
    p = dict(PROFILE, supporting_redacted={
        "求めている": "未取得", "意志要求の素材": "未取得",
        "連言選言の素材": "未取得", "チャネル重み素材": "未取得"})
    raw = _demo_generate(gather_inputs(p))
    assert raw["gate_u"] >= 0.9


# ── 出力検証 ──────────────────────────────────────────────────────────────────
def test_validate_rejects_missing_keys():
    try:
        _validate({"necessity_text": "x"})
        assert False
    except ValueError:
        pass


def test_validate_clamps_ranges():
    out = _validate({"necessity_text": "x", "gate_s": 2.0, "gate_u": -1.0,
                     "p_sharpness": -0.7, "alpha": -3.0, "beta": 1.0,
                     "evidence_span": "e"})
    assert out["gate_s"] == 1.0 and out["gate_u"] == 0.0
    assert out["alpha"] == 0.0   # 負は 0 にクランプ


def test_validate_rejects_empty_necessity():
    try:
        _validate({"necessity_text": "  ", "gate_s": 0.5, "gate_u": 0.5,
                   "p_sharpness": 0, "alpha": 1, "beta": 1, "evidence_span": ""})
        assert False
    except ValueError:
        pass


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nStep 4 DoD テスト: {len(tests)} 件 全 PASS")
