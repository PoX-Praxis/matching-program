"""
Step 3 DoD 検証テスト — embedding サービス（仕様書 C章）

DoD（Qwen3 ホスト未確定のため stub バックエンドで検証）:
  embed(text, role) が full(1024)+short(256) を返す / prefix は設定外出し /
  L2 正規化 / 決定論的 / MRL 切出し / guard(EPS) / build_vectors が "未取得" を除外。
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from embedding_service import (
    embed, l2_normalize, cosine, guard, clean_slot, state_concat, build_vectors,
)
from embedding_config import PREFIX, FULL_DIM, SHORT_DIM, EPS, MODEL_TAG


def _norm(v):
    return math.sqrt(sum(x * x for x in v))


def test_embed_dims():
    full, short = embed("地方の農家を都市とつなぎたい", "symmetric")
    assert len(full) == FULL_DIM == 1024
    assert len(short) == SHORT_DIM == 256


def test_embed_normalized():
    full, short = embed("何かしらのテキスト", "passage")
    assert abs(_norm(full) - 1.0) < 1e-6, "full は L2 正規化されていること"
    assert abs(_norm(short) - 1.0) < 1e-6, "short も再正規化されていること（C-1）"


def test_embed_deterministic():
    a1, s1 = embed("同じ入力", "query")
    a2, s2 = embed("同じ入力", "query")
    assert a1 == a2 and s1 == s2, "同一 text+role は同一ベクトル"


def test_short_is_prefix_of_full_direction():
    """short は full の先頭 SHORT_DIM を再正規化したもの（MRL / C-1）。"""
    full, short = embed("テキスト", "symmetric")
    head = l2_normalize(full[:SHORT_DIM])
    assert short == head


def test_role_changes_vector():
    """prefix 打ち分けで role が違えばベクトルが変わる（query には instruction）。"""
    q, _ = embed("text", "query")
    p, _ = embed("text", "passage")
    assert q != p, "query と passage は別 prefix なので別ベクトル"


def test_prefix_externalized():
    """prefix は embedding_config に外出しされ、query に英語 instruction を持つ。"""
    assert set(PREFIX) == {"symmetric", "query", "passage"}
    assert PREFIX["symmetric"] == ""
    assert PREFIX["passage"] == ""
    assert "Instruct:" in PREFIX["query"] and "Query:" in PREFIX["query"]


def test_unknown_role_raises():
    try:
        embed("x", "bogus")
        assert False, "未知 role は ValueError"
    except ValueError:
        pass


def test_guard_boundaries():
    """guard: cos=1→1, cos=-1→EPS(>0), cos=0→0.5, 範囲外はクリップ（C-3）。"""
    assert abs(guard(1.0) - 1.0) < 1e-12
    assert guard(-1.0) == EPS, "cos=-1 でも 0 を返さない（log/負冪で壊れない）"
    assert abs(guard(0.0) - 0.5) < 1e-12
    assert guard(2.0) == 1.0, "範囲外を [-1,1] にクリップ"
    assert guard(-5.0) == EPS
    assert guard(-1.0) > 0.0, "下限は厳密に正"


def test_clean_slot():
    assert clean_slot("未取得") is None
    assert clean_slot("") is None
    assert clean_slot(None) is None
    assert clean_slot("持っている") == "持っている"


def test_state_concat_excludes_unfetched():
    """"未取得"・空スロットを連結から除外（#11）。"""
    profile = {
        "state_have": "農業の現場知識",
        "state_can_type": "未取得",
        "state_bound": "",
        "state_unsorted": "都市と地方の往復経験",
    }
    out = state_concat(profile)
    assert "農業の現場知識" in out
    assert "都市と地方の往復経験" in out
    assert "未取得" not in out
    assert "動き方の型" not in out, "未取得スロットのラベルも出さない"


def test_build_vectors_keys_and_dims():
    """build_vectors が 8 ベクトル + model_tag を返し、次元が正しいこと（C-2）。"""
    profile = {
        "will_text": "農家と飲食店を直接つなぎたい",
        "state_have": "販路の理解",
        "state_can_type": "現場に入り込む型",
        "state_bound": "実装技術がない",
        "state_unsorted": "",
    }
    v = build_vectors(profile, necessity_text="アプリを実装できるエンジニア")
    assert v["model_tag"] == MODEL_TAG
    for full_key in ("will_symmetric", "will_passage", "state_passage", "necessity_query"):
        assert len(v[full_key]) == FULL_DIM
    for short_key in ("will_sym_256", "will_pas_256", "state_pas_256", "necessity_q_256"):
        assert len(v[short_key]) == SHORT_DIM


def test_will_symmetric_and_passage_role_routing():
    """
    意志は対称用(symmetric)と passage 用で別々に role ルーティングされ、別列に保存される。
    注: H-1 初期値では PREFIX['symmetric']==PREFIX['passage']=='' のため両者は現状一致する。
    H-1 で prefix を差別化した時点で別列のまま自動的に分離する（B-2 の混同禁止を担保）。
    ここでは「正しい role でエンコードされ別キーに格納される」ことを検証する。
    """
    profile = {"will_text": "ある意志", "state_have": "x"}
    v = build_vectors(profile, "必要像")
    will = "ある意志"
    assert v["will_symmetric"] == embed(will, "symmetric")[0]
    assert v["will_passage"] == embed(will, "passage")[0]
    assert "will_symmetric" in v and "will_passage" in v, "別列に保存される"


def test_build_vectors_defensive_redaction():
    """raw に PII があっても embedding 入力は redact される（I章: raw 直渡し禁止）。"""
    from embedding_service import embed as _embed
    raw_profile = {"will_text": "連絡は alice@example.com まで"}
    v = build_vectors(raw_profile, "必要像")
    # redact 後テキスト "連絡は [EMAIL] まで" の symmetric ベクトルと一致するはず
    expected_full, _ = _embed("連絡は [EMAIL] まで", "symmetric")
    assert v["will_symmetric"] == expected_full, "will は redact してからエンコードされる"


def test_cosine_self_is_one():
    full, _ = embed("自己一致", "symmetric")
    assert abs(cosine(full, full) - 1.0) < 1e-9


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nStep 3 DoD テスト: {len(tests)} 件 全 PASS")
