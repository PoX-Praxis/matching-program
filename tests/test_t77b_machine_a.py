"""指示書77b 順1 — 照合の表 v0.3.1-dev ＋ 機械の［A］（a_rules_v1）で v2 を測る。テスト 546〜552。

546 規則の写し・事前登録の写し・お手本の sha256（規則は指示書 §3 のまま）
547 単体の試験: お手本の肯定 7 組で、機械の［A］がお手本の［A］と一字も違わない（① もお手本と同じ）
548 規則 2: 許された丁寧の形だけを常体にする。五段動詞の「〜ました」・並びに無い形は型の ①
549 規則 1・3・4・5・6
550 60 組（参考）: 通った 15 組のうち、生成の ① 13・型の ① 2（S11・S56 は規則 4）
551 v2 の記録: 1 回目が本番・3 回とも同じ・記録が再現する・sha256 の一式・v2 の文が記録と順1 の報告に無い
552 v2 の数と合否（事前登録 §2 の線）
"""
import hashlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

import machine_a as MA  # noqa: E402
import match_table as MT  # noqa: E402
import t77b_measure as M  # noqa: E402

REPORT = os.path.join(ROOT, "docs", "reports", "77b.md")
EXPLORE_MARK = "## 探索"    # 合否を固定した後、KAORU の指示で v2 を探索として開いた節（v2 の文を書いてよい）


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture(scope="module")
def record():
    with open(M.RECORD, encoding="utf-8") as f:
        return json.load(f)


# ── 546 写しと sha256 ───────────────────────────────────────────────────────────────────
def test_t546_copies():
    rules = MA.load_rules()
    doc = open(os.path.join(ROOT, rules["source"]["file"]), encoding="utf-8").read()
    assert rules["source"]["sha256"] == sha(os.path.join(ROOT, rules["source"]["file"]))
    assert rules["version"] == MA.VERSION == "a_rules_v1"
    plain = doc.replace("**", "")
    for r in rules["rules"]:
        assert f"| {r['#']} | {r['規則']} |" in plain, r["#"]
    for v in rules["sentence"].values():
        assert f"：「{v}」" in doc
    assert sha(M.PREREG) == M.PREREG_SHA256 == "17d60c6c14785c12e7b3f41fc2c30fb0df6ef560a83e8efe9b75a515bd2c91a8"
    assert sha(M.EXAMPLES) == "d57d66389ec99410eb1c1a04f9de9f9ea18022ad7809b068fa40c47882807f42"
    assert sha(M.V2_LABELS) == M.V2_LABELS_SHA256


# ── 547 単体の試験 ──────────────────────────────────────────────────────────────────────
def test_t547_examples_exact():
    rows = M.examples_check()
    assert [r["id"] for r in rows] == list(M.POSITIVE)
    for r in rows:
        assert r["表を通った"] and r["offer_quote がお手本と同じ"], r["id"]
        assert [r["関係"], r["C の種類"]] == r["お手本の表"], r["id"]
        assert r["［A］"] == r["お手本の［A］"], r["id"]
        assert r["① がお手本と同じ"], r["id"]
    assert next(r for r in rows if r["id"] == "E01")["変換"] == "〜てきました→〜てきた"


# ── 548 規則 2 ──────────────────────────────────────────────────────────────────────────
PLAIN = {
    "子ども食堂を運営してきました": ("子ども食堂を運営してきた", "〜てきました→〜てきた"),
    "荷物を運んできました": ("荷物を運んできた", "〜てきました→〜てきた"),
    "受付を担当していました": ("受付を担当していた", "〜ていました→〜ていた"),
    "音響を担当しました": ("音響を担当した", "〜しました（サ変）→〜した"),
    "使い方を教えることができます": ("使い方を教えることができる", "〜できます→〜できる"),
    "受付を担当しています": ("受付を担当している", "〜ています→〜ている"),
    "地域の記録に関心があります": ("地域の記録に関心がある", "〜あります→〜ある"),
    "高齢者に使い方を教えました": ("高齢者に使い方を教えた", "一段動詞の〜ました→〜た"),
    "高齢者に使い方を教えます": ("高齢者に使い方を教える", "〜ます（一段・可能動詞）→辞書形"),
    "離れを地域に貸し出せます": ("離れを地域に貸し出せる", "〜ます（一段・可能動詞）→辞書形"),
    "会場の受付を担当します": ("会場の受付を担当する", "〜ます（サ変）→辞書形"),
    "会場の受付を担当してきた": ("会場の受付を担当してきた", "なし（常体のまま）"),
}
TO_TYPE1 = ["手順書を書きました", "手順書を書きます", "違いを感じます", "子ども食堂に来ました", "受付を担当しておりました",
            "使い方を教えることができません"]


@pytest.mark.parametrize("q", sorted(PLAIN))
def test_t548_allowed_conversions(q):
    a = MA.make_a(q)
    assert (a["出し方"], a["A"], a["変換"]) == ("生成", PLAIN[q][0], PLAIN[q][1])


@pytest.mark.parametrize("q", TO_TYPE1)
def test_t548_other_polite_forms_go_to_type1(q):
    a = MA.make_a(q)
    assert a["出し方"] == "型" and a["A"] is None and 2 in a["型の理由"]
    if q == "手順書を書きました":
        assert a["詳しさ"] == "五段動詞の〜ました（音便が要る）"


# ── 549 規則 1・3・4・5・6 ──────────────────────────────────────────────────────────────
def test_t549_rules_1_3_4_5_6():
    assert MA.make_a("職人の作業を記録してきたが、")["A"] == "職人の作業を記録してきた"            # 1
    assert MA.make_a("職人の作業を記録してきた。")["A"] == "職人の作業を記録してきた"
    for q in ("町内会の会計の担当です", "町内会の会計の担当だ", "管理を任される", "照明の操作が好きです"):
        assert MA.make_a(q)["型の理由"][0] == 3, q                                                    # 3
    assert MA.make_a("子どもの本を読んだ")["出し方"] == "生成"                                       # 音便の後の「だ」は「た」
    for q, k in (("使っていない部屋を貸し出せる", "否定"), ("手伝いたいと思っている", "希望"),
                 ("受付を担当してきたらしい", "推量")):
        a = MA.make_a(q)
        assert a["出し方"] == "型" and 4 in a["型の理由"] and k in a["残っていた形"], q            # 4（記録する）
    long_q = "週に一度なら、" + "公民館の" * 9 + "相談会を担当してきた"
    assert len(long_q) > 40 and MA.make_a(long_q)["型の理由"] == [5]                                # 5
    ok = "週に一度なら、公民館で、高齢の方にスマートフォンの使い方を教えることができる"
    assert MA.make_a(ok)["A"] == ok and len(ok) <= 40                                               # 6: 条件を保つ
    a = MA.make_a("平日の午後なら、町工場の職人の作業を、手順書に記録できます")
    assert a["A"] == "平日の午後なら、町工場の職人の作業を、手順書に記録できる"


def test_t549_sentences():
    r = MT.match("地域の子どもの居場所を、運営してきた人。", "団地の集会室で、子ども食堂を三年間運営してきました。",
                 version=M.VERSION)
    gen = MA.sentence(r, MA.make_a(r["clause"]))
    assert gen == "{O}は、団地の集会室で、子ども食堂を三年間運営してきた人です。{N}が求める「地域の子どもの居場所を、運営してきた経験」には、その役割を担ってきた、と書いています。"
    typ = MA.sentence(r, {"出し方": "型"})
    assert typ == MT.type1_sentence(r["need"]["B"], r["clause"], r["short"])


# ── 550 60 組（参考）─────────────────────────────────────────────────────────────────────
def test_t550_dev60(record):
    d = M.dev60()
    assert (d["通った"], d["生成の ①"], d["型の ①"]) == (15, 13, 2)
    typ = {r["id"]: r for r in d["組"] if r["出し方"] == "型"}
    assert sorted(typ) == ["S11", "S56"] and all(r["型の理由"] == [4] for r in typ.values())
    assert record["60 組（参考）"] == d


# ── 551 v2 の記録 ───────────────────────────────────────────────────────────────────────
def test_t551_record_reproduces(record):
    rec, first, items = M.measure()
    assert json.dumps(rec, ensure_ascii=False, sort_keys=True) == json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert record["v2"]["1 回目が本番"] and record["v2"]["3 回とも同じ"]
    assert len(record["v2"]["3 回の sha256"]) == 3 and len(set(record["v2"]["3 回の sha256"])) == 1
    s = record["sha256"]
    assert s["表"] == MT.table_digest(M.VERSION) and s["規則（a_rules_v1）"] == MA.rules_digest()
    assert s["正解（v2_labels.json）"] == M.V2_LABELS_SHA256 and s["事前登録"] == M.PREREG_SHA256
    assert s["お手本（examples_v1.json）"] == sha(M.EXAMPLES) and s["指示書"] == sha(M.INSTRUCTION)
    assert open(M.KAORU_FILE, encoding="utf-8").read() == M.kaoru_file(items, first)


def test_t551_no_v2_text_in_record_or_report():
    items = M.load_v2()
    res = M.run_once(items)
    texts = set()
    for it in items:
        texts |= {it["need"], it["offer"]}
        x = res[it["id"]]
        texts |= {x[k] for k in ("clause", "sentence", "B") if x.get(k)}
        if x.get("a") and x["a"].get("A"):
            texts.add(x["a"]["A"])
    report = open(REPORT, encoding="utf-8").read()
    assert EXPLORE_MARK in report                     # 探索（v2 を開いた後）の節より前が、順1 の報告
    for path, body in ((M.RECORD, open(M.RECORD, encoding="utf-8").read()), (REPORT, report.split(EXPLORE_MARK)[0])):
        assert [t for t in texts if t in body] == [], path


# ── 552 v2 の数と合否 ──────────────────────────────────────────────────────────────────
def test_t552_counts_and_verdict(record):
    c = record["v2"]["数"]
    assert (c["組"], c["通った"], c["画面に出る"]) == (60, 1, 1)
    assert c["線1"]["誤り（機械で数えた分）"] == 0
    assert (c["線2"]["分子"], c["線2"]["分母"]) == (1, 24)
    assert len(c["線2"]["出なかった組"]) == 23
    assert c["線3（記録だけ）"]["生成の ①"] == 1 and c["線3（記録だけ）"]["型の ①"] == 0
    assert {k: v["線2"] for k, v in c["領域ごと（記録だけ）"].items()} == {"一覧の中": "1/12", "一覧の外": "0/12"}
    v = record["合否"]
    assert (v["線1"]["合"], v["線2"]["合"], v["線4"]["合"], v["合否（機械で数えた分）"]) == (True, False, True, "否")
