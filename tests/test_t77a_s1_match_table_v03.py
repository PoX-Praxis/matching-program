"""指示書77a 追補1 — 照合の表 v0.3 を、LLM なしでもう一度測る。テスト 527〜536。

527 表 v0.3 のデータは文書の写し（日本語の欄は文書のまま・機械の読み方の語も文書の語。文書に無い語は読み方の一覧だけ）
528 版を選べる。v0.2 は前のまま動き、v0.3 と混ざらない
529 関門は文全体で見る（従属節の否定 G32・主語 G33）。消費の節は文を落とさず、その節の語を組にしない（G31）
530 前の節をつなぐ（S16・S17・S36・G30）。主節に自分の対象があればつながない（S30）。前の節が関門に当たればつながない
531 概念の 3 列: 領域の名詞は両方に要る・支える語だけでは入らない・禁止語で落とす（G25・G26・G27・G35・G23）。G36 は通る
532 枠の穴: 相手の句に無ければ文全体で見る（S52・G21）。穴があって概念に入らなければ落とす（S51・G20・G28）
533 offer_quote は主張を保つ最小の節（G31 に「映像を見るのが好き」が入らない・G34 に「週に一度なら」が残る）
534 「概念不明」のログ（組の id・必要の句・相手の句・人のメモ・nomic の近さ・理由）。nomic は判定に使わない
535 決定性・記録（runs/77a_match_tables_v0.3.json）・辞書形と表記のままの比べ
536 R4 の型の ① の短い名前は「同じ関心。補完ではありません」。［B］は v0.2 と同じ
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

RECORD = os.path.join(ROOT, "data", "explanation_bench", "runs", "77a_match_tables_v0.3.json")


@pytest.fixture(scope="module")
def items():
    return {it["id"]: it for it in T.load_items(T.GATES_V11)}


@pytest.fixture(scope="module")
def record():
    with open(RECORD, encoding="utf-8") as f:
        return json.load(f)


def run(items, i, words="lemma"):
    it = items[i]
    return MT.match(it["need"], it["offer"], version="v0.3", words=words)


# ── 527 表 v0.3 のデータは文書の写し ─────────────────────────────────────────────────────────
READINGS_V03 = T515.READINGS | {"予約を任されていて", "鍵を任されていて", "運営を任されていて",   # 「〜（管理・予約・鍵・運営）を任されていて」
                                "貸し出しができる"}                                               # 「受付・貸し出し・利用の手配ができる」


def test_t527_v03_tables_are_a_copy_of_the_document():
    doc = open(os.path.join(ROOT, "docs", "match_tables_v0.3.md"), encoding="utf-8").read()
    pri = open(os.path.join(ROOT, "docs", "answer_principle_v1.md"), encoding="utf-8").read()
    src = doc + pri
    src = src + src.replace("**", "")
    tabs = MT.load_tables("v0.3")
    man = json.load(open(os.path.join(MT.TABLE_ROOT, "v0.3", "manifest.json"), encoding="utf-8"))
    import hashlib
    for k in ("表", "原則"):
        f = man["source"][k]["file"]
        assert man["source"][k]["sha256"] == hashlib.sha256(open(os.path.join(ROOT, f), "rb").read()).hexdigest()
    assert sorted(man["files"]) == sorted(MT.table_files("v0.3"))
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
            if w not in src and w not in READINGS_V03:
                missing.append((name, "match:" + w))
    allowed = {"所有", "使える立場", "応えない", "v1.0 では応えない", "主題", "物・機材", "期間", "型なし（照合しない）"}
    missing = [(n, p) for n, p in missing if p not in allowed and not p.startswith("docs/")]
    assert missing == []
    rows = tabs["concepts"]["concepts"]
    assert len(rows) == 12
    for c in rows:
        line = f"| {c['概念']} | {c['領域の名詞（必要と相手の両方に 1 つ以上）']} | {c['支える語（動詞・広い語を含む。単独では不可）']} | {c['禁止語（あれば落とす）']} |"
        assert line in doc
    assert [r["種類"] for r in tabs["objects"]["rows"]][8:10] == ["場所", "資金"]
    assert next(k for k in tabs["clause_kinds"]["kinds"] if k["種類"] == "関心")["短い名前"] == "同じ関心。補完ではありません"
    # 原則 v1 から写したものは v0.2 と同じ
    for f in ("need_types", "type_compat", "nominalize"):
        a, b = dict(MT.load_tables("v0.2")[f]), dict(tabs[f])
        a.pop("source", None)
        b.pop("source", None)
        assert a == b, f


# ── 528 版を選べる・v0.2 は前のまま ───────────────────────────────────────────────────────
def test_t528_versions_do_not_mix(items):
    s16 = items["S16"]
    assert not MT.match(s16["need"], s16["offer"])["passed"]                     # 既定は v0.2（句に分けると落ちる）
    assert MT.match(s16["need"], s16["offer"], version="v0.3")["passed"]
    assert not MT.match(s16["need"], s16["offer"])["passed"]                     # v0.3 を呼んだ後も v0.2 は変わらない
    assert MT._VER.get() == "v0.2" and MT._WORDS.get() == "surface"
    assert "units" not in MT.load_tables("v0.2") and "units" in MT.load_tables("v0.3")
    assert set(MT.table_digest("v0.3")) == set(MT.table_files("v0.3"))
    assert os.path.exists(T.RECORD)                                              # v0.2 の記録は残す


# ── 529 関門は文全体で見る ───────────────────────────────────────────────────────────────
def test_t529_sentence_gates(items):
    g32 = run(items, "G32")                                                      # 記録したことはないが、…見るのは好きだ
    assert not g32["passed"] and g32["where"] == "関門" and "否定" in g32["why"] and "文全体" in g32["why"]
    assert g32["units"] == ["職人の作業を記録したことはないが", "職人の映像を見るのは好きだ"]
    g33 = run(items, "G33")
    assert not g33["passed"] and "主語が O でない" in g33["why"]
    for i in ("G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08"):
        assert run(items, i)["where"] == "関門", i
    # 名詞を修飾する節の否定（行けない子・孤立しないための）は見ない
    assert run(items, "S11")["passed"] and run(items, "S56")["passed"]
    # 消費に当たる節は、その節だけを落とす。文全体は落とさない（G31）
    g31 = run(items, "G31")
    assert g31["passed"] and g31["relation"] == "R1" and g31["kind"] == "経験" and not g31.get("joined")
    first = g31["clauses"][0]
    assert first["句"] == "職人の手仕事の映像を見るのが好きで" and first["関門"] == ["消費"]


# ── 530 前の節をつなぐ ───────────────────────────────────────────────────────────────────
JOINED = {"S16": ("R3", "所有", "自宅の離れが空いているので、平日の午後なら場所として提供できる"),
          "S17": ("R3", "使える立場", "町内会の集会所の管理を任されていて、利用の受付ができる"),
          "S36": ("R1", "経験", "介護施設で、利用者の思い出話を聞いて冊子にまとめてきました"),
          "G30": ("R3", "使える立場", "公民館の部屋の予約を任されていて、地域の活動に使えるよう受付ができる")}


@pytest.mark.parametrize("i", sorted(JOINED))
def test_t530_join_previous_clause(items, i):
    r = run(items, i)
    assert r["passed"] and r["joined"]
    assert (r["relation"], r["kind"], r["clause"]) == JOINED[i]
    assert len(r["clause"]) <= 40


def test_t530_join_conditions(items):
    s30 = run(items, "S30")                                                      # 主節に「〜に関心」の対象がある
    assert not s30["passed"] and s30["where"] == "領域"
    assert any("主節に自分の対象がある" in x["理由"] for x in s30["つながなかった"])
    g12 = run(items, "G12")                                                      # 主節が受益の関門（つないでも落ちる）
    assert not g12["passed"]
    s03 = run(items, "S03")                                                      # 前の節が消費の関門
    assert not s03["passed"]
    assert any("前の節が関門に当たる" in x["理由"] for x in s03.get("つながなかった", []))
    # 「任されて」は受身の関門に当てない（表 §1 の注）。v0.2 では当てていた（記録を変えない）
    c = MT.split_clauses(items["S17"]["offer"])[0]
    tok = MT._VER.set("v0.3")
    try:
        assert MT.gate_hits(c) == []
    finally:
        MT._VER.reset(tok)
    assert MT.gate_hits(c) == ["受身・受益"]


# ── 531 概念の 3 列 ──────────────────────────────────────────────────────────────────────
def test_t531_concepts_three_columns(items):
    for i, word in (("G25", "IT"), ("G26", "宇宙"), ("G27", "投資家"), ("G35", "会社")):
        r = run(items, i)
        assert not r["passed"] and r["where"] == "領域" and f"禁止語（{word}）" in r["why"], i
    g23 = run(items, "G23")
    assert not g23["passed"] and "支える語だけ（場所）" in g23["why"]
    assert not run(items, "G24")["passed"]
    g36 = run(items, "G36")
    assert g36["passed"] and g36["relation"] == "R4" and g36["domain"] == "概念 craft.inheritance"
    tok, tw = MT._VER.set("v0.3"), MT._WORDS.set("lemma")
    try:
        sp = MT.Span(MT.tokenize("IT 技術が、若い世代に受け継がれることに関心がある"))
        d = MT.concept_detail(sp)["craft.inheritance"]
        assert d == {"領域の名詞": [], "支える語": ["技術", "受け継ぐ", "世代"], "禁止語": ["IT"]}
        assert sp.has("受け継ぐ") and not sp.find("受け継ぐ") >= 0                # 辞書形で当たる（表記のままでは当たらない）
        assert MT.concepts_v03(MT.Span(MT.tokenize("作られた物を長く大切に使うことに関心がある"))) == []
    finally:
        MT._VER.reset(tok)
        MT._WORDS.reset(tw)


# ── 532 枠の穴 ──────────────────────────────────────────────────────────────────────────
def test_t532_frame_holes(items):
    s52 = run(items, "S52")
    assert s52["passed"] and s52["relation"] == "R2"
    assert s52["clauses"][0]["枠（文全体で見た穴）"] == {"内容": ["elder.devices"]}
    g21 = run(items, "G21")
    assert g21["passed"] and g21["kind"] == "現職" and g21["C"] == "その役割を仕事にしている、と書いています"
    for i in ("S51", "G20"):
        r = run(items, i)
        assert not r["passed"] and r["where"] == "枠" and "〈内容〉" in r["why"] and "「操作」[]" in r["why"], i
    g20 = run(items, "G20")
    assert g20["clauses"][0]["句の種類"] == "継続"
    g28 = run(items, "G28")
    assert not g28["passed"] and g28["where"] == "枠" and "数学の解き方" in g28["why"]


# ── 533 offer_quote ─────────────────────────────────────────────────────────────────────
def test_t533_offer_quote(items, record):
    assert run(items, "G31")["clause"] == "工場の作業を手順書に記録してきた"
    assert "映像を見るのが好き" not in run(items, "G31")["clause"]
    g34 = run(items, "G34")
    assert g34["passed"] and g34["clause"] == "週に一度なら、公民館のスマートフォン相談会に、教える立場で関われる"
    assert record["offer_quote"] == {"G31": "工場の作業を手順書に記録してきた",
                                     "G34": "週に一度なら、公民館のスマートフォン相談会に、教える立場で関われる"}
    for it in items.values():
        r = run(items, it["id"])
        if r["passed"]:
            assert r["clause"] in it["offer"] and len(r["clause"]) <= 40


# ── 534 「概念不明」のログ ─────────────────────────────────────────────────────────────────
def test_t534_concept_unknown_log(items, record):
    log = record["概念不明"]
    assert [x["id"] for x in log] == ["S30", "G23", "G25", "G26", "G27", "G35"]
    for x in log:
        assert set(x) == {"id", "必要の句", "相手の句", "人のメモ", "nomic の近さ（参考）", "落とした理由"}
        assert x["人のメモ"] == "" and x["落とした理由"].startswith("概念不明")
        assert x["相手の句"] in items[x["id"]]["offer"] and x["必要の句"] == items[x["id"]]["need"]
        assert isinstance(x["nomic の近さ（参考）"], float)
    rows = T.concept_unknown_log(list(items.values()), T.run_all(list(items.values()), "split", version="v0.3", words="lemma"), {})
    assert [x["id"] for x in rows] == [x["id"] for x in log] and all(x["nomic の近さ（参考）"] is None for x in rows)
    assert "判定に使わない" in record["nomic"]["使い方"]


# ── 535 決定性・記録・辞書形と表記のまま ─────────────────────────────────────────────────────
def test_t535_determinism_and_record(record):
    sims, info = T.sims_from_record(RECORD)
    a = T.measure_v03(sims, info)
    b = T.measure_v03(sims, info)
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(b, ensure_ascii=False, sort_keys=True)
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert record["determinism"]["same"] and record["table"]["files"] == MT.table_digest("v0.3")
    assert record["gates"] == "gates_v1.1.json"
    c = record["modes"]["lemma"]["counts"]
    assert (c["60"]["出した"], c["60"]["誤り"]["出した"], c["60"]["拾えた"]["出した"], c["60"]["拾えた"]["関係も一致"]) == (14, 0, 14, 14)
    assert (c["gates"]["負例"]["組"], c["gates"]["負例"]["落とした"]) == (30, 30)
    assert [x["id"] for x in c["gates"]["正例"] if x["期待どおり"]] == ["G21", "G30", "G31", "G34", "G36"]
    assert record["words_diff"] == []
    assert record["modes"]["surface"]["counts"] == record["modes"]["lemma"]["counts"]
    assert [x["id"] for x in record["v0.2 で拾えていた組"] if not x["v0.3"].startswith("R")] == []


# ── 536 型の ① ─────────────────────────────────────────────────────────────────────────
def test_t536_type1_short_names(record):
    t1 = record["modes"]["lemma"]["type1"]
    r4 = [x for x in t1 if x["関係"] == "R4"]
    assert r4 and all(x["①"].endswith("（同じ関心。補完ではありません）。") for x in r4)
    s01 = next(x for x in t1 if x["id"] == "S01")
    assert s01["①"] == ("{N}が求める「食品の加工や衛生管理の工程を、現場で担ってきた経験」について、"
                       "{O}は「ジャム工房で十年、仕込みから殺菌、衛生記録までを担当してきました」と書いています（経験）。")
    needs = {v["B"] for v in record["needs"].values()}
    assert needs == set(T515.B12.values())
