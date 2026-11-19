"""指示書78 順1 — 表 v0.3.2-dev（機械の読み M1〜M4・F1）。テスト 554〜557。

554 表 v0.3.2-dev は文書の写し。v0.3.1-dev から変わったのは、関心の形（F1）と readings.json（§0''' の写し）だけ
555 機械の読み M1〜M4・F1（文法の形だけ）。v0.3.1-dev では前のまま
556 v0.3.1-dev は凍結のまま（60 組と gates の結果が v0.3.2-dev と同じ組も含めて、前の記録が変わらない）
557 記録（runs/78_match_tables_v0.3.2-dev.json）が再現する・数
"""
import hashlib
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import match_table as MT  # noqa: E402
import t78_measure as T78  # noqa: E402
import test_t77a_match_table as T515  # noqa: E402
import test_t77a_s1_match_table_v03 as T527  # noqa: E402

V, OLD = "v0.3.2-dev", "v0.3.1-dev"
DOC = os.path.join(ROOT, "docs", "match_tables_v0.3.2-dev.md")


def run(need, offer, version=V):
    return MT.match(need, offer, version=version)


def kind_of(text, version=V):
    tok = MT._VER.set(version)
    try:
        toks = MT.tokenize(text)
        return MT.clause_kind(MT.analyze_pred(toks, text), MT.Span(toks))[0]
    finally:
        MT._VER.reset(tok)


# ── 554 表は文書の写し ───────────────────────────────────────────────────────────────────
def test_t554_tables_are_a_copy_of_the_document():
    doc = open(DOC, encoding="utf-8").read()
    man = json.load(open(os.path.join(MT.TABLE_ROOT, V, "manifest.json"), encoding="utf-8"))
    for k in ("表", "原則"):
        f = man["source"][k]["file"]
        assert man["source"][k]["sha256"] == hashlib.sha256(open(os.path.join(ROOT, f), "rb").read()).hexdigest()
    assert sorted(man["files"]) == sorted(MT.table_files(V)) and "readings.json" in man["files"]
    assert "readings.json" not in MT.table_files(OLD)
    new, old = MT.load_tables(V), MT.load_tables(OLD)
    for f in old:
        a, b = dict(old[f]), dict(new[f])
        a.pop("source", None)
        b.pop("source", None)
        if f == "clause_kinds":
            ka = {k["種類"]: k for k in a.pop("kinds")}
            kb = {k["種類"]: k for k in b.pop("kinds")}
            assert {k: v for k, v in ka.items() if k != "関心"} == {k: v for k, v in kb.items() if k != "関心"}
            assert kb["関心"]["match"][:-1] == ka["関心"]["match"]
            assert kb["関心"]["match"][-1] == {"head": ["関心がある"], "contains": "に関心を", "chain": ["て", "いる"]}
            assert kb["関心"]["形"] in doc
        assert a == b, f
    # 文書の §0''' の表と、readings.json の読みの名前
    sec = doc[doc.index("## 0'''"):doc.index("## 0''. ")]
    for r in new["readings"]["readings"]:
        assert f"| {r['#']} | {r['読み']} |" in sec, r["#"]
    # 日本語の欄は文書のまま（537 と同じ照らし合わせ）
    pri = open(os.path.join(ROOT, "docs", "answer_principle_v1.md"), encoding="utf-8").read()
    src = doc + pri
    src = src + src.replace("**", "")
    missing = []
    for name, t in new.items():
        if name in ("nominalize", "readings"):
            continue
        for s in T515._strings(t):
            s2 = s.strip("| ").replace(" | ", "|")
            for p in [p for p in re.split(r"[|]", s2) if p and p != "—"]:
                if p not in src and p.replace("（", "").replace("）", "") not in src:
                    missing.append((name, p))
        for w in T515._match_words(t):
            if w not in src and w not in T527.READINGS_V03:
                missing.append((name, "match:" + w))
    allowed = {"所有", "使える立場", "応えない", "v1.0 では応えない", "主題", "物・機材", "期間", "型なし（照合しない）"}
    assert [(n, p) for n, p in missing if p not in allowed and not p.startswith("docs/")] == []
    assert V in MT.VERSIONS and MT._VER.get() == "v0.2"


# ── 555 機械の読み ──────────────────────────────────────────────────────────────────────
TEACH_NEED = "高齢の方に、機器の使い方を教えてきた人。"


def test_t555_m1_ichidan_rareru():
    # 必要の文: 「〜を教えられる人」は申告（v0.3.1-dev は型なし）
    n = "高齢の方にスマホの使い方を教えられる人。"
    assert run(n, "。")["need"]["型"] == "役割（申告でよい）"
    assert run(n, "。", OLD)["need"]["型"] == "型なし（照合しない）"
    # 相手の文: 「〜を教えられます」は可能（申告）。過去・進行・「〜を」の無いものは受身のまま
    r = run(TEACH_NEED, "公民館で、高齢の方に携帯電話の操作を教えられます。")
    assert r["passed"] and (r["relation"], r["kind"]) == ("R2", "申告")
    assert not run(TEACH_NEED, "公民館で、高齢の方に携帯電話の操作を教えられます。", OLD)["passed"]
    for offer in ("介護施設で、職員に思い出話を聞かれて、冊子にまとめられた。",           # G13: 過去
                  "公民館で、高齢の方に携帯電話の操作を教えられている。",                 # 進行
                  "公民館で、高齢の方から携帯電話のことでよく頼られる。"):               # 「〜を」が無い（五段＋れる）
        assert not run(TEACH_NEED, offer)["passed"], offer
    assert "受身・受益" in run("高齢の方から話を聞き取り、文章にまとめてきた人。",
                               "介護施設で、職員に思い出話を聞かれて、冊子にまとめられた。")["why"]


def test_t555_m2_polite_forms_of_section2():
    assert kind_of("地域の特産品を都内の店につなぐことを仕事にしています") == "現職"
    assert kind_of("地域の特産品を都内の店につなぐことを仕事にしています", OLD) == "応えない：現在の形・期間なし"
    assert kind_of("地域の特産品を都内の店につなぐことを仕事にしている", OLD) == "現職"
    s17 = ("活動に使える空き部屋や集会所を、地域で貸し出せる人。", "町内会の集会所の管理を任されていて、利用の受付ができます。")
    r = run(*s17)                                                                     # S17 の丁寧の形
    assert r["passed"] and (r["relation"], r["kind"]) == ("R3", "使える立場")
    assert not run(*s17, version=OLD)["passed"]


def test_t555_m3_final_particles_and_contractions():
    assert kind_of("もう十年、子どもの宿題を見てるよ") == "継続"
    assert kind_of("もう十年、子どもの宿題を見てるよ", OLD) is None
    assert kind_of("子どもの宿題を見てたね") == "経験"
    assert kind_of("子どもの宿題を見てます") == "応えない：現在の形・期間なし"
    tok = MT._VER.set(V)
    try:
        cl = MT.split_clauses("使ってない電子ピアノが家にあるから、貸せるよ。")
        assert [c.text for c in cl] == ["使ってない電子ピアノが家にあるから", "貸せるよ"]
        assert MT.quote_text(cl[-1]) == "貸せる"
    finally:
        MT._VER.reset(tok)


def test_t555_m4_and_f1_interest():
    need = "ものづくりの技術が、世代を越えて受け継がれることに関心がある人。"
    for offer in ("伝統工芸の技の継承に、とても関心があります。", "伝統工芸の技の継承に関心を持っています。",
                  "伝統工芸の技の継承に関心を持っているよ。"):
        r = run(need, offer)
        assert r["passed"] and (r["relation"], r["kind"]) == ("R4", "関心"), offer
        assert not run(need, offer, OLD)["passed"], offer
    tok = MT._VER.set(V)
    try:
        p = MT.analyze_pred(MT.tokenize("技の継承に、とても関心があります"))
        assert MT.interest_np(p) == "技の継承"
    finally:
        MT._VER.reset(tok)


# ── 556 v0.3.1-dev は凍結のまま ─────────────────────────────────────────────────────────
def test_t556_v031_frozen(record):
    assert record["表"]["比べた版の files"] == MT.table_digest(OLD)
    assert record["60 組と gates"]["変わった組"] == []
    old = json.load(open(os.path.join(ROOT, "data", "explanation_bench", "runs", "77a_match_tables_v0.3.1-dev.json"),
                         encoding="utf-8"))
    assert record["60 組と gates"][OLD]["60"]["誤り"] == f"{old['counts']['60']['誤り']['出した']}/{old['counts']['60']['誤り']['分母']}"


# ── 557 記録と数 ────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def record():
    return json.load(open(T78.RECORD, encoding="utf-8"))


def test_t557_record_reproduces_and_counts(record):
    rec = T78.measure()
    assert json.dumps(rec, ensure_ascii=False, sort_keys=True) == json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert all(record["決定性"].values())
    g = record["60 組と gates"][V]
    assert (g["60"]["誤り"], g["60"]["拾えた"], g["60"]["関係も一致"]) == ("0/45", "15/15", 15)
    assert (g["gates"]["負例"], g["gates"]["正例"]) == ("37/37 を落とした", "13/13 期待どおり")
    b = record["v2"][V]
    assert b["線1（誤り・機械で数えた分）"] == 0 and b["線2（説明が出た）"] == "3/24"
    assert b["まだ落ちる説明できる組"]["数"] == 21
    assert b["まだ落ちる説明できる組"]["段ごと"] == {"種類": 4, "述語": 14, "領域": 3}
    assert sorted(x["id"] for x in record["v2"]["変わった組"]) == [
        "V01", "V03", "V07", "V10", "V12", "V13", "V17", "V24", "V26", "V29", "V36", "V51", "V59"]
    assert sorted(x["id"] for x in record["v2"]["変わった組"] if "通った" in x["変わったもの"]) == ["V03", "V17"]
    assert record["v2"][OLD]["線2（説明が出た）"] == "1/24"
