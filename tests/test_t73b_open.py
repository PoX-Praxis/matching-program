"""指示書73 順3 — 生成の open（目的の状態を 3 つに。報告73 の確認 2026-11-15）。テスト 482〜488（§7 の 1〜5・15 と、版）。

482（§7-1）欠かせない要素が欠け、ほかの要素に確かめ済みの組がある目的が open になる。組が 0 なら not_covered
483（§7-2）補完で R4 だけなら open にならない。モデルが not_covered と言った目的は open にならない
484（§7-3）open の目的が段2 に渡り、① が出る。not_covered の目的は段2 に渡らない。段2 に原文が渡らない
485（§7-4）open の ① に「満たせる」があれば落ちる（生成では not_covered に戻る＝空を返す側に倒す）
486（§7-5）open だけのカードが表示される。not_covered だけのカードは表示されない
487（§7-15）計測の「出した」が explained か open。72 の形（1 つの必要 × 1 つの相手の文）で open が起きない
488 版（スキーマ expl-v0.7・段1 expl-p1-v0.4・fixed-rule-v2・レンダラ v0.8 のまま）・合意の記録に open の組・表示
架空の文だけを使う（KAORU・PA の文は使わない）。
"""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

import explanation as X  # noqa: E402
import explanation_gen as G  # noqa: E402

NEED_A = "売上の記録を続けてきた人。"          # 欠かせない要素（応える句がある）
NEED_B = "店主と毎週話し合える人。"            # 欠かせない要素（応える句が無い）
OFFER = "売上の記録を続けてきた。"
TITLE = "店の仕組みをつくりたい"


def _n(kind="補完", musts=(True, True)):
    return {"subject": "n", "purposes": [{"purpose_id": "p1", "title": TITLE, "means": "", "kind": kind,
                                          "needs": [{"text": NEED_A, "type": "関わり方", "must": musts[0]},
                                                    {"text": NEED_B, "type": "関わり方", "must": musts[1]}]}]}


def _o(interest=None):
    return {"subject": "o", "offers": [{"text": OFFER, "type": ""}], "state": {}, "interests": [interest] if interest else []}


def _responder(elements=True, status="explained", relation="R1", meaning=None):
    """段1 は決まった組（必要 1-1 × 与え像 1）。段2 は規則の型の ①（meaning を渡せば、その文）。"""
    def respond(stage, payload):
        if stage == 1:
            els = []
            if elements:
                offer_ref, quote = ("O.interest.1", "売上の記録") if relation == "R4" else ("O.offer.1", "売上の記録を続けてきた")
                els = [{"need_ref": "N.need.p1.1-1", "need_quote": "売上の記録を続けてきた", "offer_ref": offer_ref,
                        "offer_quote": quote, "relation": relation}]
            return {"purposes": [{"ref": "N.purpose.p1", "status": status, "elements": els, "unknown": [],
                                  "missing_must": ["N.need.p1.1-2"]}]}
        out = G.rule_responder(2, payload)
        if meaning:
            for m in out["meanings"]:
                m["text"] = meaning
        return out
    return respond


def _gen(responder, n=None, o=None, trace=None):
    return G.generate_direction(n or _n(), o or _o(), G.FixedBackend(responder), trace=trace)


def _purpose(res):
    return res["direction"]["purposes"][0]


# ── 482（§7-1）open になる・組が 0 なら not_covered ─────────────────────────────────────────
def test_t482_open_when_a_must_is_missing_but_pairs_exist():
    p = _purpose(_gen(_responder()))
    assert p["status"] == "open" and p["missing_must"] == ["N.need.p1.1-2"]
    assert p["meaning"] and p["meaning"]["text"].startswith("{O}は、") and len(p["elements"]) == 1   # 覆われた分だけ ①
    p = _purpose(_gen(_responder(elements=False, status="not_covered")))
    assert p["status"] == "not_covered" and p["meaning"] is None
    assert p["missing_must"] == ["N.need.p1.1-1", "N.need.p1.1-2"]                                  # 分からないことには出る
    p = _purpose(_gen(_responder(), n=_n(musts=(True, False))))                                   # 欠けが無い → explained
    assert p["status"] == "explained" and p["missing_must"] == []


# ── 483（§7-2）補完で R4 だけ・モデルが not_covered → open にならない ─────────────────────────────────
def test_t483_r4_only_and_model_not_covered_do_not_open():
    p = _purpose(_gen(_responder(relation="R4"), o=_o(interest="売上の記録")))
    assert p["status"] == "not_covered" and p["meaning"] is None                                   # 補完で R4 だけ
    p = _purpose(_gen(_responder(relation="R4"), n=_n(kind="共鳴"), o=_o(interest="売上の記録")))
    assert p["status"] == "open"                                                                    # 共鳴なら R4 で open
    p = _purpose(_gen(_responder(status="not_covered")))
    assert p["status"] == "not_covered" and p["meaning"] is None                                   # モデルは落とす方向にだけ


# ── 484（§7-3）段2 に open が渡り、not_covered は渡らない。原文は渡らない ─────────────────────────────────
def test_t484_stage2_gets_open_but_no_source_text():
    trace = []
    _gen(_responder(), trace=trace)
    p2 = trace[0]["stage2_payload"]
    assert [x["purpose_ref"] for x in p2["purposes"]] == ["N.purpose.p1"]
    blob = json.dumps(p2, ensure_ascii=False)
    assert NEED_B not in blob and "店主" not in blob and NEED_A not in blob and OFFER not in blob   # 原文・欠けた要素の文は渡さない
    import explanation_measure as M
    res = _gen(_responder())
    assert M.leak_violations(res["direction"], p2) == 0
    trace = []
    _gen(_responder(elements=False, status="not_covered"), trace=trace)
    assert trace[0]["stage2_payload"]["purposes"] == []                                          # not_covered は渡らない


# ── 485（§7-4）open の ① に「満たせる」 → 落ちる ─────────────────────────────────────────────
def test_t485_forbidden_word_in_open_meaning_falls_back():
    bad = f"{{O}}は、売上の記録を続けてきた人で、{{N}}の{TITLE}を満たせる可能性があります。"
    res = _gen(_responder(meaning=bad))
    p = _purpose(res)
    assert p["status"] == "not_covered" and p["meaning"] is None                                   # 空を返す側に倒す
    assert any("満たせる" in x or "禁止の語" in x for x in res["problems"])
    card = X.assemble(_gen(_responder())["direction"], None, True)                                # 確かめ（check）でも落ちる
    card["purposes"][0]["meaning"]["text"] = bad.replace("{N}", "{N}").replace("{O}", "{O}")
    checked, problems = X.check(card)
    assert checked["purposes"] == [] and any("禁止の語" in x for x in problems)


# ── 486（§7-5）open だけのカードは出す・not_covered だけのカードは出さない ───────────────────────────────
def test_t486_showable_with_open_only():
    card = X.assemble(_gen(_responder())["direction"], None, True)
    assert [p["status"] for p in card["purposes"]] == ["open"] and card["status"] == "explained"
    assert X.validate(card)[0] is True                                                             # スキーマ v0.7 に通る
    assert X.showable(X.check(card)[0]) is True
    h = X.render(card, "viewer", {"viewer": "アオイ", "target": "ハル"})
    assert 'class="ex-meaning"' in h and X.STATE_TEXT["part"] in h and X.ASK_HEAD in h             # ①・一部・問い
    assert "「店主と毎週話し合える人。」について、経験はありますか。" in h
    short = X.short_form(card)
    assert short and "経験はありますか" not in json.dumps(short, ensure_ascii=False)                  # 一覧に ① は出す・問いは出さない
    nc = X.assemble(_gen(_responder(elements=False, status="not_covered"))["direction"], None, True)
    assert X.showable(X.check(nc)[0]) is False and X.render(nc, "viewer", {"viewer": "a", "target": "b"}) is None


# ── 487（§7-15）計測の「出した」は explained か open。72 の形では open が起きない ─────────────────────────
def test_t487_measure_counts_open_separately():
    import explanation_measure as M
    one = M.bench_counts(G.FixedBackend(), "one")
    whole = M.bench_counts(G.FixedBackend(), "whole")
    assert one["n"] == whole["n"] == 60
    assert one["open"] == 0                                                                        # 1 つの必要だけでは起きない
    assert whole["open"] >= 1 and whole["shown"] >= one["shown"] - 60                              # まるごとの形では起きうる（分けて書く）
    res, shown, _ = M.run_one(_n(), _o(), G.FixedBackend(_responder()), __import__("collections").Counter(),
                              __import__("collections").Counter())
    assert shown is True and M.open_count(res["direction"]) == 1                                  # open だけでも「出した」
    md = M.to_markdown({"backend": "fixed", "model": "m", "revision": None, "seconds": 0, "labeled_neg": None,
                        "labeled_pos": None, "upper_neg": None, "hn_shown": {"shown": 1, "n": 2, "open": 1}, "c_shown": None,
                        "bench_one_need": one, "bench_whole": whole, "drops": {}, "stage_drops": {},
                        "determinism": {"same": 0, "runs": 0}, "leak": {"violations": 0, "runs": 0}, "exemplar": None})
    assert "うち open の目的" in md and "1 つの必要 × 1 つの相手の文" in md


# ── 488 版・合意の記録・表示 ────────────────────────────────────────────────────────────
def test_t488_versions_agreement_and_display(monkeypatch):
    monkeypatch.setenv("POX_EXPLANATION_DIGEST_KEY", "k" * 40)
    assert X.SCHEMA_VERSION == "expl-v0.7" and X.RENDER_VERSION == "v0.8"                          # レンダラは上げない
    assert G.PROMPT1_VERSION == "expl-p1-v0.4" and G.PROMPT2_VERSION == "expl-p2-v0.3"
    text = open(os.path.join(ROOT, "docs", "prompts", "explanation_p1_v0.4.txt"), encoding="utf-8").read()
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == G.PROMPT1_SHA == \
        "c222f021707e8ab92f976d328ce7dbb56aa01051ef564863b768e6bda4a7f252"
    assert "その目的に組が1つも無いときだけ、status を \"not_covered\" にする。" in text
    assert os.path.exists(os.path.join(ROOT, "docs", "prompts", "explanation_p1_v0.3.txt"))     # 前の版は残す
    assert G.FixedBackend().model == "fixed-rule-v2"
    assert '"pending", "explained", "open", "not_covered"' in json.dumps(X.schema(), ensure_ascii=False)   # スキーマ v0.7
    fx = json.load(open(os.path.join(ROOT, "tests", "fixtures", "explanation_v0_4.json"), encoding="utf-8"))
    assert X.validate(fx["cases"][0]["card"])[0] is True                                          # v0.4 のカードは v0.4 のスキーマで
    # fixed の段1: 組が無かった欠かせない要素を missing_must に入れる（status は組があれば explained のまま）
    ns, os_ = G.need_side(_n()), G.offer_side(_o())
    out = G.rule_responder(1, {"N": ns["block"], "O": os_["block"]})
    assert out["purposes"][0]["missing_must"] and out["purposes"][0]["status"] in ("explained", "not_covered")
    # 合意の記録（a1）に、open の目的の組を explained と同じ形で入れる（確認 1）。問い・分からないことは入れない
    import explanation_agreement as XA
    card = X.assemble(_gen(_responder())["direction"], None, True)
    a1 = XA.a1_object(card, "n", "o")
    assert len(a1["purposes"]) == 1 and len(a1["purposes"][0]["pairs"]) == 1
    blob = json.dumps(a1, ensure_ascii=False)
    assert "経験はありますか" not in blob and NEED_B not in blob
    # 並び: explained → open → not_covered（指示書74 §2。生成の open も同じ段）
    two = copy.deepcopy(card)
    q = copy.deepcopy(two["purposes"][0])
    q.update(ref="viewer.purpose.p2", order=1, number=2, status="explained", missing_must=[])
    q["elements"] = [dict(e, need_ref=e["need_ref"].replace("p1.1-1", "p2.2-1")) for e in q["elements"]]
    two["citations"].append({"ref": "viewer.need.p2.2-1", "text": NEED_A})
    two["purposes"].append(q)
    h = X.render(two, "viewer", {"viewer": "アオイ", "target": "ハル"})
    assert h.index("■ あなたの目的2") < h.index("■ あなたの目的1")
