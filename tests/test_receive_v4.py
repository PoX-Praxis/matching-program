"""
束2b DoD 検証テスト — 必要像「受け取り」経路（B-1/B-2/B-3）

Flask を通さず、/v4/seekers エンドポイントが呼ぶ building block
（necessity_gen.build_user_necessity / db_v4.receive_profile_v4 /
 generate_necessity_v4 / vectorize_profile_v4 / check_and_flag_regeneration）
を MemoryStore + stub embedding で検証する。非同期ジョブは本体関数を同期実行して代替。

DoD:
  (i)   必要像同梱JSON → 検証 → γ算出 → 保存 → 4ベクトル生成（E2E・正式ルート）
  (ii)  各自AI出力を無検証で信頼しない: 範囲クランプ / 供給γ不採用 / 必須キー欠落は拒否
  (iii) フォールバック経路: 必要像なし → サーバー生成（demo）→ 保存 → ベクトル化
  (iv)  意志/現状 編集で src_input_hash 不一致 → needs_regeneration
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db_v4 import (MemoryStore, receive_profile_v4, generate_necessity_v4,
                   vectorize_profile_v4, check_and_flag_regeneration,
                   GEN_PREPARING, GEN_READY, GEN_NEEDS_REGEN)
from necessity_gen import build_user_necessity, compute_gamma
from pii_redaction import redact_for_storage
from embedding_config import MODEL_TAG, FULL_DIM
from match_config import GAMMA_MAX


def _profile_input(will="農家と店をつなぎたい", want="実装できるエンジニア"):
    return {
        "will_text": will,
        "state_have": "現場の知識",
        "state_can_type": "動ける型",
        "state_bound": "技術がない",
        "state_unsorted": "",
        "supporting_raw": {"求めている": want},
    }


def _supplied(gate_s=0.6, gate_u=0.3, generator="ChatGPT-5", **over):
    d = {
        "necessity_text": "現場を実装に翻訳できる開発者",
        "gate_s": gate_s, "gate_u": gate_u, "p_sharpness": -0.4,
        "alpha": 1.0, "beta": 2.0, "evidence_span": "同じ課題に向き合う姿勢を求める",
        "generator": generator,
    }
    d.update(over)
    return d


def _receive_user_supplied(store, pid, profile_input, supplied):
    """エンドポイント A 経路（正式ルート）の受付を同期で再現する。"""
    supporting_redacted, _ = redact_for_storage(profile_input.get("supporting_raw") or {})
    hash_profile = {**profile_input, "supporting_redacted": supporting_redacted}
    necessity = build_user_necessity(hash_profile, supplied)
    receive_profile_v4(store, pid, profile_input, necessity,
                       generation_status=GEN_PREPARING)
    return necessity


# ── (i) 正式ルート E2E ────────────────────────────────────────────────────────
def test_user_supplied_end_to_end():
    store = MemoryStore()
    p = _profile_input()
    nec = _receive_user_supplied(store, "u1", p, _supplied(gate_s=0.6, gate_u=0.3))

    # 受付時点: profile は preparing、necessity は保存済み
    assert store.get_profile_status("u1")["generation_status"] == GEN_PREPARING
    saved = store.get_necessity("u1", MODEL_TAG)
    assert saved["necessity_text"] == "現場を実装に翻訳できる開発者"

    # γ はサーバー側で compute_gamma 算出（②所有）
    assert nec["gamma"] == compute_gamma(0.6, 0.3, GAMMA_MAX)
    assert nec["is_generated"] is False           # 本人側AI生成＝サーバー生成物ではない
    assert nec["generator_model_tag"] == "user-supplied"
    assert saved["generator_name"] == "ChatGPT-5"

    # 非同期ジョブ相当: ベクトル化 → ready
    vectorize_profile_v4(store, "u1", p, nec["necessity_text"])
    assert store.get_profile_status("u1")["generation_status"] == GEN_READY
    v = store.vectors[("u1", MODEL_TAG)]
    assert set(v) == {"will_symmetric", "will_passage", "state_passage", "necessity_query"}
    for k in v:
        assert len(v[k]) == FULL_DIM


def test_user_supplied_generator_unknown_when_absent():
    store = MemoryStore()
    p = _profile_input()
    supplied = _supplied()
    del supplied["generator"]
    _receive_user_supplied(store, "u1", p, supplied)
    assert store.get_necessity("u1", MODEL_TAG)["generator_name"] == "user-supplied(unknown)"


# ── (ii) 無検証で信頼しない: クランプ / 供給γ不採用 / 欠落拒否 ────────────────────
def test_supplied_values_are_clamped():
    """gate_s>1 / gate_u<0 / alpha<0 を [0,1]・≥0 にクランプ（各自AI出力を信頼しない）。"""
    store = MemoryStore()
    p = _profile_input()
    nec = _receive_user_supplied(store, "u1", p,
                                 _supplied(gate_s=1.7, gate_u=-0.5, alpha=-3.0))
    assert nec["gate_s"] == 1.0 and nec["gate_u"] == 0.0
    assert nec["alpha"] == 0.0
    # クランプ後の値で γ を算出
    assert nec["gamma"] == compute_gamma(1.0, 0.0, GAMMA_MAX)


def test_supplied_gamma_is_ignored():
    """供給された gamma は採用しない（フロア捏造・改竄防止）。compute_gamma で再計算。"""
    store = MemoryStore()
    p = _profile_input()
    supplied = _supplied(gate_s=0.2, gate_u=0.1)
    supplied["gamma"] = 0.99  # 悪意ある/誤った供給値
    nec = _receive_user_supplied(store, "u1", p, supplied)
    assert nec["gamma"] == compute_gamma(0.2, 0.1, GAMMA_MAX)
    assert nec["gamma"] != 0.99


def test_supplied_missing_required_key_rejected():
    """必須キー欠落は ValueError（受付前に 400 相当で弾く）。"""
    store = MemoryStore()
    p = _profile_input()
    supplied = _supplied()
    del supplied["gate_s"]
    try:
        _receive_user_supplied(store, "u1", p, supplied)
        assert False, "必須キー欠落は拒否されるべき"
    except ValueError:
        pass


def test_supplied_empty_necessity_rejected():
    store = MemoryStore()
    p = _profile_input()
    try:
        _receive_user_supplied(store, "u1", p, _supplied(necessity_text=""))
        assert False
    except ValueError:
        pass


# ── (iii) フォールバック経路 ──────────────────────────────────────────────────
def test_fallback_generates_and_vectorizes():
    """必要像なし → 受付は profile のみ → 非同期でサーバー生成（demo）→ ベクトル化 ready。"""
    store = MemoryStore()
    p = _profile_input(want="必要な実証パートナー")

    # 受付（同期）: necessity=None で profile のみ保存
    receive_profile_v4(store, "u1", p, None, generation_status=GEN_PREPARING)
    assert store.get_profile_status("u1")["generation_status"] == GEN_PREPARING
    assert store.get_necessity("u1", MODEL_TAG) is None

    # 非同期ジョブ相当: サーバー生成（ANTHROPIC_API_KEY 無 → demo-fallback）
    nec = generate_necessity_v4(store, "u1", p)
    assert nec["is_generated"] is True
    assert nec["generator_model_tag"] == "demo-fallback"
    saved = store.get_necessity("u1", MODEL_TAG)
    assert saved["necessity_text"]  # 生成された
    assert saved["gamma"] == compute_gamma(saved["gate_s"], saved["gate_u"], GAMMA_MAX)

    vectorize_profile_v4(store, "u1", p, nec["necessity_text"])
    assert store.get_profile_status("u1")["generation_status"] == GEN_READY


def test_fallback_uses_injected_generator():
    store = MemoryStore()
    p = _profile_input()
    receive_profile_v4(store, "u1", p, None)

    def fake(inputs):
        return {"necessity_text": "注入フォールバック像", "gate_s": 0.5, "gate_u": 0.2,
                "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0, "evidence_span": ""}

    nec = generate_necessity_v4(store, "u1", p, generator_fn=fake)
    assert nec["necessity_text"] == "注入フォールバック像"
    assert store.get_necessity("u1", MODEL_TAG)["necessity_text"] == "注入フォールバック像"


# ── (iv) 再生成判定（B-3）─────────────────────────────────────────────────────
def test_regeneration_flag_on_will_edit():
    """意志を編集すると src_input_hash が食い違い needs_regeneration が立つ。"""
    store = MemoryStore()
    p = _profile_input(will="つなぎたい")
    _receive_user_supplied(store, "u1", p, _supplied())

    # 未編集: 最新なのでフラグは立たない
    assert check_and_flag_regeneration(store, "u1") is False
    assert store.get_profile_status("u1")["generation_status"] == GEN_PREPARING

    # 意志を編集（プロフィール本文が変わる）
    store.profiles["u1"]["will_text"] = "まったく別の意志に書き換えた"
    assert check_and_flag_regeneration(store, "u1") is True
    assert store.get_profile_status("u1")["generation_status"] == GEN_NEEDS_REGEN


def test_regeneration_flag_on_state_edit():
    store = MemoryStore()
    p = _profile_input()
    _receive_user_supplied(store, "u1", p, _supplied())
    store.profiles["u1"]["state_have"] = "新しく獲得した技術力"
    assert check_and_flag_regeneration(store, "u1") is True
    assert store.get_profile_status("u1")["generation_status"] == GEN_NEEDS_REGEN


def test_no_regeneration_without_necessity():
    """necessity 未保存なら判定不能として False（フラグを立てない）。"""
    store = MemoryStore()
    receive_profile_v4(store, "u1", _profile_input(), None)
    assert check_and_flag_regeneration(store, "u1") is False


def test_no_regeneration_for_missing_profile():
    store = MemoryStore()
    assert check_and_flag_regeneration(store, "ghost") is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\n束2b 受け取り経路テスト: {len(tests)} 件 全 PASS")
