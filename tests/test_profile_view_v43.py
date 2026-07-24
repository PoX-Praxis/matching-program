"""
v4.3 表示再構成の変換層テスト（指示書06）。

要点:
- build_profile_view が v4.3 表示キー（background/will_where/will_why/will_origin/one_liner）を出す。
- 新キーが無い既存プロフィールはフォールバック（background←headline, will_where←pursuing, 他←空）。
- _clean_supporting_material_v4 は5新キーを空文字既定で追加、既存7キーの扱いは不変。
- apply_overrides が free_text をパススルー（表示専用）。
- 【最重要】新しい表示キーを supporting に足しても compute_src_hash は不変
  → 必要像・ベクトルの再生成（needs_regeneration）が走らない。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from profile_view import (build_profile_view, apply_overrides,
                          _clean_supporting_material_v4)
from necessity_gen import compute_src_hash


def _v4_seeker(sm_extra=None):
    sm = {"要約文": "要約です", "求めている": "実装者"}
    if sm_extra:
        sm.update(sm_extra)
    return {
        "_meta": {"schema_version": "v4"},
        "意志": "農家と店をつなぐ",
        "現状": {"持っているもの": "知識", "できること_型": "翻訳",
                 "縛られているもの": "技術不足", "未分類": ""},
        "supporting_material": sm,
    }


# ── build_profile_view: 新キーあり ────────────────────────────────────────────
def test_v43_keys_emitted_when_present():
    pv = build_profile_view(_v4_seeker({
        "背景": "元エンジニア", "意志_どこへ": "流通を変える",
        "意志_なぜ": "分断が嫌", "経験": "市場で働いた", "一行紹介": "つなぐ人"}))
    assert pv["background"] == "元エンジニア"
    assert pv["will_where"] == "流通を変える"
    assert pv["will_why"] == "分断が嫌"
    assert pv["will_origin"] == "市場で働いた"
    assert pv["one_liner"] == "つなぐ人"


# ── build_profile_view: 新キーなし → フォールバック ──────────────────────────
def test_v43_fallbacks_when_absent():
    pv = build_profile_view(_v4_seeker())
    assert pv["background"] == pv["headline"]      # 背景←要約文(headline)
    assert pv["will_where"] == pv["pursuing"]      # いま目指す←意志
    assert pv["will_why"] == ""                    # ブロックごと非表示
    assert pv["will_origin"] == ""
    assert pv["one_liner"] == ""


# ── _clean_supporting_material_v4: 5新キー空文字既定・既存7キー不変 ───────────
def test_clean_v4_adds_new_keys_empty_default():
    c = _clean_supporting_material_v4({"要約文": "a"})
    for k in ("背景", "意志_なぜ", "意志_どこへ", "経験", "一行紹介"):
        assert c[k] == ""                       # "未取得" ではなく空文字
    assert c["求めている"] == "未取得"          # 既存素材は従来どおり
    assert c["生テキスト"] == [] and c["系列素材"] == []


# ── apply_overrides: free_text パススルー（表示専用）─────────────────────────
def test_free_text_passthrough():
    assert apply_overrides({"schema_version": "v4"}, {"free_text": "  本人より  "})["free_text"] == "本人より"
    assert "free_text" not in apply_overrides({"schema_version": "v4"}, {"headline": "h"})


# ── 【最重要】新表示キーは src_input_hash を変えない（再生成が走らない）──────
def test_new_display_keys_do_not_change_src_hash():
    base = {
        "will_text": "つなぐ", "state_have": "知識", "state_can_type": "翻訳",
        "state_bound": "技術不足", "state_unsorted": "",
        "supporting_redacted": {"生テキスト": ["x"], "要約文": "y", "求めている": "実装者",
                                "意志要求の素材": "未取得", "連言選言の素材": "未取得",
                                "チャネル重み素材": "未取得", "系列素材": []},
    }
    h1 = compute_src_hash(base)
    with_new = {**base, "supporting_redacted": {
        **base["supporting_redacted"],
        "背景": "元エンジニア", "意志_なぜ": "分断が嫌", "意志_どこへ": "流通を変える",
        "経験": "市場で働いた", "一行紹介": "つなぐ人"}}
    h2 = compute_src_hash(with_new)
    assert h1 == h2, "新しい表示キーで src_input_hash が変わってはいけない（一斉再生成の防止）"


# ── v3 は不変 ────────────────────────────────────────────────────────────────
def test_v3_layout_unchanged():
    v3 = {"_meta": {"schema_version": "v3"}, "意志": "w", "求めている": "n",
          "能力": "o", "フェーズ": "mvp"}
    pv = build_profile_view(v3)
    assert pv["schema_version"] == "v3"
    assert pv["needs"] == "n" and pv["offering"] == "o"
    assert "background" not in pv  # v3 には v4.3 キーを足さない


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nv4.3 変換層テスト: {len(tests)} 件 全 PASS")
