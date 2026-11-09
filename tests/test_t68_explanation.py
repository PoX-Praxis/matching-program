"""指示書68 段階1 — 接続の説明の契約（カードの JSON）・機械の確かめ・レンダラ（固定のデータ・LLM なし）。

合成データ（tests/fixtures/explanation_v0_4.json）はすべて架空の人と文。§7 のうち段階1で確かめられるもの:
1 スキーマ／2 呼び方／3 目的の並び／4 確かめに落ちた部分を出さない／5 画面2 の保存 → 画面3 は置き換えだけ／
12 状態の表示が崩れない／13 上層の印を出さない。画面への組み込み・保存・フラグ・HMAC（§7 の 6〜11）は段階1の後半（§9-3）。
"""
import copy
import json
import os
import re
import sys
from html.parser import HTMLParser

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src")]

import explanation as X  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "explanation_v0_4.json")


def _fix():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)


def _case(name):
    d = _fix()
    c = next(x for x in d["cases"] if x["case"] == name)
    names = {"viewer": d["people"][c["viewer"]]["name"], "target": d["people"][c["target"]]["name"]}
    return copy.deepcopy(c["card"]), names


def _text(h):
    h = re.sub(r"<details.*?</details>", "", h)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))


class _Balance(HTMLParser):
    VOID = {"br", "img", "hr", "input", "meta", "link"}

    def __init__(self):
        super().__init__()
        self.stack, self.ok = [], True

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.ok = False


def _balanced(h):
    p = _Balance()
    p.feed(h)
    return p.ok and not p.stack


# ── 387 スキーマ: 合成データは通る・形の違うものは弾く（§7-1）──────────────────────────────────
def test_t387_schema_accepts_fixtures_and_rejects_bad():
    for c in _fix()["cases"]:
        ok, why = X.validate(c["card"])
        assert ok, (c["case"], why)
        assert c["card"]["output_digest"] == X.output_digest(c["card"])          # 出力のハッシュ（§12-3）
    card, _ = _case("1_complement")
    bad = []
    b = copy.deepcopy(card); del b["as_of"]; bad.append(b)                           # 版と as-of が無い
    b = copy.deepcopy(card); b["purposes"][0]["elements"][0]["relation"] = "R5"; bad.append(b)   # ラベルの外
    b = copy.deepcopy(card); b["purposes"][0]["elements"][0]["offer_quote"] = "あ" * 41; bad.append(b)   # 句が 40 字超
    b = copy.deepcopy(card); b["status"] = "maybe"; bad.append(b)                    # 状態の外
    b = copy.deepcopy(card); b["purposes"][0]["score"] = 0.8; bad.append(b)          # 数値の欄を持たない
    b = copy.deepcopy(card); b["purposes"][0]["kind"] = "相補"; bad.append(b)        # 種類は補完／共鳴
    for x in bad:
        assert X.validate(x)[0] is False


# ── 388 呼び方: 画面1・2 は「あなた」「〇〇さん」、画面3 は両方ともユーザー名。記号が残らない（§7-2）──────────
def test_t388_reader_wording():
    card, names = _case("1_complement")
    s1 = X.render(card, "viewer", names)
    s3 = X.render(card, "recipient", names)
    t1, t3 = _text(s1), _text(s3)
    assert "あなたの目的1" in t1 and "ハル さんから見たあなた" in t1 and "ハル さんは、" in t1
    assert "あなた" not in t3                                                       # 画面3 に一人称・二人称を残さない
    assert "アオイ さんの目的1" in t3 and "ハル さんから見たアオイ さん" in t3
    for h in (s1, s3):
        assert "{N}" not in h and "{O}" not in h and X.V not in h and X.T not in h
    # カードの本文（生成した文）は名前や「あなた」を持たない（呼び方は記号だけ）
    for p in card["purposes"] + card["reverse_purposes"]:
        if p["meaning"]:
            assert "あなた" not in p["meaning"]["text"] and not any(n in p["meaning"]["text"] for n in names.values())


# ── 389 目的の並びはプロフィールの順。成否で変わらない（§7-3）────────────────────────────────────
def test_t389_purpose_order_is_profile_order():
    card, names = _case("2_resonance")                      # 目的1 は not_covered、目的2 は explained
    t = _text(X.render(card, "viewer", names))
    assert t.index("あなたの目的1") < t.index("あなたの目的2")
    card["purposes"].reverse()                               # JSON の並びを入れ替えても、order（プロフィールの順）で並ぶ
    t2 = _text(X.render(card, "viewer", names))
    assert t2.index("あなたの目的1") < t2.index("あなたの目的2")


# ── 390 確かめに落ちた部分は出さない（引用の実在・句の長さ・ラベル・R1/R2 の述語・禁止の語・呼び方・補充しない）（§7-4）─────
@pytest.mark.parametrize("break_it, expect", [
    (lambda p: p["elements"][0].update(offer_quote="原文に無い句を作った"), "引用が原文に無い"),
    (lambda p: p["elements"][0].update(relation="R2"), "R2 なのに申告の表現が無い"),            # 「作ってきた」は経験（R1）
    (lambda p: p["elements"][1].update(relation="R1"), "R1 なのに経験の表現が無い"),
    (lambda p: p["elements"][0].update(need_ref="viewer.need.p2.2-1", need_quote="古い大工道具の手入れ"), "ほかの目的の要素"),
    (lambda p: p["meaning"].update(text="{O}は、相性が良い人で、{N}の店の仕組みづくりに関われる可能性があります。"), "禁止の語"),
    (lambda p: p["meaning"].update(text="{O}は、あなたの店に関われる可能性があります。"), "「あなた」がある"),
    (lambda p: p["meaning"].update(text="{O}は、店を必ず立て直します。"), "閉じた語彙"),
    (lambda p: p["meaning"].update(text="{O}は、3 年で{N}の店の仕組みづくりに関われる可能性があります。"), "数値"),
])
def test_t390_failed_checks_are_not_shown(break_it, expect):
    card, names = _case("1_complement")
    break_it(card["purposes"][0])
    _, probs = X.check(card, names=tuple(names.values()))
    assert any(expect in p for p in probs), probs
    t = _text(X.render(card, "viewer", names))
    assert "原文に無い句" not in t and "相性" not in t and "必ず立て直します" not in t and "3 年で" not in t
    # ① が指す組が落ちると、① の根拠が確かめ済みでなくなるので、その目的の説明は出さない（仕様 §6 の 4）。逆の向きは残る
    assert "あなたの目的1" not in t and "ハル さんから見たあなた" in t
    # 句の長さ: スキーマを通らない長さでも、確かめで落とす（スキーマを通さずに渡された場合）
    card2, _ = _case("1_complement")
    card2["citations"].append({"ref": "target.offer.9", "text": "い" * 50})
    card2["purposes"][0]["elements"][0].update(offer_ref="target.offer.9", offer_quote="い" * 50)
    assert any("40 字を超える" in p for p in X.check(card2)[1])
    # 補完の目的で R4 だけの組なら、その目的は explained にしない
    card3, _ = _case("1_complement")
    for e in card3["purposes"][0]["elements"]:
        e["relation"] = "R4"
    assert any("補完の目的で R4 だけ" in p for p in X.check(card3)[1])


# ── 391 画面2 で保存した表示が、画面3 でそのまま（呼び方の置き換えだけで）出る。再生成しない（§7-5）──────────
def test_t391_screen3_is_saved_screen2_with_names_only():
    card, names = _case("1_complement")
    saved = X.render_symbolic(card, names=tuple(names.values()))     # 画面2 の時点で保存するもの
    screen2 = X.personalize(saved, "viewer", names)
    card["purposes"][0]["meaning"]["text"] = "{O}は、別の内容に関われる可能性があります。"   # 後からカードが変わっても
    screen3 = X.personalize(saved, "recipient", names)               # 保存した HTML だけから描く（カードを読まない）
    back = lambda h, v: h.replace(v, X.V).replace("ハル さん", X.T)    # noqa: E731
    assert back(screen2, "あなた") == back(screen3, "アオイ さん") == saved
    assert "別の内容" not in screen3


# ── 392 0 件・1 件・多件・stale・補足あり・deleted・pending の表示が崩れない（§7-12）───────────────────────
def test_t392_states_render_without_breaking():
    seen = {}
    for c in _fix()["cases"]:
        names = {"viewer": _fix()["people"][c["viewer"]]["name"], "target": _fix()["people"][c["target"]]["name"]}
        h = X.render(c["card"], "viewer", names)
        assert h and _balanced(h), c["case"]
        seen[c["case"]] = _text(h)
    assert "承認時の説明（2026-11-01）" in seen["4_stale_supplemented"]
    assert "ハル さんの力になれること（2）" in seen["4_stale_supplemented"]          # 違う欄は読める形で
    assert "ハル さんの補足（2026-11-05）" in seen["4_stale_supplemented"]
    assert "欠かせない要素（1-1・1-2）に当たる文は、ソラ さんの公開の文の中には見つかりません" in seen["3_sparse"]
    card, names = _case("1_complement")
    card["status"] = "deleted"
    h = X.render(card, "viewer", names)
    assert _text(h).strip() == X.DELETED_TEXT and "在庫" not in h                  # 本文を出さない
    card["status"] = "pending"
    assert X.render(card, "viewer", names) is None                                # 「準備中」と出さない
    card, names = _case("3_sparse")
    card["reverse_purposes"] = []                                                 # どちらの向きにも explained が無い
    assert X.render(card, "viewer", names) is None
    # 相手に目的の欄が無い（v4）ときの ⑥ の見出し
    card, names = _case("1_complement")
    card["target_has_purposes"] = False
    assert "── ハル さんが求めている相手 ──" in _text(X.render(card, "viewer", names))


# ── 393 上層の印を出さない（指示書67 と矛盾しない）・数値を出さない・AI の説明であることと補足の入口（§7-13）─────────
def test_t393_no_upper_badge_no_numbers_and_footer():
    for c in _fix()["cases"]:
        h = X.render(c["card"], "viewer", {"viewer": "見る人", "target": "相手"})
        for bad in ("欠かせない要素をすべて覆う人", "目的のすべてを満たす", "満たせる", "score", "gate"):
            assert bad not in h, (c["case"], bad)
        assert X.FOOTER_TEXT in h and "補足する ›" in h
    src = open(os.path.join(ROOT, "src", "explanation.py"), encoding="utf-8").read()
    assert "anthropic" not in src.lower() and "openai" not in src.lower()          # 段階1は LLM を使わない


# ── 394 補完と共鳴の区別: 共鳴（R4 だけ）は explained で「補完ではない」を出す ───────────────────────────
def test_t394_resonance_labelled_not_complement():
    card, names = _case("2_resonance")
    t = _text(X.render(card, "viewer", names))
    assert "［同じ関心でつながれる（補完ではない）］← 関心が同じ" in t
    assert "［足りない役割を補える］← その役割を担ってきた（経験）" in t           # 逆の向き（R1）


# ── 395 同じ相手を 2 人の見る人から見て、説明が違う（§5-1 の 5）─────────────────────────────────────
def test_t395_same_target_two_viewers_differ():
    a, na = _case("1_complement")
    b, nb = _case("5_same_target_other_viewer")
    assert a["as_of"]["target"] == b["as_of"]["target"]                          # 同じ相手（の同じ版）
    ta, tb = _text(X.render(a, "viewer", na)), _text(X.render(b, "viewer", nb))
    assert ta != tb
    assert "店の仕組みづくり" in ta and "教室の申し込みと会計" in tb
    assert a["output_digest"] != b["output_digest"]


# ── 396 原文を 1 回の操作で開ける（根拠の印）・句は原文の連続した部分 ────────────────────────────────────
def test_t396_quotes_open_original_in_one_step():
    card, names = _case("1_complement")
    h = X.render(card, "viewer", names)
    src = {c["ref"]: c["text"] for c in card["citations"]}
    for e in card["purposes"][0]["elements"]:
        assert e["offer_quote"] in src[e["offer_ref"]]
        assert f'<span class="ex-src-text">{src[e["offer_ref"]]}</span>' in h        # <details> を 1 回開くと原文
    assert h.count("<details") == h.count("</details>") >= 4
