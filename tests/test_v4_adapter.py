"""
アダプタ検証テスト — ①v4.2 統合プロンプトのネストJSON → /v4/seekers フラット body

_normalize_v4_body が、①構造化プロンプト（v4.2）の出す入れ子JSON
（seeker.意志 / seeker.現状.* / supporting_material / necessity.*）を、
/v4/seekers が読むフラット body（will_text / state_* / supporting_raw / necessity平置き）
へ機械的に変換することを確認する。値の検証・γ算出は下流が担う（アダプタは形だけ）。
"""
import sys, os
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from app import _normalize_v4_body, _NECESSITY_FIELDS
from necessity_gen import build_user_necessity, compute_gamma
from match_config import GAMMA_MAX


def _nested():
    return {
        "id": "alice_2025",
        "schema_version": "v4",
        "seeker": {
            "意志": "農家と店をつなぎたい",
            "現状": {
                "持っているもの": "現場の知識",
                "できること_型": "翻訳する動き",
                "縛られているもの": "技術がない",
                "未分類": "",
            },
        },
        "supporting_material": {
            "求めている": "実装できる人",
            "生テキスト": ["繋がれるはずの人が繋がれない"],
            "意志要求の素材": "一緒に背負える人がいい",
        },
        "necessity": {
            "necessity_text": "現場を実装に翻訳できる開発者",
            "gate_s": 0.6, "gate_u": 0.3, "p_sharpness": -0.4,
            "alpha": 1.0, "beta": 2.0, "evidence_span": "一緒に背負える",
            "generator": "Claude Opus 4.8",
        },
    }


# ── 二軸（意志/現状）とハンドルの平坦化 ────────────────────────────────────────
def test_will_and_state_flattened():
    flat = _normalize_v4_body(_nested())
    assert flat["will_text"] == "農家と店をつなぎたい"
    assert flat["state_have"] == "現場の知識"
    assert flat["state_can_type"] == "翻訳する動き"
    assert flat["state_bound"] == "技術がない"
    assert flat["state_unsorted"] == ""


def test_id_maps_to_user_id():
    assert _normalize_v4_body(_nested())["user_id"] == "alice_2025"


def test_supporting_material_becomes_supporting_raw():
    flat = _normalize_v4_body(_nested())
    assert flat["supporting_raw"]["求めている"] == "実装できる人"
    assert flat["supporting_raw"]["意志要求の素材"] == "一緒に背負える人がいい"


# ── necessity ブロックをトップレベルへ展開 ────────────────────────────────────
def test_necessity_block_lifted_to_toplevel():
    flat = _normalize_v4_body(_nested())
    for k in _NECESSITY_FIELDS:
        assert k in flat, f"{k} がトップレベルに無い"
    assert flat["necessity_text"] == "現場を実装に翻訳できる開発者"
    assert flat["gate_s"] == 0.6 and flat["p_sharpness"] == -0.4
    assert flat["generator"] == "Claude Opus 4.8"


# ── 後方互換: 既にフラットな body はそのまま ──────────────────────────────────
def test_flat_body_passthrough_unchanged():
    flat_in = {"will_text": "x", "state_have": "y", "necessity_text": "z",
               "gate_s": 0.1, "gate_u": 0.1}
    out = _normalize_v4_body(flat_in)
    assert out is flat_in  # 変換せずそのまま（同一オブジェクト）


def test_non_dict_passthrough():
    assert _normalize_v4_body("not a dict") == "not a dict"


# ── necessity 無し → フォールバック経路（necessity_text が出ない）────────────────
def test_missing_necessity_falls_back():
    nested = {"seeker": {"意志": "やりたい", "現状": {}}, "supporting_material": {}}
    flat = _normalize_v4_body(nested)
    assert flat["will_text"] == "やりたい"
    assert "necessity_text" not in flat  # → is_fallback（サーバー生成）に落ちる


def test_missing_state_slots_default_empty():
    nested = {"seeker": {"意志": "a", "現状": {"持っているもの": "b"}}}
    flat = _normalize_v4_body(nested)
    assert flat["state_have"] == "b"
    assert flat["state_can_type"] == "" and flat["state_unsorted"] == ""


# ── 通し: 平坦化後の body が build_user_necessity にそのまま乗る ────────────────
def test_flattened_body_flows_into_build_user_necessity():
    flat = _normalize_v4_body(_nested())
    profile = {k: flat.get(k, "") for k in
               ("will_text", "state_have", "state_can_type", "state_bound", "state_unsorted")}
    profile["supporting_redacted"] = {}
    nec = build_user_necessity(profile, flat)  # flat をそのまま supplied として渡す
    # γ はサーバー側 compute_gamma 算出（供給値ではない）
    assert nec["gamma"] == compute_gamma(0.6, 0.3, GAMMA_MAX)
    assert nec["is_generated"] is False
    assert nec["generator_model_tag"] == "user-supplied"
    assert nec["generator_name"] == "Claude Opus 4.8"


def test_flattened_clamps_out_of_range():
    """アダプタは形だけ整え、範囲クランプは build_user_necessity が担う（無検証で信頼しない）。"""
    nested = _nested()
    nested["necessity"]["gate_s"] = 1.7   # 範囲外
    nested["necessity"]["alpha"] = -3.0   # 負
    flat = _normalize_v4_body(nested)
    profile = {k: flat.get(k, "") for k in
               ("will_text", "state_have", "state_can_type", "state_bound", "state_unsorted")}
    profile["supporting_redacted"] = {}
    nec = build_user_necessity(profile, flat)
    assert nec["gate_s"] == 1.0 and nec["alpha"] == 0.0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nアダプタテスト: {len(tests)} 件 全 PASS")
