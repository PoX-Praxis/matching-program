"""
Step 7 DoD 検証テスト — v3.1 → v4 遅延移行（F-5）

DoD:
  - map_v31_to_v4: 4軸→二軸写像（意志→will, 能力→state_have, 求めている→supporting）
  - フェーズ軸は破棄される
  - v3.1 に無い素材は "未取得"（捏造しない）
  - migrate_seeker: migrated_from='v3.1' を記録し v4 取り込みを通す
  - ensure_migrated: 冪等（既存なら何もしない）/ 不在 seeker は移行しない / 遅延実行
  - 非破壊: v4 ストアにのみ書き、元 seeker は読むだけ
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from migrate_v4 import map_v31_to_v4, migrate_seeker, ensure_migrated, MIGRATED_FROM
from db_v4 import MemoryStore, match_v4
from embedding_config import MODEL_TAG, FULL_DIM


SEEKER_V31 = {
    "id": "user_sample_01",
    "意志": "止まっている接続を動かし直したい",
    "求めている": "実証パートナー",
    "能力": "概念を構造へ翻訳する力",
    "フェーズ": "mvp",
    "生テキスト": "繋がれるはずの人が繋がれない状況を見てきた",
    "supporting_material": {
        "要約文": "接続の停止を構造化で解決するビルダー",
        "attention候補": ["設計哲学", "双方向翻訳"],
        "系列素材": ["目標X→次に必要Y"],
    },
}


# ── map_v31_to_v4 ─────────────────────────────────────────────────────────────
def test_map_will_from_ishi():
    out = map_v31_to_v4(SEEKER_V31)
    assert out["will_text"] == "止まっている接続を動かし直したい"


def test_map_state_have_from_nouryoku():
    out = map_v31_to_v4(SEEKER_V31)
    assert out["state_have"] == "概念を構造へ翻訳する力"


def test_map_want_into_supporting():
    out = map_v31_to_v4(SEEKER_V31)
    assert out["supporting_raw"]["求めている"] == "実証パートナー"


def test_map_drops_phase():
    """フェーズ軸は v4 に持ち越さない（破棄）。"""
    out = map_v31_to_v4(SEEKER_V31)
    assert "フェーズ" not in out
    assert "phase" not in out
    # state スロットにフェーズ値が紛れ込まない
    assert "mvp" not in (out["state_have"] + out["state_can_type"] +
                         out["state_bound"] + out["state_unsorted"])


def test_map_raw_text_listified():
    out = map_v31_to_v4(SEEKER_V31)
    assert out["supporting_raw"]["生テキスト"] == ["繋がれるはずの人が繋がれない状況を見てきた"]


def test_map_summary_and_series_carried():
    out = map_v31_to_v4(SEEKER_V31)
    assert out["supporting_raw"]["要約文"] == "接続の停止を構造化で解決するビルダー"
    assert out["supporting_raw"]["系列素材"] == ["目標X→次に必要Y"]


def test_map_missing_materials_marked_unfetched():
    """v3.1 に無い素材は "未取得"（捏造しない / D-3）。"""
    out = map_v31_to_v4(SEEKER_V31)
    for k in ("意志要求の素材", "連言選言の素材", "チャネル重み素材"):
        assert out["supporting_raw"][k] == "未取得"
    # v3.1 に無い現状スロットは空（未取得相当）
    assert out["state_can_type"] == "" and out["state_bound"] == ""


def test_map_preserves_v31_specific_material():
    """attention候補 など v3.1 固有素材は温存（非破壊）。"""
    out = map_v31_to_v4(SEEKER_V31)
    assert out["supporting_raw"]["attention候補"] == ["設計哲学", "双方向翻訳"]


def test_map_handles_sparse_seeker():
    """欠損だらけの seeker でも例外を出さず "未取得"/"" で埋める。"""
    out = map_v31_to_v4({"意志": "やりたい"})
    assert out["will_text"] == "やりたい"
    assert out["state_have"] == ""
    assert out["supporting_raw"]["求めている"] == "未取得"
    assert out["supporting_raw"]["生テキスト"] == "未取得"


def test_map_rejects_non_dict():
    try:
        map_v31_to_v4("not a dict")
        assert False
    except ValueError:
        pass


# ── migrate_seeker ────────────────────────────────────────────────────────────
def test_migrate_records_migrated_from():
    store = MemoryStore()
    migrate_seeker(store, "u1", SEEKER_V31)
    assert store.profiles["u1"]["migrated_from"] == MIGRATED_FROM == "v3.1"


def test_migrate_produces_vectors_and_necessity():
    store = MemoryStore()
    migrate_seeker(store, "u1", SEEKER_V31)
    v = store.vectors[("u1", MODEL_TAG)]
    assert len(v["will_symmetric"]) == FULL_DIM
    nec = store.get_necessity("u1", MODEL_TAG)
    # 求めている申告（実証パートナー）が demo の必要像に反映される
    assert nec["necessity_text"] == "実証パートナー"


def test_migrated_seeker_is_matchable():
    """移行済み seeker は v4 照合にそのまま乗る。"""
    store = MemoryStore()
    migrate_seeker(store, "seeker", SEEKER_V31)
    migrate_seeker(store, "cand", dict(SEEKER_V31, id="c", 意志="別の意志"))
    out = match_v4(store, "seeker")
    assert any(r["candidate_id"] == "cand" for r in out["results"])


# ── ensure_migrated（遅延・冪等）──────────────────────────────────────────────
def test_ensure_migrates_when_absent():
    store = MemoryStore()
    loaded = {}
    def loader(uid):
        loaded["called"] = uid
        return SEEKER_V31
    res = ensure_migrated(store, "u1", loader)
    assert res is not None
    assert loaded["called"] == "u1"
    assert store.has_bundle("u1", MODEL_TAG)


def test_ensure_idempotent_when_present():
    """既に v4 にあるなら loader を呼ばず再取り込みしない（冪等・遅延の肝）。"""
    store = MemoryStore()
    migrate_seeker(store, "u1", SEEKER_V31)
    calls = []
    def loader(uid):
        calls.append(uid)
        return SEEKER_V31
    res = ensure_migrated(store, "u1", loader)
    assert res is None
    assert calls == [], "既存ならローダーを呼ばない"


def test_ensure_skips_when_seeker_missing():
    """v3.1 にも居ない user は移行しない（None）。"""
    store = MemoryStore()
    res = ensure_migrated(store, "ghost", lambda uid: None)
    assert res is None
    assert not store.has_bundle("ghost", MODEL_TAG)


def test_ensure_lazy_not_eager():
    """ensure は対象 1 件だけ移行し、他の v3.1 ユーザーには触れない（遅延）。"""
    store = MemoryStore()
    pool = {"a": dict(SEEKER_V31, 意志="A"), "b": dict(SEEKER_V31, 意志="B")}
    ensure_migrated(store, "a", lambda uid: pool.get(uid))
    assert store.has_bundle("a", MODEL_TAG)
    assert not store.has_bundle("b", MODEL_TAG), "他ユーザーは前もって移行しない"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nStep 7 DoD テスト: {len(tests)} 件 全 PASS")
