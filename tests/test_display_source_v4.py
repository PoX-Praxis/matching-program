"""指示書18: 登録経路の不整合と churn 判定の是正。

Postgres 専用の profiles_v4 実テーブルを使わずに検証できる部分を固定する:
  - 作業B: profiles_v4 行 → build_profile_view が期待する seeker 形への写像
    （_seeker_from_v4_row）と、そこから v4 profile_view が正しく組めること。
  - 作業A/§2-3: 表示用項目（背景等）だけを変えても profile_content_hash が変わること
    ＝台帳とスナップショットが「同一の計算範囲」で連動し、片方だけスキップされない。
  - get_profile_view は SQLite（profiles_v4 なし）では v3 フォールバックすること。
  - 作業C: POST /seekers は閉鎖（410）で、下書き→確定が新規登録の入口。
  - 作業D: view_overrides は content_hash の範囲外（本人より編集は台帳・snapshot に残らない）。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import db as dbmod
from profile_view import build_profile_view
from subject_ledger import profile_content_hash
import app as appmod
from db_v4 import MemoryStore, receive_profile_v4, GEN_READY


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


# ── 作業B: profiles_v4 行 → profile_view ────────────────────────────────────
def _v4_row():
    # (will_text, state_have, state_can_type, state_bound, state_unsorted, supporting_raw)
    return (
        "農家と店をつなぎたい", "現場の知識", "翻訳する動き", "技術がない", "",
        {"背景": "10年畑にいた", "一行紹介": "つなぎ手", "意志_どこへ": "地域の流通",
         "意志_なぜ": "分断が惜しい", "経験": "市場で働いた", "要約文": "現場を翻訳する人",
         "求めている": "実装できる人", "系列素材": [], "attention候補": []},
    )


def test_seeker_from_v4_row_builds_v4_view():
    seeker = dbmod._seeker_from_v4_row(_v4_row())
    pv = build_profile_view(seeker)
    assert pv["schema_version"] == "v4"
    assert pv["pursuing"] == "農家と店をつなぎたい"
    assert pv["state_have"] == "現場の知識"
    assert pv["state_can_type"] == "翻訳する動き"
    assert pv["state_bound"] == "技術がない"
    assert pv["seeking"] == "実装できる人"          # supporting_raw の求めている
    assert pv["background"] == "10年畑にいた"        # 表示用6項目
    assert pv["will_where"] == "地域の流通"
    assert pv["will_why"] == "分断が惜しい"
    assert pv["will_origin"] == "市場で働いた"
    assert pv["one_liner"] == "つなぎ手"
    assert pv["headline"] == "現場を翻訳する人"


def test_seeker_from_v4_row_accepts_json_string_supporting():
    # Postgres ドライバによっては JSONB が str で来る。その場合もデコードできる。
    import json
    row = list(_v4_row())
    row[5] = json.dumps(row[5], ensure_ascii=False)
    seeker = dbmod._seeker_from_v4_row(tuple(row))
    pv = build_profile_view(seeker)
    assert pv["seeking"] == "実装できる人" and pv["background"] == "10年畑にいた"


# ── 作業A/§2-3: 表示用項目だけの変更でも content_hash が変わる ──────────────────
def _profile_input(**over):
    base = {
        "will_text": "つなぎたい",
        "state_have": "知識", "state_can_type": "型", "state_bound": "縛り",
        "state_unsorted": "",
        "supporting_raw": {"背景": "b1", "一行紹介": "o1", "意志_どこへ": "w1",
                           "意志_なぜ": "y1", "経験": "e1", "要約文": "s1",
                           "求めている": "x", "生テキスト": ["r1"]},
    }
    base.update(over)
    return base


def test_display_only_change_changes_content_hash():
    """背景（表示用）だけ変えても content_hash が変わる → 台帳もスナップショットも記録される
    （src_input_hash 基準だと表示用項目が範囲外でスキップされ、不整合が起きていた）。"""
    a = _profile_input()
    b = _profile_input(supporting_raw={**a["supporting_raw"], "背景": "b2"})
    assert profile_content_hash(a) != profile_content_hash(b)


def test_raw_text_change_does_not_change_content_hash():
    """生テキスト(raw)は content_hash の範囲外（宣言ではない）。変えても同一。"""
    a = _profile_input()
    b = _profile_input(supporting_raw={**a["supporting_raw"], "生テキスト": ["r2", "r3"]})
    assert profile_content_hash(a) == profile_content_hash(b)


def test_identical_input_same_content_hash():
    assert profile_content_hash(_profile_input()) == profile_content_hash(_profile_input())


# ── 作業D: view_overrides / free_text は content_hash の範囲外 ─────────────────
def test_view_overrides_not_in_content_hash_range():
    """本人より（free_text/view_overrides）は profile_input に含まれず、content_hash に
    影響しない（＝本人より編集は台帳・スナップショットに残らない・仕様 v2 §6-2 未決）。"""
    base = _profile_input()
    # profile_input に free_text を足しても profile_content_hash は base のキーしか見ない
    with_free = {**base, "free_text": "本人からのメッセージ"}
    assert profile_content_hash(base) == profile_content_hash(with_free)


# ── get_profile_view: SQLite では v3 フォールバック（profiles_v4 は Postgres 専用）──
def test_get_profile_view_v3_fallback_on_sqlite():
    db = _db()
    seeker = {"_meta": {"schema_version": "v4"}, "意志": "作りたい",
              "現状": {"持っているもの": "時間"},
              "supporting_material": {"求めている": "仲間"}}
    dbmod.save_profile("u_a", seeker, db_path=db)
    pv = dbmod.get_profile_view("u_a", db_path=db)
    assert pv is not None and pv["pursuing"] == "作りたい"
    assert dbmod.get_profile_view("u_missing", db_path=db) is None


# ── 意志の編集が表示（いま目指していること）に反映される（指示書18 追補）──────────
def test_will_edit_surfaces_in_display():
    """①由来の 意志_どこへ があると will_text だけ編集しても表示が変わらない不具合の是正。
    _edit_core_v4 が意志編集を will_text と 意志_どこへ の両方へ通す → build_profile_view の
    will_where（＝画面の「いま目指していること」）が編集後の値になる。"""
    store = MemoryStore()
    pin = {"will_text": "古い意志", "state_have": "h", "state_can_type": "",
           "state_bound": "", "state_unsorted": "",
           "supporting_raw": {"意志_どこへ": "①が作った意志_どこへ", "意志_なぜ": "なぜ",
                              "経験": "経験", "求めている": "x"}}
    receive_profile_v4(store, "u1", pin, None, generation_status=GEN_READY)

    orig = (appmod.is_postgres, appmod._v4_store, appmod._spawn_v4_job)
    appmod.is_postgres = lambda: True
    appmod._v4_store = lambda: store
    appmod._spawn_v4_job = lambda *a, **k: None
    try:
        assert appmod._edit_core_v4("u1", {"意志": "新しい意志"}) is True
    finally:
        appmod.is_postgres, appmod._v4_store, appmod._spawn_v4_job = orig

    prof = store.get_profile("u1")
    pv = build_profile_view(dbmod._seeker_from_v4_row((
        prof["will_text"], prof["state_have"], prof["state_can_type"],
        prof["state_bound"], prof["state_unsorted"], prof["supporting_raw"])))
    assert pv["will_where"] == "新しい意志"          # 画面「いま目指していること」に反映
    assert pv["will_why"] == "なぜ" and pv["will_origin"] == "経験"   # なぜ/経験 は①のまま


# ── 作業C: /seekers は閉鎖（410）──────────────────────────────────────────────
def test_seekers_post_closed():
    appmod.DB = _db()
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.post("/seekers", json={"意志": "x", "現状": {}})
    assert r.status_code == 410
    assert r.get_json().get("moved_to") == "/register"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n指示書18 表示源/churn テスト: {len(tests)} 件 全 PASS")
