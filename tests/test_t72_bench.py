"""指示書72 — 説明の基準テスト（物差し 2 つ・計測の形・R1/R2 の述語 rel-v0.4）。テスト 498〜506。

498（§5-1）expl_label を読み、説明できない・説明できる・判断できない・対象外を分けて数える（誤り・拾えた・規則で出さない・関係の一致）
499（§5-2）架空の組がリポジトリから読め、KAORU の組が無いときは架空の組だけで動く。物差しの sha256 を書く
500（§5-3）1 回ごとの結果の種類（a〜d）が組ごとに記録され、文は記録されない
501（§5-4）R1 の述語: 経験の文末は通り、願望・思い・変化・続いていた状態・関係の無い過去は通らない
502（§5-5）R2 の述語: 丁寧の形は通り、願望・言い切らない形は通らない
503 版（rel-v0.4・fixed-rule-v3・スキーマ v0.7 は rel の両方を受け付ける）と、仕様 §14 の述語がコードと同じ
504 指示書72 の形（1 つの必要 × 1 つの相手の文）では open が起きない（指示書73 の open。報告73 のとおり）
505 決まった規則の応答役は確かめと同じ述語でラベルを付け、広げた形の R1・R2 が最後まで通る
506 KAORU の 48 組の入力の作り方（報告72 §3-1。架空のデータで確かめる）
"""
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

import explanation as X  # noqa: E402
import explanation_gen as G  # noqa: E402
import explanation_measure as M  # noqa: E402

SYN_SHA = "018235232543a730603eaad00b43a4b1afe3de413bf76247ccd651f8003eea37"
LAB_SHA = "e81c27467ed6287cf359e0a4a87685213d14bc6d68e3545e8b9f64c404fb3c82"
TITLE = "地域の小さな店が続けられる仕組みをつくりたい"
NEED = "売上や在庫の記録を、店の人と一緒に担ってきた人。"


def _syn():
    with open(M.BENCH, encoding="utf-8") as f:
        return json.load(f)


def _n(kind="補完", must=True, need=NEED):
    return {"subject": "n", "purposes": [{"purpose_id": "p1", "title": TITLE, "means": "", "kind": kind,
                                          "needs": [{"text": need, "type": "", "must": must}]}]}


def _responder(offer_quote=None, relation="R1", raw=None, elements=True, need_quote="売上や在庫の記録"):
    """段1 は決まった組を返す（offer_quote が None なら相手の文の末尾）。段2 は規則の応答役のまま。"""
    def run(stage, payload):
        if stage == 2:
            return G.rule_responder(stage, payload)
        if raw is not None:
            return raw
        p = payload["N"]["purposes"][0]
        o = (payload["O"]["与え像"] + payload["O"]["現状"] + payload["O"]["関心"])[0]
        els = [{"need_ref": p["必要像"][0]["ref"], "must": p["必要像"][0]["必須"], "need_quote": need_quote,
                "offer_ref": o["ref"], "offer_quote": offer_quote or G._tail(o["文"]), "relation": relation}] if elements else []
        return {"purposes": [{"ref": p["ref"], "status": "explained" if els else "not_covered", "elements": els,
                              "unknown": [], "missing_must": [p["必要像"][0]["ref"]] if elements else []}]}
    return run


def _pair(pid, label, other, need=NEED, must=True):
    return {"id": pid, "need": need, "must": must, "other": other, "expl_label": label}


# ── 498（§5-1）ラベルを分けて数える ─────────────────────────────────────────────────────
def test_t498_classes_and_counts():
    assert M.label_class(["R1"]) == M.label_class(["R4", "R1"]) == "説明できる"
    assert M.label_class(["説明できない"]) == "説明できない"
    assert M.label_class(["判断できない"]) == "判断できない" and M.label_class(["対象外"]) == "対象外"
    assert M.label_class([]) == M.label_class(["そのほか"]) == "読めない"
    shown_r1 = "店の売上や在庫の記録を、三年担当してきました。"
    items = [
        (_pair("T1", ["R1"], shown_r1), _n(), M.one_sentence_person(shown_r1)),                       # 拾えた・一致
        (_pair("T2", ["R2"], shown_r1), _n(), M.one_sentence_person(shown_r1)),                       # 拾えた・一致しない
        (_pair("T3", ["説明できない"], shown_r1), _n(), M.one_sentence_person(shown_r1)),               # 誤り
        (_pair("T4", ["R4"], shown_r1), _n(kind="補完"), M.one_sentence_person(shown_r1)),             # 規則で出さない
        (_pair("T5", ["判断できない"], shown_r1), _n(), M.one_sentence_person(shown_r1)),               # 生成しない
        (_pair("T6", ["対象外"], shown_r1), None, M.one_sentence_person(shown_r1)),                     # 生成しない
        (_pair("T7", ["R1"], shown_r1), None, M.one_sentence_person(shown_r1)),                       # 入力が作れない
        (_pair("T8", ["R4", "R1"], shown_r1), _n(), M.one_sentence_person(shown_r1)),                 # R1 なら出せる
    ]
    out = M.bench_measure(items, G.FixedBackend(_responder()))
    c = out["counts"]
    assert c["pairs"] == 8 and c["classes"] == {"説明できる": 5, "説明できない": 1, "判断できない": 1, "対象外": 1}
    assert c["errors"] == {"shown": 1, "n": 1}                                                       # 説明できないで出した
    assert c["rule_excluded"] == {"n": 1, "shown": 1}                                               # 補完で R4 だけ → 分母から外す
    assert c["recall"] == {"shown": 3, "n": 3}                                                       # T1・T2・T8（T4・T7 は入らない）
    assert c["relation_match"] == {"same": 2, "shown": 4}                                            # T2（R2）・T4（R4）は R1 を出したので一致しない
    assert c["by_relation"]["R1"] == {"shown": 2, "n": 2} and c["by_relation"]["R2"] == {"shown": 0, "n": 1}
    assert c["by_relation"]["R4"] == {"shown": 0, "n": 1}
    assert c["not_built"] == 1 and c["kinds"]["d"] == 5
    by_id = {r["id"]: r for r in out["records"]}
    assert by_id["T5"]["result"] is None and by_id["T6"]["result"] is None and by_id["T7"]["result"] == "入力が作れない"
    assert by_id["T4"]["rule_excluded"] is True and by_id["T1"]["rule_excluded"] is False
    # 補完で R4 の組を出すと、機械の状態で出さない → (c) に規則の理由が付く（この組は「規則で出さない」に数える）
    out = M.bench_measure([(_pair("T9", ["R4"], shown_r1), _n(), M.one_sentence_person(shown_r1))],
                          G.FixedBackend(_responder(relation="R4")))
    r = out["records"][0]
    assert r["result"] == "c" and M.R4_RULE in r["reasons"] and out["counts"]["rule_excluded"] == {"n": 1, "shown": 0}
    assert out["counts"]["recall"]["n"] == 0


# ── 499（§5-2）架空の組はリポジトリから読め、KAORU の組が無ければ架空の組だけで動く ──────────────────────────
def test_t499_synthetic_only_when_labels_missing(tmp_path):
    assert M.sha256_file(M.BENCH) == SYN_SHA
    syn = _syn()
    assert len(syn["pairs"]) == 60 and len(syn["people"]) == 6
    items = list(M.synthetic_items(syn))
    assert len(items) == 60
    res = M.main(["--backend", "fixed", "--labels", str(tmp_path / "none.json"), "--kaoru", str(tmp_path / "none.json"),
                  "--upper-neg", str(tmp_path / "none.json"), "--exemplar", str(tmp_path / "none.json"),
                  "--out", str(tmp_path)])
    assert list(res["bench"]) == ["synthetic_v1"] and res["bench"]["synthetic_v1"]["sha256"] == SYN_SHA
    c = res["bench"]["synthetic_v1"]["counts"]
    assert c["pairs"] == 60 and c["classes"] == {"説明できる": 28, "説明できない": 25, "判断できない": 7, "対象外": 0}
    assert c["rule_excluded"]["n"] == 4 and c["recall"]["n"] == 24                                   # S13・S26・S46・S56 を外す
    assert sum(c["kinds"].values()) == 53                                                           # 判断できない 7 は生成しない
    md = (tmp_path / "explanation_measure_fixed.md").read_text(encoding="utf-8")
    assert SYN_SHA in md and "KAORU の 48 組は手元に無い" in md and "| 架空の 60 組 | 60 | 28 | 25 | 7 | 0 |" in md
    assert "応える" not in md                                                                         # 前の 48 対の数え方は無い
    for k in ("upper_neg", "hn_shown", "c_shown", "determinism", "leak", "exemplar", "bench_whole"):
        assert k in res                                                                             # 既存の数は残す
    if os.path.exists(M.LABELS):                                                                    # 手元にあるときだけ（CI には無い）
        assert M.sha256_file(M.LABELS) == LAB_SHA


# ── 500（§5-3）1 回ごとの結果の種類（a〜d）・文は記録しない ──────────────────────────────────────
def test_t500_result_kinds_and_no_text(tmp_path):
    o = "店の売上や在庫の記録を、三年担当してきました。"
    cases = {"a": _responder(raw="モデルの答えではない文"), "b": _responder(raw={"purposes": []}),
             "c": _responder(offer_quote="三年担当してきました", relation="R2"),                       # R2 の述語に落ちる
             "d": _responder()}
    for kind, resp in cases.items():
        r = M.run_pair(_n(), M.one_sentence_person(o), G.FixedBackend(resp))
        assert r["result"] == kind, (kind, r)
        assert set(r) == {"result", "reasons", "relations", "stage2", "open", "leak"}
        assert r["stage2"] is (kind == "d") and r["open"] == 0 and r["leak"] == 0
        if kind == "c":
            assert r["reasons"] == ["R2 なのに申告の表現が無い"]
        if kind == "d":
            assert r["relations"] == ["R1"] and r["reasons"] == []
    # 理由の取り出し: 括弧の中を 1 つずつ。「連絡先・URL」は 1 つの理由のまま。番号・参照は残さない
    assert M.drop_reasons(["N.purpose.p1: 組 3 を出さない（句に連絡先・URL がある・引用が原文に無い）"]) == \
        ["句に連絡先・URL がある", "引用が原文に無い"]
    assert M.drop_reasons(["N.purpose.p1: ① が無い"]) == ["① が無い"]
    # 記録・Markdown に文が入らない（架空の組で全体を走らせる）
    res = M.main(["--backend", "fixed", "--labels", str(tmp_path / "none.json"), "--kaoru", str(tmp_path / "none.json"),
                  "--out", str(tmp_path)])
    blob = (tmp_path / "explanation_measure_fixed.json").read_text(encoding="utf-8") + \
        (tmp_path / "explanation_measure_fixed.md").read_text(encoding="utf-8")
    syn = _syn()
    for t in [p["need"] for p in syn["pairs"]] + [p["other"] for p in syn["pairs"]] + [p["purpose"] for p in syn["people"]]:
        assert not any(t[i:i + 8] in blob for i in range(max(1, len(t) - 7))), t[:8]
    recs = res["bench"]["synthetic_v1"]["records"]
    assert len(recs) == 60 and {r["id"] for r in recs} == {p["id"] for p in syn["pairs"]}
    assert all(set(r) <= {"id", "class", "result", "rule_excluded", "reasons", "relations", "stage2", "open", "leak"}
               for r in recs)


# ── 501（§5-4）R1 の述語 ────────────────────────────────────────────────────────────
R1_PASS = ["担当してきた", "担当してきました", "担当しました", "学んできた", "取り組んできました", "全国に広げた。", "店を作った。",
           "リーダーになりました", "十年、仕込みを担当した、", "仕込みを担当した"]
R1_FAIL = ["担当したい", "好きだった", "好きでした", "楽しかった。", "教えたいと思いました", "考えるようになりました",
           "使うようになった。", "図書館に逃げ込んでいた。", "担当していた。", "担当していました。", "逃げ込んでいました",
           "担当しています", "担当したいと思った。"]


def test_t501_r1_predicate():
    for s in R1_PASS:
        assert X.R1_PRED.search(s), s
    for s in R1_FAIL:
        assert not X.R1_PRED.search(s), s


# ── 502（§5-5）R2 の述語 ────────────────────────────────────────────────────────────
R2_PASS = ["手伝えます", "できます", "関われます", "週末なら手伝える", "記帳ならできる", "立場で関われる"]
R2_FAIL = ["できるようになりたい", "関われるかもしれない", "手伝えるかもしれません", "できません", "できない", "関われない"]


def test_t502_r2_predicate():
    for s in R2_PASS:
        assert X.R2_PRED.search(s), s
    for s in R2_FAIL:
        assert not X.R2_PRED.search(s), s


# ── 503 版と仕様 §14 ──────────────────────────────────────────────────────────────
def test_t503_versions_and_spec():
    assert X.RELATION_RULE_VERSION == "rel-v0.4" and G.FixedBackend().model == "fixed-rule-v3"
    assert G.versions(G.FixedBackend())["rel"] == "rel-v0.4"
    assert X.SCHEMA_VERSION == "expl-v0.7" and X.RENDER_VERSION == "v0.8"                        # スキーマ・レンダラは上げない
    rel = X.schema()["properties"]["relation_rule_version"]
    assert rel == {"enum": ["rel-v0.3", "rel-v0.4"]}                                                # v0.3 で作ったカードも読める
    fx = json.load(open(os.path.join(ROOT, "tests", "fixtures", "explanation_v0_4.json"), encoding="utf-8"))
    for c in fx["cases"]:
        assert X.validate(c["card"])[0] is True                                                     # 保存済みの形（rel-v0.3）は通る
    spec = open(os.path.join(ROOT, "docs", "explanation_spec_v0.4.md"), encoding="utf-8").read()
    sec = spec[spec.index("## 14. v0.6 の追記"):spec.index("## 15. v0.7 の追記")]
    code = sec[sec.index("```python"):sec.index("```", sec.index("```python") + 3)]
    ns = {"re": re}
    exec(code.replace("```python", ""), ns)                                                         # 仕様の述語をそのまま読む
    assert ns["R1_PRED"].pattern == X.R1_PRED.pattern and ns["R2_PRED"].pattern == X.R2_PRED.pattern
    assert "rel-v0.4" in sec and "fixed-rule-v3" in sec and SYN_SHA in sec and LAB_SHA in sec
    src = open(os.path.join(ROOT, "src", "explanation.py"), encoding="utf-8").read()
    assert src.count("R1_PRED = re.compile(") == 1 and src.count("R2_PRED = re.compile(") == 1     # 述語は 1 か所だけ
    gen = open(os.path.join(ROOT, "src", "explanation_gen.py"), encoding="utf-8").read()
    assert "_R1_END = X.R1_PRED" in gen and "_R2_END = X.R2_PRED" in gen


# ── 504 指示書72 の形では open が起きない ─────────────────────────────────────────────────
def test_t504_no_open_in_one_need_form():
    items = list(M.synthetic_items(_syn()))
    assert all(len(n["purposes"]) == 1 and len(n["purposes"][0]["needs"]) == 1 for _, n, _ in items)
    out = M.bench_measure(items, G.FixedBackend())
    assert out["counts"]["open"] == 0 and all(r.get("open", 0) == 0 for r in out["records"])
    # モデルが欠けを言っても、必要が 1 つなら、覆えば explained・覆わなければ not_covered（機械が数え直す）
    o = M.one_sentence_person("店の売上や在庫の記録を、三年担当してきました。")
    for must in (True, False):
        res = G.generate_direction(_n(must=must), o, G.FixedBackend(_responder()))
        assert [p["status"] for p in res["direction"]["purposes"]] == ["explained"]
        res = G.generate_direction(_n(must=must), o, G.FixedBackend(_responder(elements=False)))
        assert [p["status"] for p in res["direction"]["purposes"]] == ["not_covered"]
    # 人の必要像まるごとの形（指示書73）は別に数える（そこでは起きうる）
    assert M.bench_counts(G.FixedBackend(), "whole")["open"] >= 1
    if os.path.exists(M.LABELS) and os.path.exists(os.path.join(ROOT, "local", "kaoru_v5_public.json")):
        labels = json.load(open(M.LABELS, encoding="utf-8"))                                       # 手元にあるときだけ
        kaoru = json.load(open(os.path.join(ROOT, "local", "kaoru_v5_public.json"), encoding="utf-8"))
        out = M.bench_measure(list(M.kaoru_items(labels, kaoru)), G.FixedBackend())
        assert out["counts"]["open"] == 0 and out["counts"]["not_built"] == 0


# ── 505 規則の応答役は確かめと同じ述語。広げた形が最後まで通る ────────────────────────────────────────
def test_t505_fixed_uses_check_predicates():
    assert G._R1_END is X.R1_PRED and G._R2_END is X.R2_PRED
    for offer, rel in (("店の売上や在庫の記録を、三年担当しました。", "R1"),            # rel-v0.3 では落ちた形
                       ("売上や在庫の記録なら、週末に手伝えます。", "R2")):
        res = G.generate_direction(_n(), M.one_sentence_person(offer), G.FixedBackend())
        p = res["direction"]["purposes"][0]
        assert p["status"] == "explained" and [e["relation"] for e in p["elements"]] == [rel], (offer, res["problems"])
        assert X.showable(X.check(X.assemble(res["direction"], None, True))[0])
    for offer in ("店の売上や在庫の記録を、担当していました。", "売上や在庫の記録を、いつか手伝えるかもしれません。"):
        res = G.generate_direction(_n(), M.one_sentence_person(offer), G.FixedBackend())
        assert res["direction"]["purposes"][0]["status"] == "not_covered", offer          # 述語に合わない形は組にしない


# ── 506 KAORU の 48 組の入力の作り方（架空のデータで）─────────────────────────────────────────
def test_t506_kaoru_inputs():
    kaoru = {"purposes": [
        {"purpose_id": "p1", "向かう先": "見出し一", "手段": "手段一", "必要像": [
            {"文": "必要一の一", "型": "関わり方", "必須": True}, {"文": "必要一の二", "型": "関心", "必須": False}]},
        {"purpose_id": "p2", "向かう先": "見出し二", "手段": "手段二", "必要像": [
            {"文": "必要二の一", "型": "関わり方", "必須": False}, {"文": "必要二の二", "型": "関心", "必須": False}]}]}
    n = M.kaoru_need_person({"need_key": "1-2", "need": "必要一の二"}, kaoru)["purposes"][0]
    assert n["title"] == "見出し一" and n["means"] == "" and n["kind"] == "補完"                      # 目的全体の規則（欠かせない要素がある）
    assert n["needs"] == [{"text": "必要一の二", "type": "関心", "must": False}]
    n = M.kaoru_need_person({"need_key": "2-2", "need": "必要二の二"}, kaoru)["purposes"][0]
    assert n["kind"] == "共鳴" and n["needs"][0]["must"] is False                                    # 欠かせない要素が無く関心がある
    n = M.kaoru_need_person({"need_key": "PA", "need": "v4 の必要"}, None)["purposes"][0]
    assert n["title"] == "" and n["kind"] == "補完" and n["needs"] == [{"text": "v4 の必要", "type": "", "must": True}]
    assert M.kaoru_need_person({"need_key": "1-1", "need": "違う文"}, kaoru) is None                  # 文が合わなければ作らない
    assert M.kaoru_need_person({"need_key": "3-1", "need": "必要一の一"}, kaoru) is None
    assert M.kaoru_need_person({"need_key": "1-1", "need": "必要一の一"}, None) is None              # JSON が無ければ作らない
    o = M.other_person({"other_kind": "PA の現状（全文）",
                        "other": "持っているもの: 道具 / 動き方の型: 週末に動く / 縛られているもの: 平日"})
    assert o["state"] == {"持っているもの": "道具", "できること_型": "週末に動く"} and o["offers"] == []  # 縛られているものは使わない
    o = M.other_person({"other_kind": "関心の文（関心型の必要像 × 相手の関心）", "other": "関心の文"})
    assert o["interests"] == ["関心の文"] and o["offers"] == []
    o = M.other_person({"other_kind": "C 役割が合う（新しい会話が書いた文）", "other": "与え像の文"})
    assert o["offers"] == [{"text": "与え像の文", "type": ""}]
    labels = {"pairs": [{"id": "Q1", "need_key": "1-1", "need": "必要一の一", "other_kind": "C", "other": "x",
                         "expl_label": ["R1"]},
                        {"id": "Q2", "need_key": "1-1", "need": "必要一の一", "other_kind": "C", "other": "x",
                         "expl_label": ["対象外"]}]}
    items = list(M.kaoru_items(labels, kaoru))
    assert items[0][1] is not None and items[1][1] is None                                           # 対象外は作らない
    assert Counter(M.label_class(p["expl_label"]) for p, _, _ in items) == Counter({"説明できる": 1, "対象外": 1})
