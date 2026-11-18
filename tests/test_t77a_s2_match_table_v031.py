"""指示書77a 追補2 — 照合の表 v0.3.1-dev を入れて測る。テスト 537〜544。

537 表 v0.3.1-dev のデータは文書の写し（文書が変わった所だけが v0.3 と違う）
538 版を選べる。v0.2・v0.3 は前のまま（それぞれの記録が変わらない）
539 「教える」の比べ方 A〜C（G37〜G45・S51・G20・G28）
540 関門: 文全体に追加でかけるのは 4 つ（G31 は通る・G32・G47）。名詞を修飾する節の否定は見ない（S11・S56）
541 前の節をつなぐ 2 つの場合（② 対象を補う: S06・S11・S16・S17・S21・S36・G30・G50）。S30・G31 はつながない
542 offer_quote（G31・G34・G46・G49・G50）
543 決定性・記録（runs/77a_match_tables_v0.3.1-dev.json）・回帰（v0.3 から変わった組）
544 数（60 組 15/15・誤り 0・gates 負例 37/37・正例 13/13）
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import match_table as MT  # noqa: E402
import t77a_measure as T  # noqa: E402
import test_t77a_match_table as T515  # noqa: E402
import test_t77a_s1_match_table_v03 as T527  # noqa: E402

RECORD = os.path.join(ROOT, "data", "explanation_bench", "runs", "77a_match_tables_v0.3.1-dev.json")
V = "v0.3.1-dev"


@pytest.fixture(scope="module")
def items():
    return {it["id"]: it for it in T.load_items(T.GATES_V12)}


@pytest.fixture(scope="module")
def record():
    with open(RECORD, encoding="utf-8") as f:
        return json.load(f)


def run(items, i):
    it = items[i]
    return MT.match(it["need"], it["offer"], version=V)


# ── 537 表 v0.3.1-dev は文書の写し ───────────────────────────────────────────────────────
def test_t537_tables_are_a_copy_of_the_document():
    doc = open(os.path.join(ROOT, "docs", "match_tables_v0.3.1-dev.md"), encoding="utf-8").read()
    pri = open(os.path.join(ROOT, "docs", "answer_principle_v1.md"), encoding="utf-8").read()
    src = doc + pri
    src = src + src.replace("**", "")
    tabs = MT.load_tables(V)
    man = json.load(open(os.path.join(MT.TABLE_ROOT, V, "manifest.json"), encoding="utf-8"))
    import hashlib
    for k in ("表", "原則"):
        f = man["source"][k]["file"]
        assert man["source"][k]["sha256"] == hashlib.sha256(open(os.path.join(ROOT, f), "rb").read()).hexdigest()
    assert sorted(man["files"]) == sorted(MT.table_files(V))
    missing = []
    for name, t in tabs.items():
        if name == "nominalize":
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
    ed = next(c for c in tabs["concepts"]["concepts"] if c["概念"] == "elder.devices")["match"]
    assert ed["forbidden"] == ["工場", "産業", "業務用", "重機"] and {"携帯", "タブレット", "パソコン"} <= set(ed["domain"])
    teach = tabs["predicates"]["教えるの比べ方"]["match"]
    assert teach["generic_nouns"] == ["人", "相手", "誰か", "方々"]
    # 文書が変わっていない所は v0.3 と同じ
    for f in ("clause_kinds", "objects", "conditions", "need_types", "type_compat", "nominalize"):
        a, b = dict(MT.load_tables("v0.3")[f]), dict(tabs[f])
        a.pop("source", None)
        b.pop("source", None)
        assert a == b, f


# ── 538 版を選べる・前の版は前のまま ───────────────────────────────────────────────────────
def test_t538_versions(items):
    s51 = items["S51"]
    assert not MT.match(s51["need"], s51["offer"], version="v0.3")["passed"]
    assert MT.match(s51["need"], s51["offer"], version=V)["passed"]
    assert not MT.match(s51["need"], s51["offer"], version="v0.3")["passed"]
    assert MT._VER.get() == "v0.2"
    assert V in MT.VERSIONS and os.path.exists(T.RECORD) and os.path.exists(T.RECORD_V03)


# ── 539 「教える」の比べ方 A〜C ─────────────────────────────────────────────────────────────
TEACH = {"G37": True, "G38": True, "G39": False, "G40": False, "G41": False, "G42": False, "G43": False,
         "G44": True, "G45": True, "S51": True, "G20": True, "G28": False}


@pytest.mark.parametrize("i", sorted(TEACH))
def test_t539_teach_frame(items, i):
    r = run(items, i)
    assert r["passed"] == TEACH[i]
    if not TEACH[i]:
        assert r["where"] == "枠", (i, r["where"], r["why"])


def test_t539_teach_frame_reasons(items):
    assert "禁止語が節にある（工場）" in run(items, "G42")["why"]
    assert "相手の側だけが汎用の名詞（人）" in run(items, "G43")["why"]
    g44 = run(items, "G44")
    assert "〈相手〉必要が汎用の名詞（人）" in g44["clauses"][0]["枠（比べない穴）"]
    s51 = run(items, "S51")
    assert s51["clauses"][0]["枠（教える・v0.3.1-dev）"] == {"相手": ["elder.devices", "elder.listening"], "内容": ["elder.devices"]}
    g20 = run(items, "G20")
    assert (g20["relation"], g20["kind"], g20["C"]) == ("R1", "継続", "その役割を十年間続けている、と書いています")


# ── 540 関門 ────────────────────────────────────────────────────────────────────────────
def test_t540_gates(items):
    assert run(items, "G31")["passed"]                                            # 前の節の消費は、文を落とさない
    for i in ("G32", "G47"):
        r = run(items, i)
        assert not r["passed"] and r["where"] == "関門" and "否定" in r["why"], i  # 従属節の否定・一致した述語の否定
    g48 = run(items, "G48")
    assert not g48["passed"] and "気持ち・後悔・評価" in g48["why"]
    assert run(items, "S11")["passed"] and run(items, "S56")["passed"]           # 名詞を修飾する節の否定は見ない
    g46 = run(items, "G46")
    assert g46["passed"] and g46["units"] == ["職人の作業を記録してきたが", "映像を見るのは好きだ"]


# ── 541 前の節をつなぐ ───────────────────────────────────────────────────────────────────
JOINED = ("S06", "S11", "S16", "S17", "S21", "S36", "G30", "G50")


def test_t541_join_cases(items, record):
    for i in JOINED:
        r = run(items, i)
        assert r["passed"] and r["joined"] == "② 対象を補う", i
        assert len(r["clause"]) <= 40
    assert sorted(x["id"] for x in record["joined"]) == sorted(JOINED)
    s30 = run(items, "S30")                                                      # 主節に「〜に関心」の対象がある
    assert not s30["passed"] and not s30.get("つながなかった")
    g31 = run(items, "G31")                                                      # 主節に対象がある（前の節は関門）
    assert not g31.get("joined")
    s44 = run(items, "S44")                                                      # 前の節が消費の関門 → つながない
    assert any("前の節が関門に当たる" in x["理由"] for x in s44["つながなかった"])


# ── 542 offer_quote ─────────────────────────────────────────────────────────────────────
QUOTES = {"G31": "工場の作業を手順書に記録してきた",
          "G34": "週に一度なら、公民館のスマートフォン相談会に、教える立場で関われる",
          "G46": "職人の作業を記録してきた",
          "G49": "週に一度なら、公民館で、高齢の方にスマートフォンの使い方を教えることができる",
          "G50": "職人の作業を撮影し、手順書にまとめてきた"}


def test_t542_offer_quotes(items, record):
    for i, q in QUOTES.items():
        assert run(items, i)["clause"] == q, i
    assert record["offer_quote"] == QUOTES
    for it in items.values():
        r = run(items, it["id"])
        if r["passed"]:
            assert r["clause"] in it["offer"] and not r["clause"].endswith(("が", "、", "。"))


# ── 543 決定性・記録・回帰 ─────────────────────────────────────────────────────────────
def test_t543_determinism_record_regression(record):
    a, b = T.measure_v031(), T.measure_v031()
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(b, ensure_ascii=False, sort_keys=True)
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert record["determinism"]["same"] and record["table"]["files"] == MT.table_digest(V)
    assert record["gates"] == "gates_v1.2.json" and record["words"] == "lemma"
    reg = {x["id"]: x["変わったもの"] for x in record["回帰（v0.3 から変わった組）"]}
    assert sorted(reg) == ["G20", "G23", "G28", "S06", "S11", "S21", "S43", "S51", "S55"]
    assert [i for i, ch in reg.items() if "通った" in ch] == ["S51", "G20"]
    assert all(reg[i] == ["引用"] for i in ("S06", "S11", "S21"))
    assert record["教える A〜C"]["すべて期待どおり"] is True


# ── 544 数 ──────────────────────────────────────────────────────────────────────────────
def test_t544_counts(record):
    c = record["counts"]
    assert (c["60"]["出した"], c["60"]["誤り"]["出した"], c["60"]["拾えた"]["出した"], c["60"]["拾えた"]["関係も一致"]) == (15, 0, 15, 15)
    assert (c["gates"]["負例"]["組"], c["gates"]["負例"]["落とした"]) == (37, 37)
    assert all(x["期待どおり"] for x in c["gates"]["正例"]) and len(c["gates"]["正例"]) == 13
    assert all(v["r2 のとおり"] for v in c["gates"]["元の組"].values())
    r4 = [x for x in record["type1"] if x["関係"] == "R4"]
    assert r4 and all(x["①"].endswith("（同じ関心。補完ではありません）。") for x in r4)
