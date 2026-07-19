"""
dual-write 検証テスト — /seekers(v3) 登録時に v4(Nomic) 取り込みも起動する経路

app._dual_write_v4 / _ingest_v4_from_flat を、MemoryStore とスタブ spawn で検証する
（Postgres・スレッド・Nomic を使わず同期的に受付部分だけ確認）。
"""
import sys, os
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
from db_v4 import MemoryStore, MODEL_TAG


def _nested_v42():
    return {
        "id": "alice_2025",
        "schema_version": "v4",
        "seeker": {
            "意志": "農家と店をつなぎたい",
            "現状": {"持っているもの": "現場の知識", "できること_型": "翻訳する動き",
                     "縛られているもの": "技術がない", "未分類": ""},
        },
        "supporting_material": {"求めている": "実装できる人"},
        "necessity": {
            "necessity_text": "現場を実装に翻訳できる開発者", "gate_s": 0.6, "gate_u": 0.3,
            "p_sharpness": -0.4, "alpha": 1.0, "beta": 2.0,
            "evidence_span": "一緒に背負える", "generator": "Claude Opus 4.8",
        },
    }


def _patch(store, spawned):
    orig = (appmod.is_postgres, appmod._v4_store, appmod._spawn_v4_job)
    appmod.is_postgres = lambda: True
    appmod._v4_store = lambda: store
    appmod._spawn_v4_job = lambda *a, **k: spawned.append((a, k))
    return orig


def _restore(orig):
    appmod.is_postgres, appmod._v4_store, appmod._spawn_v4_job = orig


# ── v4.2 ネストJSON → v4 取り込みが起動する ───────────────────────────────────
def test_dual_write_ingests_v42_json():
    store, spawned = MemoryStore(), []
    orig = _patch(store, spawned)
    try:
        ok = appmod._dual_write_v4("u_kaoru", _nested_v42())
        assert ok is True
        # profile と necessity が保存され、非同期ジョブが起動された
        assert store.get_profile("u_kaoru") is not None
        nec = store.get_necessity("u_kaoru", MODEL_TAG)
        assert nec and nec["necessity_text"] == "現場を実装に翻訳できる開発者"
        assert nec["generator_name"] == "Claude Opus 4.8"
        assert len(spawned) == 1  # ベクトル化ジョブ spawn
    finally:
        _restore(orig)


def test_dual_write_uses_v3_userid_not_handle():
    """v4 の profile_id は v3 の user_id（JSONの id 'alice_2025' ではない）で揃える。"""
    store, spawned = MemoryStore(), []
    orig = _patch(store, spawned)
    try:
        appmod._dual_write_v4("u_internal", _nested_v42())
        assert store.get_profile("u_internal") is not None
        assert store.get_profile("alice_2025") is None
    finally:
        _restore(orig)


# ── v4 形でない（旧 v3.1 等）→ v4 化しない（v3 のみ）─────────────────────────
def test_dual_write_skips_legacy_v31():
    store, spawned = MemoryStore(), []
    orig = _patch(store, spawned)
    try:
        legacy = {"意志": "やりたい", "求めている": "x", "能力": "y", "フェーズ": "mvp"}
        ok = appmod._dual_write_v4("u_leg", legacy)
        assert ok is False
        assert store.get_profile("u_leg") is None
        assert spawned == []
    finally:
        _restore(orig)


def test_dual_write_skips_when_will_empty():
    store, spawned = MemoryStore(), []
    orig = _patch(store, spawned)
    try:
        empty_will = {"seeker": {"意志": "", "現状": {}}, "necessity": {}}
        assert appmod._dual_write_v4("u_x", empty_will) is False
        assert store.get_profile("u_x") is None
    finally:
        _restore(orig)


# ── Postgres でない環境では何もしない ────────────────────────────────────────
def test_dual_write_noop_without_postgres():
    store, spawned = MemoryStore(), []
    orig = _patch(store, spawned)
    appmod.is_postgres = lambda: False  # 上書き
    try:
        assert appmod._dual_write_v4("u", _nested_v42()) is False
        assert spawned == []
    finally:
        _restore(orig)


# ── necessity 無しの v4 形 → フォールバック経路で spawn（is_fallback=True）──────
def test_dual_write_v4_without_necessity_falls_back():
    store, spawned = MemoryStore(), []
    orig = _patch(store, spawned)
    try:
        no_nec = {"seeker": {"意志": "つなぎたい", "現状": {"持っているもの": "知識"}},
                  "supporting_material": {}}
        ok = appmod._dual_write_v4("u_fb", no_nec)
        assert ok is True
        assert store.get_profile("u_fb") is not None
        # necessity は未保存（サーバー生成は非同期ジョブ側）
        assert store.get_necessity("u_fb", MODEL_TAG) is None
        assert spawned and spawned[0][1].get("is_fallback") is True
    finally:
        _restore(orig)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\ndual-write テスト: {len(tests)} 件 全 PASS")
