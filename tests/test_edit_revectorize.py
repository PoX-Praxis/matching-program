"""
指示書08 フェーズ2/3: 編集による v4 再ベクトル化（MemoryStore・非同期を同期化）。

完了条件の中核を検証:
- profiles_v4.will_text が更新される
- profile_vectors が再計算される（値が変わる）
- generation_status が needs_regeneration になる
- derived_necessity（必要像本文・数値）は変化しない
- 編集経路でサーバー必要像生成が呼ばれない（is_fallback=False）
- v4 未登録 / 必要像未保存 なら何もしない
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as appmod
import db_v4
from db_v4 import (MemoryStore, MODEL_TAG, receive_profile_v4,
                   vectorize_profile_v4, GEN_READY)
from necessity_gen import build_user_necessity


def _seed(store):
    pin = {"will_text": "old will text", "state_have": "h", "state_can_type": "",
           "state_bound": "", "state_unsorted": "", "supporting_raw": {}}
    nec = build_user_necessity(
        {**pin, "supporting_redacted": {}},
        {"necessity_text": "必要像テキスト", "gate_s": 0.6, "gate_u": 0.3,
         "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0, "evidence_span": "", "generator": "AIname"})
    receive_profile_v4(store, "u1", pin, nec, generation_status=GEN_READY)
    vectorize_profile_v4(store, "u1", pin, nec["necessity_text"])
    return nec


def _run(store, edited_seeker):
    orig = (appmod.is_postgres, appmod._v4_store, appmod.get_seeker, appmod._spawn_v4_job)
    appmod.is_postgres = lambda: True
    appmod._v4_store = lambda: store
    appmod.get_seeker = lambda pid, db_path=None: edited_seeker
    appmod._spawn_v4_job = lambda *a, **k: appmod._v4_async_job(*a, **k)  # 同期実行
    try:
        appmod._revectorize_after_edit("u1")
    finally:
        appmod.is_postgres, appmod._v4_store, appmod.get_seeker, appmod._spawn_v4_job = orig


def test_revectorize_updates_vectors_and_flags_regen():
    store = MemoryStore()
    _seed(store)
    old_vec = list(store.get_bundle("u1", MODEL_TAG)["vectors"]["will_symmetric"])
    old_nec = store.get_necessity("u1", MODEL_TAG)

    edited = {"意志": "BRAND NEW will content", "現状": {
        "持っているもの": "h", "できること_型": "", "縛られているもの": "", "未分類": ""}}
    _run(store, edited)

    assert store.get_profile("u1")["will_text"] == "BRAND NEW will content"          # profiles_v4 更新
    new_vec = store.get_bundle("u1", MODEL_TAG)["vectors"]["will_symmetric"]
    assert list(new_vec) != old_vec                                                    # ベクトル再計算
    assert store.get_profile_status("u1")["generation_status"] == "needs_regeneration"  # 鮮度フラグ
    n = store.get_necessity("u1", MODEL_TAG)
    assert n["necessity_text"] == old_nec["necessity_text"]                             # 必要像本文 不変
    assert n["gate_s"] == old_nec["gate_s"] and n["gamma"] == old_nec["gamma"]          # 数値 不変


def test_no_server_necessity_generation_on_edit():
    store = MemoryStore(); _seed(store)
    called = {"gen": False}
    orig = db_v4.generate_necessity_v4
    db_v4.generate_necessity_v4 = lambda *a, **k: (called.__setitem__("gen", True), orig(*a, **k))[1]
    try:
        _run(store, {"意志": "changed will here", "現状": {"持っているもの": "h"}})
    finally:
        db_v4.generate_necessity_v4 = orig
    assert called["gen"] is False   # is_fallback=False ＝ サーバー②生成に入らない


def test_revectorize_skips_when_no_v4_profile():
    store = MemoryStore()   # u1 は v4 未登録
    _run(store, {"意志": "x", "現状": {}})
    assert store.get_profile("u1") is None   # 何も起きない（v3編集のみ）


def test_revectorize_skips_when_no_necessity():
    store = MemoryStore()
    # profile はあるが necessity 未保存 → 再ベクトル化しない（②生成しない方針）
    pin = {"will_text": "w", "state_have": "h", "state_can_type": "",
           "state_bound": "", "state_unsorted": "", "supporting_raw": {}}
    receive_profile_v4(store, "u1", pin, None, generation_status=GEN_READY)
    _run(store, {"意志": "new", "現状": {"持っているもの": "h"}})
    assert store.get_bundle("u1", MODEL_TAG) is None  # ベクトルは作られない


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n再ベクトル化テスト: {len(tests)} 件 全 PASS")
