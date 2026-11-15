"""指示書74 順1 — 受信箱の一覧と詳細（承認）画面・目的の並び（仕様 v0.8 §16）。テスト 463〜469（§8 の 1〜7）。

カードは tests/fixtures/explanation_v0_4.json の架空の人のもの。足す文も架空（KAORU・PA の文は使わない）。

463（§8-1）受信箱の一覧に、説明・一行紹介が出ない。詳細に、一行紹介が出ない
464（§8-2）詳細の並びが §1-2 のとおり。受けた人の目的の側が先
465（§8-3）どちらの側の根拠も閉じている。見出しが「根拠」
466（§8-4）目的が §2 の順に並ぶ（画面1・画面3・逆の向き・要旨・一覧の短い形）。番号は変わらない
467（§8-5）組み直した表示の文が、保存した表示の文と一致する。pair_digest が変わらない
468（§8-6）この指示書より前に保存された申請が、前の形のまま出る
469（§8-7）画面3 に「あなた」が残らない。承認した人の表示のハッシュは、詳細で見た表示のハッシュ
"""
import copy
import hashlib
import hmac
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import explanation as X  # noqa: E402
import explanation_agreement as XA  # noqa: E402
import explanation_store as S  # noqa: E402
from canon import canonicalize  # noqa: E402
from test_t61_resonance_gate import _cli  # noqa: E402
from test_t68_screens import KEY, _card, _load, _offer, _pair_doc, _state, xs  # noqa: E402,F401
from test_t65b2_two_layer import world2  # noqa: E402,F401
from test_t61_resonance_gate import db  # noqa: E402,F401

FIX = os.path.join(ROOT, "tests", "fixtures", "explanation_v0_4.json")
NAMES = {"viewer": "アオイ", "target": "ハル"}
CASES = ("1_complement", "2_resonance", "3_sparse", "4_stale_supplemented", "5_same_target_other_viewer")


def _case(name):
    with open(FIX, encoding="utf-8") as f:
        fx = json.load(f)
    c = next(x for x in fx["cases"] if x["case"] == name)
    return copy.deepcopy(c["card"]), {"viewer": fx["people"][c["viewer"]]["name"], "target": fx["people"][c["target"]]["name"]}


def _tpl(name):
    return open(os.path.join(ROOT, "templates", name), encoding="utf-8").read()


def _mixed_card():
    """目的が 3 つ（目的1＝open・目的2＝explained・目的3＝分からない）と、逆の向きが 2 つ（目的1＝分からない・目的2＝explained）。"""
    card, _ = _case("1_complement")
    card["purposes"][0]["missing_must"] = ["viewer.need.p1.1-3"]                     # 目的1: open の形
    card["citations"] += [{"ref": "viewer.need.p3.3-1", "text": "帳簿の付け方を教えられる人。"},
                          {"ref": "target.need.p2.2-1", "text": "古道具の修理を一緒に楽しめる人。"}]
    card["purposes"][1].update(status="explained", elements=[                         # 目的2: explained（欠けが無い）
        {"need_ref": "viewer.need.p2.2-1", "must": False, "need_quote": "古い大工道具の手入れ",
         "offer_ref": "target.interest.1", "offer_quote": "パン作り", "relation": "R4"}],
        meaning={"text": "{O}は、パン作りに関心がある人で、{N}の語り合える場に関われる可能性があります。", "cites": [0]})
    p3 = copy.deepcopy(card["purposes"][1])
    p3.update(ref="viewer.purpose.p3", order=2, number=3, title="帳簿を続けたい", kind="補完", status="not_covered",
              meaning=None, elements=[], unknown=[], missing_must=["viewer.need.p3.3-1"])
    card["purposes"].append(p3)                                                       # 目的3: 分からない
    r1 = card["reverse_purposes"][0]
    r1.update(status="not_covered", meaning=None, elements=[], missing_must=["target.need.p1.1-1"])
    card["reverse_purposes"].append({
        "ref": "target.purpose.p2", "order": 1, "number": 2, "title": "古道具を直す仲間がほしい", "kind": "共鳴",
        "status": "explained", "unknown": [], "missing_must": [],
        "meaning": {"text": "{O}は、古道具の修理に関心がある人で、{N}の仲間づくりに関われる可能性があります。", "cites": [0]},
        "elements": [{"need_ref": "target.need.p2.2-1", "must": False, "need_quote": "古道具の修理",
                      "offer_ref": "viewer.interest.1", "offer_quote": "古道具の修理", "relation": "R4"}]})
    return card


def _heads(h):
    return re.findall(r'<h4 class="ex-ph">■ (.*?)「', h)


def _texts(h):
    return Counter(t.strip() for t in re.split(r"<[^>]+>", h) if t.strip())


# ── 463（§8-1）受信箱の一覧には説明・一行紹介を出さない ───────────────────────────────────────
def test_t463_inbox_list_has_no_explanation_or_one_liner():
    t = _tpl("inbox.html")
    lst = t[t.index("function renderApprovals("):t.index("/* 詳細（承認）画面")]
    assert 'href="/inbox?from=' in lst and "ib-row-name" in lst and "に届きました" in lst   # 1 人 1 行: 名前・届いた日
    for word in ("loadIncomingExplanation", "ib-explain", "one_liner", "PoXReason", "doApprove", "askDecline"):
        assert word not in lst, word                                                  # 説明・承認は詳細の画面だけ
    detail = t[t.index("function renderDetail("):t.index("async function loadIncomingExplanation(")]
    assert "one_liner" not in detail and "ib-one" not in t                           # 詳細にも一行紹介は出さない
    assert "from_one_liner" not in t


# ── 464（§8-2）詳細の並び ─────────────────────────────────────────────────────────────────
def test_t464_detail_order(xs):
    t = _tpl("inbox.html")
    detail = t[t.index("function renderDetail("):t.index("async function loadIncomingExplanation(")]
    order = ["さんから申請が届きました", "mdOf(o.at)", 'class="ib-explain"', 'class="ex-check"', 'onclick="doApprove(']
    assert [detail.index(x) for x in order] == sorted(detail.index(x) for x in order)
    load = t[t.index("async function loadIncomingExplanation("):t.index("function syncApprove(")]
    assert "open: true" in load                                                       # 補足の欄は開いた状態（順1）
    assert _offer().status_code == 200
    h = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()["html"]  # 説明の部分（サーバーが組み直したもの）
    rev, main = h.index('class="ex-side ex-side-rev"'), h.index('class="ex-side ex-side-main"')
    assert rev < main < h.index('<p class="ex-foot">') < h.index("補足する ›")          # 受けた人の側 → 申請者の側 → 注記
    assert "ハル さん（受けた人）の目的から見た、アオイ さん" in h
    assert "アオイ さん（申請した人）の目的から見た、ハル さん" in h
    assert "ex-revh" not in h                                                         # ⑥ の見出しは側の見出しに置き換える
    assert h.index("■ ハル さんの目的") < h.index("■ アオイ さんの目的")


# ── 465（§8-3）根拠は閉じる・見出しは「根拠」 ───────────────────────────────────────────────
def test_t465_basis_closed_and_heading(xs):
    assert X.BASIS_HEAD == "根拠"
    for c in CASES:
        card, names = _case(c)
        h = X.render(card, "viewer", names) or ""
        assert "その根拠として" not in h, c
        assert "<details class=\"ex-basis\" open>" not in h
    card, names = _case("1_complement")
    assert "── 根拠 ──" in X.render(card, "viewer", names)
    assert _offer().status_code == 200
    h = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()["html"]
    assert h.count('<details class="ex-basis">') > 0 and "ex-basis\" open" not in h   # 両方の側とも閉じている
    for side in ("ex-side-rev", "ex-side-main"):
        part = h[h.index(f'class="ex-side {side}"'):]
        assert part.index('<details class="ex-basis">') > 0
    assert '<details class="ex-src" open>' not in h                                  # 原文も閉じたまま


# ── 466（§8-4）目的の並び（すべての画面）。番号は変わらない ──────────────────────────────────────
def test_t466_purpose_order_everywhere():
    card = _mixed_card()
    h = X.render(card, "viewer", NAMES)                                              # 画面1・2
    assert _heads(h[:h.index("ex-revh")]) == ["あなたの目的2", "あなたの目的1", "あなたの目的3"]
    assert _heads(h[h.index("ex-revh"):]) == ["ハル さんの目的2", "ハル さんの目的1"]          # 逆の向きも
    assert re.search(r'<p class="ex-summary">(.*?)</p>', h).group(1) == "あなたの目的2・目的1に、つながる意味があります。"
    short = X.short_form(card)                                                        # 一覧の短い形
    assert [x["head"].split("「")[0] for x in short] == ["⟦V⟧の目的2", "⟦V⟧の目的1"]
    inc = X.incoming_html(X.render_symbolic(card), NAMES)                              # 画面3
    assert _heads(inc) == ["ハル さんの目的2", "ハル さんの目的1", "アオイ さんの目的2", "アオイ さんの目的1", "アオイ さんの目的3"]
    sums = re.findall(r'<p class="ex-summary">(.*?)</p>', inc)
    assert sums == ["ハル さんの目的2に、つながる意味があります。", "アオイ さんの目的2・目的1に、つながる意味があります。"]
    shuffled = copy.deepcopy(card)                                                    # JSON の並びによらない
    shuffled["purposes"].reverse()
    shuffled["reverse_purposes"].reverse()
    assert X.render(shuffled, "viewer", NAMES) == h
    # 問いは今までどおり open の目的（目的1）に 1 つ。番号は変わらない
    assert h.count(X.ASK_HEAD) == 1 and h.index(X.ASK_HEAD) > h.index("■ あなたの目的1")


# ── 467（§8-5）組み直しで文は一字も変わらない・pair_digest も変わらない ─────────────────────────────
def test_t467_rebuild_keeps_every_text(xs):
    cards = [_case(c)[0] for c in CASES] + [_mixed_card()]
    for card in cards:
        sym = X.render_symbolic(card)
        lay = X.incoming_layout(sym)
        assert lay is not None
        added, removed = _texts(lay) - _texts(sym), _texts(sym) - _texts(lay)
        allowed_added = {X.SIDE_HEAD_REV["p"].format(t=X.T, v=X.V), X.SIDE_HEAD_REV["s"].format(t=X.T, v=X.V),
                         X.SIDE_HEAD_MAIN.format(t=X.T, v=X.V)}
        for t in added:
            assert t in allowed_added or re.fullmatch(r"⟦T⟧の目的[0-9]+(・目的[0-9]+)*に、つながる意味があります。", t), t
        assert set(removed) <= {f"── {X.T}から見た{X.V} ──", f"── {X.T}が求めている相手 ──"}, removed
        for frag in re.findall(r'<div class="ex-purpose[^"]*" data-ref="[^"]*"', sym):  # 目的の枠はそのまま全部ある
            assert lay.count(frag) == sym.count(frag)
    # 画面から承認まで: 合意の記録（pair_digest）は保存したカードの組から作る（表示の組み直しに左右されない）
    assert _offer().status_code == 200
    row = S.pending_render("me", "c_full", db_path=appmod.DB)
    _cli("c_full").get("/api/explanations/incoming?with=me")
    assert S.pending_render("me", "c_full", db_path=appmod.DB)["card_json"] == row["card_json"]
    assert _cli("c_full").post("/approve", json={"to_id": "me", "explanation_confirmed": True}).status_code == 200
    done = S.approved_render("me", "c_full", db_path=appmod.DB)
    a1 = XA.a1_object(json.loads(row["card_json"]), "me", "c_full")
    assert done["pair_digest"] == hmac.new(KEY.encode(), canonicalize(a1), hashlib.sha256).hexdigest()


# ── 468（§8-6）前に保存した申請は前の形のまま ───────────────────────────────────────────────
def test_t468_saved_before_74_keeps_old_form(xs):
    from ledger import approve
    from test_t71_explanation_v05 import OLD_HTML
    from test_t73_explanation_v07 import V05_HTML
    card, _ = _case("1_complement")
    v07 = X.render_symbolic(card).replace(X.MAIN_OPEN, "").replace(X.MAIN_CLOSE, "")   # 印の無い形（v0.7 まで）
    v07 = re.sub(r"<!--/?ex:rev(:[ps])?-->", "", v07)
    for saved in (OLD_HTML, V05_HTML, v07):
        assert X.incoming_layout(saved) is None
        S.save_offer_render("me", "c_full", saved, X.personalize(saved, "viewer", NAMES), "d" * 64, db_path=appmod.DB)
        approve("me", "c_full", db_path=appmod.DB)
        inc = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()
        assert inc["status"] == "ok"
        assert inc["html"] == X.open_basis(X.personalize(saved, "recipient", NAMES))   # 前の形（並べ直さない）


# ── 469（§8-7）画面3 に「あなた」が残らない・承認した人の表示のハッシュ ──────────────────────────────
def test_t469_no_anata_and_recipient_digest(xs):
    assert _offer().status_code == 200
    inc = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()["html"]
    assert "あなた" not in inc and re.search(r"⟦[VT]⟧", inc) is None
    assert _cli("c_full").post("/approve", json={"to_id": "me", "explanation_confirmed": True,
                                                 "supplement": "週末なら話せます"}).status_code == 200
    assert _state("me", "c_full") == "connected"
    done = S.approved_render("me", "c_full", db_path=appmod.DB)
    assert S.verify_digest(inc, done["recipient_digest"], done["recipient_key_id"])     # 詳細で見た表示（補足は含めない）
