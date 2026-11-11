"""指示書71 — 接続の説明・画面の調整（仕様 v0.5 の追記）。テスト 444〜452（§9 の 1〜9。10 は既存のテスト全体）。

444 要旨が目的ごとの状態どおり（すべて explained／一部／explained が無い）。出さない目的・逆の向きは入れない
445 状態の 1 行が §2 の表どおり。数値・割合の文字が出ない
446 根拠と確かめられることが <details> の中。画面1・2 では閉じ、画面3 では開いている
447 ⑥ の枠は畳まない（見出し・①・状態の 1 行が最初から見える）
448 古い文（「文からは分からないこと」「見つかりません」）が出ない。missing_must は確かめられることの節に入り、先に並ぶ
449 補足の案内に新しい 1 文（画面1・3）
450 注記が新しい文
451 保存済みの表示（画面2 で保存した分）は描き直されずにそのまま出る
452 画面3 の本文に「あなた」が残らない・呼び方の置き換えが要旨と状態の 1 行にも効く
"""
import copy
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import explanation as X  # noqa: E402
import explanation_store as S  # noqa: E402
from test_t61_resonance_gate import _cli  # noqa: E402
from test_t68_screens import _card, _offer, xs  # noqa: E402,F401
from test_t65b2_two_layer import world2  # noqa: E402,F401
from test_t61_resonance_gate import db  # noqa: E402,F401

FIX = os.path.join(ROOT, "tests", "fixtures", "explanation_v0_4.json")
NAMES = {"viewer": "アオイ", "target": "ハル"}


def _case(name):
    with open(FIX, encoding="utf-8") as f:
        fx = json.load(f)
    c = next(x for x in fx["cases"] if x["case"] == name)
    return copy.deepcopy(c["card"]), {"viewer": fx["people"][c["viewer"]]["name"], "target": fx["people"][c["target"]]["name"]}


def _summary(h):
    m = re.search(r'<p class="ex-summary">(.*?)</p>', h or "")
    return m.group(1) if m else None


def _states(h):
    return re.findall(r'<p class="ex-state">(.*?)</p>', h or "")


# ── 444（§9-1）要旨 ─────────────────────────────────────────────────────────────────
def test_t444_summary_follows_states():
    card, names = _case("1_complement")                     # 目的1: explained・分からないことが 1 つ（一部）
    assert _summary(X.render(card, "viewer", names)) == "あなたの目的1に、つながる意味があります。"
    card, names = _case("2_resonance")                      # 目的1: not_covered（欠かせない要素が欠ける）／目的2: explained
    assert _summary(X.render(card, "viewer", names)) == \
        "あなたの目的2に、つながる意味があります。目的1は、つながった後に確かめられます。"
    card, names = _case("3_sparse")                         # 見る人の目的は explained が無い（逆の向きにだけ説明がある）
    s = _summary(X.render(card, "viewer", names))
    assert s == "あなたの目的1は、つながった後に確かめられます。" and "さんの目的" not in s   # 逆の向き（⑥）は入れない
    # 出さない目的（pending・要素も必須も無い）は要旨にも出さない
    card, names = _case("2_resonance")
    hidden = copy.deepcopy(card["purposes"][0])
    hidden.update(ref="viewer.purpose.p9", order=9, number=3, status="pending", missing_must=[], unknown=[], elements=[])
    empty = copy.deepcopy(hidden)
    empty.update(ref="viewer.purpose.p8", order=8, number=4, status="not_covered")
    card["purposes"] += [hidden, empty]
    s = _summary(X.render(card, "viewer", names))
    assert "目的3" not in s and "目的4" not in s


# ── 445（§9-2）状態の 1 行 ────────────────────────────────────────────────────────────
def test_t445_state_lines():
    card, names = _case("1_complement")
    assert _states(X.render(card, "viewer", names))[0] == X.STATE_TEXT["part"]          # explained ＋ 分からないこと
    card["purposes"][0]["unknown"] = []
    assert _states(X.render(card, "viewer", names))[0] == X.STATE_TEXT["all"]           # explained だけ
    assert _summary(X.render(card, "viewer", names)) == "あなたの目的1に、つながる意味があります。"
    card, names = _case("2_resonance")
    assert _states(X.render(card, "viewer", names))[0] == X.STATE_TEXT["check"]         # explained でない
    assert X.STATE_TEXT == {"all": "この目的の要素のすべてに、つながる意味があります。",
                            "part": "この目的の要素の一部に、つながる意味があります。",
                            "check": "この目的は、つながった後に確かめられます。"}
    for c in ("1_complement", "2_resonance", "3_sparse", "4_stale_supplemented", "5_same_target_other_viewer"):
        card, names = _case(c)
        h = X.render(card, "viewer", names) or ""
        for line in _states(h) + [_summary(h) or ""]:
            assert not re.search(r"[%％割]|[0-9０-９]+\s*(点|件|人中)", line), line            # 数値・割合を出さない


# ── 446（§9-3）根拠は畳む。画面1・2 は閉じ、画面3 は開く ───────────────────────────────────────
def test_t446_basis_folded_and_open_on_screen3(xs):
    d = _card("me", "c_full")                                                         # 画面1（申請の前は画面2 と同じ表示）
    h = d["html"]
    assert '<details class="ex-basis"><summary>根拠を見る ›</summary>' in h and "ex-basis\" open" not in h
    for part in re.findall(r'<div class="ex-purpose[^"]*"[^>]*>(.*?)</div>(?=<div class="ex-purpose|<div class="ex-sub ex-revh|<p class="ex-supp|<p class="ex-foot)', h):
        head, basis = part.split('<details class="ex-basis">', 1)
        assert "ex-els" not in head and "ex-unk" not in head                          # 根拠と確かめられることは中に
    assert _offer().status_code == 200
    inc = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()         # 画面3
    assert inc["html"].count('<details class="ex-basis" open>') == h.count('<details class="ex-basis">') > 0
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert src.count("X_explanation.open_basis(") == 2                               # 画面3 の表示と、承認した人の表示の保存


# ── 447（§9-4）⑥ の枠は畳まない ──────────────────────────────────────────────────────────
def test_t447_reverse_frame_not_folded():
    card, names = _case("3_sparse")
    h = X.render(card, "viewer", names)
    rev = h[h.index('<div class="ex-sub ex-revh">'):]
    assert rev.index("ソラ さんから見たあなた") < rev.index("<details")                 # 枠の見出しは畳まない
    first = rev[rev.index('<div class="ex-purpose ex-rev"'):]
    head = first[:first.index('<details class="ex-basis">')]
    assert '<h4 class="ex-ph">' in head and 'class="ex-meaning"' in head and 'class="ex-state"' in head


# ── 448（§9-5）古い文が出ない。missing_must は確かめられることの節に、欠かせない要素が先 ────────────────────────
def test_t448_check_section_replaces_unknown():
    for c in ("1_complement", "2_resonance", "3_sparse", "4_stale_supplemented", "5_same_target_other_viewer"):
        card, names = _case(c)
        h = X.render(card, "viewer", names) or ""
        assert "文からは分からないこと" not in h and "見つかりません" not in h, c
    assert not hasattr(X, "NOT_COVERED_TEXT") and not hasattr(X, "UNKNOWN_TEXT")
    # 欠けている欠かせない要素と、分からないこと（あれば嬉しい要素）が同じ目的にある
    card, names = _case("2_resonance")
    p = card["purposes"][0]
    nice = {"ref": "viewer.need.p1.1-9", "text": "店の帳簿を一緒に見直せる人。"}                  # あれば嬉しい要素（架空）
    card["citations"].append(nice)
    p["unknown"] = [{"need_ref": nice["ref"], "need_quote": "店の帳簿"}]
    h = X.render(card, "viewer", names)
    sec = h[h.index("── つながった後に確かめられること ──"):]
    items = re.findall(r"<li>(.*?)</li>", sec[:sec.index("</ul>")])
    assert len(items) == len(p["missing_must"]) + 1
    assert all(x.startswith("欠かせない要素：「") for x in items[:-1])                       # 欠かせない要素を先に
    assert items[-1] == (f"「店の帳簿」について、{names['target']} さんの経験は公開の文からは分かりません。"
                         "つながった後に確かめられます。")
    # 同じ要素が分からないことにも欠けている要素にもあるときは 1 回だけ（欠かせない要素として、分からないことの句で）
    p["unknown"] = [{"need_ref": p["missing_must"][0], "need_quote": "売上と在庫"}]
    h = X.render(card, "viewer", names)
    assert h.count("「売上と在庫」について") == 1 and "欠かせない要素：「売上と在庫」について" in h


# ── 449（§9-6）補足の案内 ───────────────────────────────────────────────────────────────
def test_t449_supplement_note():
    line = "「つながった後に確かめられること」への答えも、ここに書けます。"
    for f in ("profile.html", "inbox.html"):
        assert line in open(os.path.join(ROOT, "templates", f), encoding="utf-8").read(), f


# ── 450（§9-7）注記 ─────────────────────────────────────────────────────────────────
def test_t450_footer():
    expect = ("AI による説明です。並び順は近さの順ではありません。説明は、選ばれた理由ではなく、"
              "二人の公開の文から読み取った、つながる意味です。機械の確かめは正しさを保証しません。")
    assert X.FOOTER_TEXT == expect
    card, names = _case("1_complement")
    assert f'<p class="ex-foot">{expect}</p>' in X.render(card, "viewer", names)


# ── 451（§9-8）保存済みの表示は描き直さない ──────────────────────────────────────────────────
OLD_HTML = ('<section class="ex-card" data-schema="expl-v0.4" data-status="explained"><div class="ex-purpose">'
            '<h4 class="ex-ph">■ ⟦V⟧の目的1「店」について</h4><p class="ex-meaning">⟦T⟧は、⟦V⟧の店に関われる可能性があります。</p>'
            '<div class="ex-sub">── 文からは分からないこと ──</div><ul class="ex-unk"><li>「帳簿」に当たる経験は、⟦T⟧の公開の文の中には'
            '見つかりません</li></ul></div><p class="ex-foot">AI による説明です。根拠は二人の公開の文です。機械の確かめは正しさを保証しません。'
            '</p><button type="button" class="ex-supp-btn">補足する ›</button></section>')


def test_t451_saved_display_is_not_redrawn(xs):
    S.save_offer_render("me", "c_full", OLD_HTML, X.personalize(OLD_HTML, "viewer", NAMES), "d" * 64, db_path=appmod.DB)
    from ledger import approve
    approve("me", "c_full", db_path=appmod.DB)                                          # 申請中（画面3 に出る）にする
    inc = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()
    assert inc["status"] == "ok"
    assert inc["html"] == X.personalize(OLD_HTML, "recipient", NAMES)                   # そのまま（名前だけ。開く枠も無い）
    assert "文からは分からないこと" in inc["html"] and "根拠は二人の公開の文です" in inc["html"]


# ── 452（§9-9）画面3 に「あなた」が残らない・呼び方の置き換えが要旨と状態にも効く ──────────────────────────
def test_t452_names_in_summary_and_states(xs):
    assert _offer().status_code == 200
    h = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()["html"]
    assert "あなた" not in h and re.search(r"⟦[VT]⟧", h) is None
    assert _summary(h).startswith("アオイ さんの目的")
    assert all("⟦" not in s and "あなた" not in s for s in _states(h))
    v = _card("me", "c_full")["html"]
    assert _summary(v).startswith("あなたの目的")
