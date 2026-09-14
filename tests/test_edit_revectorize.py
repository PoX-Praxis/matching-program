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


def _fields(will, jotai):
    """/core ルートが body から組む fields 形（意志 + state_*）を作る。"""
    m = {"持っているもの": "state_have", "できること_型": "state_can_type",
         "縛られているもの": "state_bound", "未分類": "state_unsorted"}
    f = {"意志": will}
    for jp, eng in m.items():
        if jp in (jotai or {}):
            f[eng] = jotai[jp]
    return f


def _run(store, will, jotai):
    # 指示書18 作業C: 編集は profiles_v4 に直接反映（_edit_core_v4）。get_seeker には依存しない。
    orig = (appmod.is_postgres, appmod._v4_store, appmod._spawn_v4_job)
    appmod.is_postgres = lambda: True
    appmod._v4_store = lambda: store
    appmod._spawn_v4_job = lambda *a, **k: appmod._v4_async_job(*a, **k)  # 同期実行
    try:
        return appmod._edit_core_v4("u1", _fields(will, jotai))
    finally:
        appmod.is_postgres, appmod._v4_store, appmod._spawn_v4_job = orig


def test_revectorize_updates_vectors_and_sets_ready():
    # 指示書28 §6-2: needs_regeneration は廃止。編集の再ベクトル化は ready にする。
    store = MemoryStore()
    _seed(store)
    old_vec = list(store.get_bundle("u1", MODEL_TAG)["vectors"]["will_symmetric"])
    old_nec = store.get_necessity("u1", MODEL_TAG)

    _run(store, "BRAND NEW will content", {
        "持っているもの": "h", "できること_型": "", "縛られているもの": "", "未分類": ""})

    assert store.get_profile("u1")["will_text"] == "BRAND NEW will content"          # profiles_v4 更新
    new_vec = store.get_bundle("u1", MODEL_TAG)["vectors"]["will_symmetric"]
    assert list(new_vec) != old_vec                                                    # ベクトル再計算
    assert store.get_profile_status("u1")["generation_status"] == "ready"             # 廃止: needs_regeneration ではなく ready
    n = store.get_necessity("u1", MODEL_TAG)
    assert n["necessity_text"] == old_nec["necessity_text"]                             # 必要像本文 不変
    assert n["gate_s"] == old_nec["gate_s"] and n["gamma"] == old_nec["gamma"]          # 数値 不変


def test_no_server_necessity_generation_on_edit():
    store = MemoryStore(); _seed(store)
    called = {"gen": False}
    orig = db_v4.generate_necessity_v4
    db_v4.generate_necessity_v4 = lambda *a, **k: (called.__setitem__("gen", True), orig(*a, **k))[1]
    try:
        _run(store, "changed will here", {"持っているもの": "h"})
    finally:
        db_v4.generate_necessity_v4 = orig
    assert called["gen"] is False   # is_fallback=False ＝ サーバー②生成に入らない


def test_revectorize_skips_when_no_v4_profile():
    store = MemoryStore()   # u1 は v4 未登録
    assert _run(store, "x", {}) is False       # 何もしない（v3編集のみ・表示は v3 フォールバック）
    assert store.get_profile("u1") is None


def test_edit_updates_v4_display_even_without_necessity():
    # 指示書18 作業C: 必要像が無くても profiles_v4 の will/state は更新する（表示反映）。
    # ただしベクトルは作らない（②生成しない方針）。
    store = MemoryStore()
    pin = {"will_text": "w", "state_have": "h", "state_can_type": "",
           "state_bound": "", "state_unsorted": "", "supporting_raw": {}}
    receive_profile_v4(store, "u1", pin, None, generation_status=GEN_READY)
    assert _run(store, "new will", {"持っているもの": "h"}) is True
    assert store.get_profile("u1")["will_text"] == "new will"   # 表示元は更新される
    assert store.get_bundle("u1", MODEL_TAG) is None            # ベクトルは作られない


def test_edit_noop_when_unchanged():
    # 意志・現状が変わっていなければ False（churn 防止）。
    store = MemoryStore(); _seed(store)
    assert _run(store, "old will text", {"持っているもの": "h", "できること_型": "",
                                         "縛られているもの": "", "未分類": ""}) is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n再ベクトル化テスト: {len(tests)} 件 全 PASS")
