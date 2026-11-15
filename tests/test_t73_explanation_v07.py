"""指示書73 順1 — 「分からないこと」を事実だけにし、問いは根拠があるときだけ出す（表示・仕様 v0.7 §15）。テスト 453〜462。

順1 は表示だけ。生成はまだ open を出さないので、「explained で missing_must がある」目的（取り込み分の形）を open とみなす。
カードは tests/fixtures/explanation_v0_4.json の架空の人のもの。足す文も架空（KAORU・PA の文は使わない）。

453（§7-6）状態の 1 行が §4-1 の表どおり。要旨に「分からない」の目的が入らない
454（§7-7）分からないことの文に「確かめられます」が無い。見出しが「文からは分からないこと」。両方の向きに出る
455（§7-8）問いは open だけ・本体の向きだけ・最初の欠けた欠かせない要素（必要の並び順）・1 枚に 1 つ
456（§7-9）句が 40 字を超え unknown の句も無いとき・必要像に無い要素のとき、問いが出ない（分からないことの文は出る）
457（§7-10）取り込み分の形（explained で missing_must がある）が open と同じに描かれる
458（§7-11）一覧の短い形に問いが出ない。pair_digest が問いの有無で変わらない
459（§7-12）補足の案内が新しい 1 文（画面1・3）
460（§7-13）保存済みの表示（v0.4・v0.5）が描き直されずにそのまま出る
461（§7-14）画面3 に、申請者が読んだ問いがそのまま出て、「あなた」が残らない
462 レンダラの版 v0.7・問いは「根拠を見る」の中で、分からないことの節の後ろ・render_digest は問いを含む表示のハッシュ
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
from test_t68_screens import _card, _load, _offer, _pair_doc, xs, KEY  # noqa: E402,F401
from test_t65b2_two_layer import world2  # noqa: E402,F401
from test_t61_resonance_gate import db  # noqa: E402,F401

FIX = os.path.join(ROOT, "tests", "fixtures", "explanation_v0_4.json")
NAMES = {"viewer": "アオイ", "target": "ハル"}
ASK_1_3 = "「地域の行事を企画し、人を集めてきた」について、経験はありますか。いまは、関われる状況ですか。"


def _case(name):
    with open(FIX, encoding="utf-8") as f:
        fx = json.load(f)
    c = next(x for x in fx["cases"] if x["case"] == name)
    return copy.deepcopy(c["card"]), {"viewer": fx["people"][c["viewer"]]["name"], "target": fx["people"][c["target"]]["name"]}


def _cite(card, ref, text):
    card["citations"].append({"ref": ref, "text": text})


def _open_card():
    """目的1 が open の形（explained で、組がある。あれば嬉しい要素だった 1-3 を、欠けた欠かせない要素にする）。"""
    card, names = _case("1_complement")
    card["purposes"][0]["missing_must"] = ["viewer.need.p1.1-3"]
    return card, names


def _explain_p2(card):
    """目的2（共鳴）にも確かめを通る組と ① を足し、欠けた欠かせない要素（架空の 2-2）を足して open にする。"""
    _cite(card, "viewer.need.p2.2-2", "道具の写真を撮って残せる人。")
    p2 = card["purposes"][1]
    p2.update(status="explained", missing_must=["viewer.need.p2.2-2"],
              elements=[{"need_ref": "viewer.need.p2.2-1", "must": False, "need_quote": "古い大工道具の手入れ",
                         "offer_ref": "target.interest.1", "offer_quote": "パン作り", "relation": "R4"}],
              meaning={"text": "{O}は、パン作りに関心がある人で、{N}の語り合える場に関われる可能性があります。", "cites": [0]})
    return card


def _summary(h):
    m = re.search(r'<p class="ex-summary">(.*?)</p>', h or "")
    return m.group(1) if m else None


def _states(h):
    return re.findall(r'<p class="ex-state">(.*?)</p>', h or "")


def _asks(h):
    return re.findall(r'<ul class="ex-ask"><li>(.*?)</li></ul>', h or "")


def _purpose_block(h, ref):
    i = h.index(f'data-ref="{ref}"')
    j = h.find('<div class="ex-purpose', i)
    k = h.find('<div class="ex-sub ex-revh">', i)
    ends = [x for x in (j, k, h.find('<p class="ex-foot">', i)) if x != -1]
    return h[i:min(ends)]


# ── 453（§7-6）状態の 1 行と要旨 ─────────────────────────────────────────────────────────
def test_t453_state_lines_and_summary():
    assert X.STATE_TEXT["unknown"] == "この目的については、公開の文からは分かりません。"
    card, names = _case("1_complement")                     # 目的1: explained ＋ unknown → 一部
    h = X.render(card, "viewer", names)
    assert _states(h)[0] == X.STATE_TEXT["part"]
    card["purposes"][0]["unknown"] = []                     # explained だけ → すべて
    assert _states(X.render(card, "viewer", names))[0] == X.STATE_TEXT["all"]
    card, names = _open_card()                              # open（explained ＋ missing_must）→ 一部
    h = X.render(card, "viewer", names)
    assert _states(h)[0] == X.STATE_TEXT["part"] and 'data-state="part"' in _purpose_block(h, "viewer.purpose.p1")
    card, names = _case("2_resonance")                      # 目的1: not_covered ＋ missing_must → 分からない
    h = X.render(card, "viewer", names)
    assert _states(_purpose_block(h, "viewer.purpose.p1")) == [X.STATE_TEXT["unknown"]]
    assert 'data-state="unknown"' in _purpose_block(h, "viewer.purpose.p1")
    assert _summary(h) == "あなたの目的2に、つながる意味があります。"           # 「分からない」の目的1は要旨に入れない
    card, names = _case("3_sparse")                         # 見る人の目的に、つながる意味が無い → 要旨を出さない
    h = X.render(card, "viewer", names)
    assert _summary(h) is None and X.STATE_TEXT["unknown"] in h
    card, names = _case("1_complement")                     # 分からないことも無い not_covered（目的2）は出さない
    assert 'data-ref="viewer.purpose.p2"' not in X.render(card, "viewer", names)
    card = _explain_p2(_open_card()[0])                     # 二つとも つながる意味がある
    assert _summary(X.render(card, "viewer", NAMES)) == "あなたの目的1・目的2に、つながる意味があります。"


# ── 454（§7-7）分からないことは事実だけ・見出し・両方の向き ─────────────────────────────────────────
def _all_renders():
    for c in ("1_complement", "2_resonance", "3_sparse", "4_stale_supplemented", "5_same_target_other_viewer"):
        card, names = _case(c)
        yield c, card, names
    yield "open", *_open_card()
    yield "open2", _explain_p2(_open_card()[0]), NAMES


def test_t454_fact_section():
    assert X.FACT_HEAD == "文からは分からないこと"
    assert X.FACT_TEXT == "「{quote}」について、{who}の経験は公開の文からは分かりません。"
    for c, card, names in _all_renders():
        for reader in ("viewer", "recipient"):
            h = X.render(card, reader, names) or ""
            assert "確かめられ" not in h, (c, reader)                  # 成功の条件 2（新しく描く表示のどこにも）
            for line in re.findall(r'<ul class="ex-unk">(.*?)</ul>', h):
                for li in re.findall(r"<li>(.*?)</li>", line):
                    assert li.endswith("の経験は公開の文からは分かりません。"), (c, li)
    card, names = _case("2_resonance")                      # 本体の向き: 欠かせない要素が先・頭に印
    h = X.render(card, "viewer", names)
    blk = _purpose_block(h, "viewer.purpose.p1")
    assert "── 文からは分からないこと ──" in blk and "欠かせない要素：「" in blk
    card, names = _case("5_same_target_other_viewer")       # 逆の向き（⑥）にも出す
    h = X.render(card, "viewer", names)
    rev = h[h.index('<div class="ex-sub ex-revh">'):]
    assert "── 文からは分からないこと ──" in rev and X.STATE_TEXT["unknown"] in rev
    assert X.ASK_HEAD not in h                              # 根拠の無い目的に問いは出ない（成功の条件 1）


# ── 455（§7-8）問いの条件 ───────────────────────────────────────────────────────────────
def test_t455_question_conditions():
    card, names = _open_card()                              # open → 問いが 1 つ（unknown の句）
    h = X.render(card, "viewer", names)
    assert _asks(h) == [ASK_1_3]
    assert _asks(_purpose_block(h, "viewer.purpose.p1")) == [ASK_1_3]
    for c in ("1_complement", "2_resonance", "3_sparse", "4_stale_supplemented", "5_same_target_other_viewer"):
        card, names = _case(c)                              # explained（欠けが無い）・not_covered には出ない
        assert X.ASK_HEAD not in (X.render(card, "viewer", names) or ""), c
    # 必要の並び順で最初の欠けた欠かせない要素（missing_must の並びではない）
    card, names = _open_card()
    _cite(card, "viewer.need.p1.1-4", "週末に店番を代われる人。")
    card["purposes"][0]["missing_must"] = ["viewer.need.p1.1-4", "viewer.need.p1.1-3"]
    assert _asks(X.render(card, "viewer", names)) == [ASK_1_3]
    card["purposes"][0]["missing_must"] = ["viewer.need.p1.1-4"]        # unknown の句が無い → 40 字以内の全文
    assert _asks(X.render(card, "viewer", names)) == \
        ["「週末に店番を代われる人。」について、経験はありますか。いまは、関われる状況ですか。"]
    # 逆の向き（⑥）には出さない
    card, names = _case("1_complement")
    _cite(card, "target.need.p1.1-2", "道具の使い方を教えられる人。")
    card["reverse_purposes"][0]["missing_must"] = ["target.need.p1.1-2"]
    h = X.render(card, "viewer", names)
    assert 'data-state="part"' in h[h.index('<div class="ex-sub ex-revh">'):] and X.ASK_HEAD not in h
    # カード 1 枚に 1 つまで: open が二つなら、プロフィールの順で最初の目的だけ
    card = _explain_p2(_open_card()[0])
    h = X.render(card, "viewer", NAMES)
    assert _asks(h) == [ASK_1_3] and X.ASK_HEAD not in _purpose_block(h, "viewer.purpose.p2")
    card["purposes"][0]["missing_must"] = []                # 目的1 が open でなければ、目的2 に出る
    assert _asks(X.render(card, "viewer", NAMES)) == \
        ["「道具の写真を撮って残せる人。」について、経験はありますか。いまは、関われる状況ですか。"]
    # 本体と逆の向きの両方が open でも、本体の 1 つだけ
    card, names = _open_card()
    _cite(card, "target.need.p1.1-2", "道具の使い方を教えられる人。")
    card["reverse_purposes"][0]["missing_must"] = ["target.need.p1.1-2"]
    assert _asks(X.render(card, "viewer", names)) == [ASK_1_3]
    # 要旨・①・状態の 1 行には出さない
    h = X.render(*_open_card()[:1], "viewer", NAMES)
    for part in [_summary(h)] + _states(h) + re.findall(r'<p class="ex-meaning">(.*?)</p>', h):
        assert "経験はありますか" not in part


# ── 456（§7-9）句が無いときは問いを出さない ───────────────────────────────────────────────────
def test_t456_no_question_without_quote():
    card, names = _open_card()
    long_text = "店の売上と在庫の記録を、毎日欠かさず続けるための手順を、地域の店主と一緒に考えて決めてきた人。"
    assert len(long_text) > X.QUOTE_MAX
    _cite(card, "viewer.need.p1.1-4", long_text)
    card["purposes"][0]["missing_must"] = ["viewer.need.p1.1-4"]          # 全文が 40 字を超え、unknown の句も無い
    h = X.render(card, "viewer", names)
    assert X.ASK_HEAD not in h
    assert f"欠かせない要素：「{long_text}」について、ハル さんの経験は公開の文からは分かりません。" in h   # 事実の文は出る
    # 最初の open の目的に句が無ければ、次の目的には回さない（1 枚に 1 つまで・最初の目的だけ）
    card = _explain_p2(card)
    assert X.ASK_HEAD not in X.render(card, "viewer", NAMES)
    # 必要像に無い要素（引用に無い参照）は問いの対象にしない
    card, names = _open_card()
    card["purposes"][0]["missing_must"] = ["viewer.need.p1.1-9"]
    card["purposes"][0]["unknown"] = []
    assert X.ASK_HEAD not in X.render(card, "viewer", names)


# ── 457（§7-10）取り込み分の形は open と同じに描く ─────────────────────────────────────────────
def test_t457_imported_explained_with_missing_must_is_open():
    card, names = _open_card()
    assert card["purposes"][0]["status"] == "explained" and card["purposes"][0]["missing_must"]
    checked, _ = X.check(card)
    assert X._is_open(checked["purposes"][0]) and not X._is_open(checked["reverse_purposes"][0])
    h = X.render(card, "viewer", names)
    blk = _purpose_block(h, "viewer.purpose.p1")
    assert 'class="ex-meaning"' in blk                                    # ① は出す（確かめを通ったもの）
    assert _states(blk) == [X.STATE_TEXT["part"]]                         # 状態は「一部」
    assert "目的1" in _summary(h)
    assert "── 文からは分からないこと ──" in blk and _asks(blk) == [ASK_1_3]
    assert card == _open_card()[0]                                        # カード（保存してあるもの）は書き換えない


# ── 458（§7-11）一覧の短い形・pair_digest ───────────────────────────────────────────────────
def test_t458_short_form_and_pair_digest(monkeypatch):
    import explanation_agreement as A
    card, names = _open_card()
    short = X.short_form(card)
    assert short and short[0]["head"].startswith("⟦V⟧の目的1")           # open の目的の ① は出す
    assert "経験はありますか" not in json.dumps(short, ensure_ascii=False) and X.ASK_HEAD not in json.dumps(short, ensure_ascii=False)
    monkeypatch.setenv("POX_EXPLANATION_DIGEST_KEY", KEY)
    a1 = A.a1_object(card, "me", "c_full")
    d1 = A.pair_digest(a1)
    assert "経験はありますか" not in json.dumps(a1, ensure_ascii=False)
    plain, _ = _case("1_complement")                                       # 問いが出ない同じ組
    assert A.pair_digest(A.a1_object(plain, "me", "c_full")) == d1
    h1 = X.render(card, "viewer", names)
    monkeypatch.setattr(X, "ASK_TEXT", "「{quote}」について。")                # 問いの文が変わっても
    assert X.render(card, "viewer", names) != h1
    assert A.pair_digest(A.a1_object(card, "me", "c_full")) == d1          # pair_digest は変わらない


# ── 459（§7-12）補足の案内 ───────────────────────────────────────────────────────────────
def test_t459_supplement_note():
    line = "「文からは分からないこと」や「つながった後に尋ねること」について、ここに書けます。書かなくてもかまいません。"
    for f in ("profile.html", "inbox.html"):                               # 画面1（プロフィール）・画面3（受信箱）
        src = open(os.path.join(ROOT, "templates", f), encoding="utf-8").read()
        assert line in src and "確かめられること" not in src, f


# ── 460（§7-13）保存済みの表示（v0.4・v0.5）は描き直さない ──────────────────────────────────────
V05_HTML = ('<section class="ex-card" data-schema="expl-v0.4" data-render="v0.5" data-status="explained">'
            '<p class="ex-summary">⟦V⟧の目的1は、つながった後に確かめられます。</p>'
            '<div class="ex-purpose" data-ref="viewer.purpose.p1" data-status="not_covered" data-state="check">'
            '<h4 class="ex-ph">■ ⟦V⟧の目的1「店」について</h4><p class="ex-state">この目的は、つながった後に確かめられます。</p>'
            '<details class="ex-basis"><summary>根拠を見る ›</summary><div class="ex-sub">── つながった後に確かめられること ──</div>'
            '<ul class="ex-unk"><li>「帳簿」について、⟦T⟧の経験は公開の文からは分かりません。つながった後に確かめられます。</li>'
            '</ul></details></div><p class="ex-foot">AI による説明です。</p>'
            '<button type="button" class="ex-supp-btn">補足する ›</button></section>')


def test_t460_saved_displays_are_not_redrawn(xs):
    from ledger import approve
    from test_t71_explanation_v05 import OLD_HTML
    for saved in (OLD_HTML, V05_HTML):
        S.save_offer_render("me", "c_full", saved, X.personalize(saved, "viewer", NAMES), "d" * 64, db_path=appmod.DB)
        approve("me", "c_full", db_path=appmod.DB)
        inc = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()
        assert inc["status"] == "ok"
        assert inc["html"] == X.open_basis(X.personalize(saved, "recipient", NAMES))   # 名前と開く印だけ
        assert "確かめられ" in inc["html"] or "見つかりません" in inc["html"]          # 古い文のまま


# ── 461（§7-14）画面3 に、申請者が読んだ問いがそのまま出る ─────────────────────────────────────
def test_t461_screen3_shows_the_question_as_read(xs):
    card, _ = _open_card()
    doc = _pair_doc()
    doc["cards"][0]["card"] = card
    assert _load(doc)["saved"] == 2
    v = _card("me", "c_full")["html"]                                      # 画面1（申請の前）
    assert _asks(v) == [ASK_1_3]
    assert _offer().status_code == 200
    row = S.pending_render("me", "c_full", db_path=appmod.DB)
    inc = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()["html"]   # 画面3
    assert inc == X.incoming_html(row["symbolic_html"], NAMES)                         # 再生成しない（組み直すだけ）
    assert _asks(inc) == [ASK_1_3] and '<details class="ex-basis" open>' not in inc     # 根拠は閉じる（指示書74 §1-3）
    main = inc[inc.index('class="ex-side ex-side-main"'):]                              # 問いは申請者の目的の側（指示書74 §7）
    assert _asks(main) == [ASK_1_3]
    assert "あなた" not in inc and re.search(r"⟦[VT]⟧", inc) is None


# ── 462 版・置き場所・render_digest ────────────────────────────────────────────────────────
def test_t462_version_placement_and_render_digest(monkeypatch):
    monkeypatch.setenv("POX_EXPLANATION_DIGEST_KEY", KEY)
    assert X.RENDER_VERSION == "v0.8"                                                # 指示書74 で v0.8（問いの型は v0.7 のまま）
    card, names = _open_card()
    h = X.render(card, "viewer", names)
    assert 'data-render="v0.8"' in h
    blk = _purpose_block(h, "viewer.purpose.p1")
    basis = blk[blk.index('<details class="ex-basis">'):]
    head = blk[:blk.index('<details class="ex-basis">')]
    assert X.ASK_HEAD not in head and X.ASK_HEAD in basis                 # 「根拠を見る」の中
    assert basis.index("── 文からは分からないこと ──") < basis.index(f"── {X.ASK_HEAD} ──")   # 事実の節の後ろ
    plain, _ = _case("1_complement")
    assert S.render_digest(h) != S.render_digest(X.render(plain, "viewer", names))   # 見た表示（問いを含む）のハッシュ
