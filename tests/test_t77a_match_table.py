"""指示書77a — 照合の表 v0.2 を、LLM を使わずに単体で測る。テスト 515〜526。

515 表のデータは文書の写し（日本語の欄は文書のまま・機械の読み方の語も文書の語。文書に無い語は読み方の一覧に挙げたものだけ）
516 関門ごとに、gates_v1 の負例が落ちる（その関門で）。元の組の結果は記録のとおり
517 「見る」が、対象で消費と支援に分かれる（S23 と S12）
518 軽い動詞（担当する・担う）が、対象で種類に分かれる（S01・S41・S51・S39）
519 「教える」の枠で、内容が違えば落ちる（S04・G28）。〈相手〉が概念表に無ければ落ちる（S51・G20。表が落としすぎ）
520 概念表: 言葉が重ならない正しい組（S16・S26・S31・S46）が概念で通る。領域が近いだけの負例（G24〜G27）が落ちる。語は形態素の切れ目で当てる
521 ［C］の種類: 使える立場（S17・G30）・継続（G20）・現職（G21）
522 決定性: 同じ入力で同じ結果。記録（runs/77a_match_tables_v0.2.json）は、記録にある nomic の値でもう一度計算すると同じになる
523 ［B］の名詞化（12 文）と、型の ①
524 句に分ける（読点・〜て・〜し。述語で終わらない切れ端はつなぐ。「として」「使い方」は切らない）
525 nomic の線の決め方（負例が 0 になる、いちばん低い線）と、概念表との食い違いを採らないこと
526 道具の版の固定（requirements-bench.txt・CI）と、本番のコードから読まれないこと
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

import match_table as MT  # noqa: E402
import t77a_measure as T  # noqa: E402

RECORD = os.path.join(ROOT, "data", "explanation_bench", "runs", "77a_match_tables_v0.2.json")


@pytest.fixture(scope="module")
def items():
    return {it["id"]: it for it in T.load_items()}


@pytest.fixture(scope="module")
def record():
    with open(RECORD, encoding="utf-8") as f:
        return json.load(f)


def run(items, i, mode="split"):
    it = items[i]
    return MT.match(it["need"], it["offer"], mode=mode)


# ── 515 表のデータは文書の写し ─────────────────────────────────────────────────────────────
# 文書に無い語を、機械の読み方として使ったもの（報告77a §1 に挙げた）
READINGS = {"受け入れる",      # 表 §3-1 の注「受け入れてきた（S11）は運営の中の行い」の辞書形
            "仕事にする",      # 軽い動詞「する」の形（〜を仕事にしている）
            "思う", "考える",   # 希望の「〜たいと思っている／と考えている」の辞書形
            "くる"}            # 「〜てきた」の「き」の辞書形


def _strings(obj, skip=("match", "source", "出どころ", "効く型", "機械が見る順", "能力の語（原則 v1 §1）", "資格の語（原則 v1 §1）")):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip:
                continue
            yield from _strings(v, skip)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, skip)
    elif isinstance(obj, str):
        yield obj


def _match_words(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "match":
                yield from _all_str(v)
            else:
                yield from _match_words(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _match_words(v)


def _all_str(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _all_str(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_str(v)
    elif isinstance(obj, str):
        yield obj


def test_t515_tables_are_a_copy_of_the_document():
    doc = open(os.path.join(ROOT, "docs", "match_tables_v0.2.md"), encoding="utf-8").read()
    pri = open(os.path.join(ROOT, "docs", "answer_principle_v1.md"), encoding="utf-8").read()
    spec_words = "生成仕様 v1.0（案2）§6-1・指示書77a §2-3"
    src = doc + pri
    src = src + src.replace("**", "")                                  # 太字の印は、あってもなくてもよい
    tabs = MT.load_tables()
    man = json.load(open(os.path.join(MT.TABLE_ROOT, "v0.2", "manifest.json"), encoding="utf-8"))
    import hashlib
    assert man["source"]["表"]["sha256"] == hashlib.sha256(open(os.path.join(ROOT, man["source"]["表"]["file"]), "rb").read()).hexdigest()
    assert man["source"]["原則"]["sha256"] == hashlib.sha256(open(os.path.join(ROOT, man["source"]["原則"]["file"]), "rb").read()).hexdigest()
    assert sorted(man["files"]) == sorted(MT.TABLE_FILES)
    missing = []
    for name, t in tabs.items():
        if name == "nominalize":
            continue                                            # Code の案（77a §2-3）。表の写しではない
        for s in _strings(t):
            s2 = s.strip("| ").replace(" | ", "|")
            parts = [p for p in re.split(r"[|]", s2) if p and p != "—"]
            for p in parts:
                if p not in src and p.replace("（", "").replace("）", "") not in src:
                    missing.append((name, p))
        for w in _match_words(t):
            if w not in src and w not in READINGS:
                missing.append((name, "match:" + w))
    # 欄の名前（表の見出しと同じ語）と、組み立てた名前（資源（所有）の短い呼び方など）は除く
    allowed = {"所有", "使える立場", "応えない", "v1.0 では応えない", "主題", "物・機材", "期間", "型なし（照合しない）"}
    missing = [(n, p) for n, p in missing if p not in allowed and not p.startswith(("docs/", spec_words))]
    assert missing == []
    assert "fugashi" in man["tool"] and man["tool"]["fugashi"] == "1.5.2"


def test_t515_concepts_copied_exactly():
    tabs = MT.load_tables()
    doc = open(os.path.join(ROOT, "docs", "match_tables_v0.2.md"), encoding="utf-8").read()
    rows = [c for c in tabs["concepts"]["concepts"]]
    assert len(rows) == 12
    for c in rows:
        assert f"| {c['概念']} | {c['必要の側に要る語']} | {c['まとめる句']} |" in doc
        assert c["match"]["phrases"] == c["まとめる句"].split("・")
    gates = [g["関門"] for g in tabs["gates"]["gates"]]
    assert gates == ["主語が O でない", "否定", "推量・伝聞", "希望", "気持ち・後悔・評価", "受身・受益", "探す・募集する", "消費", "学ぶ側"]
    assert [k["種類"] for k in tabs["clause_kinds"]["kinds"]] == ["経験", "継続", "現職", "申告", "所有", "使える立場", "関心"]
    assert len(tabs["predicates"]["互換"]) == 9 and len(tabs["predicates"]["枠"]) == 10 and len(tabs["objects"]["rows"]) == 12


# ── 516 関門 ─────────────────────────────────────────────────────────────────────────────
GATE_IDS = {"G01": "否定", "G02": "否定", "G03": "否定", "G04": "否定", "G05": "推量・伝聞", "G06": "推量・伝聞",
            "G07": "推量・伝聞", "G08": "主語が O でない", "G09": "希望", "G10": "希望", "G11": "受身・受益", "G12": "受身・受益",
            "G13": "受身・受益", "G14": "探す・募集する", "G15": "探す・募集する", "G16": "消費", "G17": "消費",
            "G18": "学ぶ側", "G19": "学ぶ側"}


@pytest.mark.parametrize("gid", sorted(GATE_IDS))
def test_t516_gate_negatives_drop_at_the_gate(items, gid):
    r = run(items, gid)
    assert not r["passed"]
    hits = {g for c in r["clauses"] for g in (c.get("関門") or [])}
    assert GATE_IDS[gid] in hits


def test_t516_all_gate_negatives_drop_and_bases(items, record):
    for it in items.values():
        if it["set"] == "gates" and it["gold"] == "説明できない":
            assert not run(items, it["id"])["passed"], it["id"]
    # 元の組（base）: 正解 r2 のとおりに通るかは、記録のとおり（S16・S36・S51・S52 は表で落ちる。報告77a §3）
    bases = record["modes"]["split"]["counts"]["gates"]["元の組"]
    assert sorted(b for b, v in bases.items() if not v["r2 のとおり"]) == ["S16", "S36", "S51", "S52"]
    for b, v in bases.items():
        r = run(items, b)
        assert (r["relation"] if r["passed"] else "落とした") == v["表"]


def test_t516_consumption_and_learning_gates_only_for_role_and_resource(items):
    # 学ぶ側（習い始めた）は、関心の必要（S50）には当てない（効く必要の型は役割・資源）。種類で落ちる
    r = run(items, "S50")
    assert not r["passed"] and not any(c.get("関門") for c in r["clauses"])
    assert r["where"] == "種類"


# ── 517 「見る」────────────────────────────────────────────────────────────────────────────
def test_t517_miru_consumption_vs_support(items):
    s23 = run(items, "S23")
    assert not s23["passed"]
    assert "消費" in s23["clauses"][0]["関門"]                   # 職人の手仕事の映像を見るのが好きで（映像を見る）
    s12 = run(items, "S12")
    c = s12["clauses"][0]
    assert not s12["passed"] and c["述語の種類"] == "支援" and c["落ちた段"] == "述語"    # 宿題を見る立場で関われる
    assert c["関門"] == []


# ── 518 軽い動詞 ────────────────────────────────────────────────────────────────────────
def test_t518_light_verbs_by_object(items):
    def kind(i):
        r = run(items, i)
        c = [x for x in r["clauses"] if x.get("述語の種類") or x.get("述語の鍵")][-1]
        return c.get("述語の種類"), c.get("述語の鍵"), c.get("述語の決め方")
    assert kind("S01")[:2] == ("実施・担当（工程）", "担当する")          # 仕込みから殺菌、衛生記録までを担当
    assert kind("S41")[:2] == ("実施・担当（技術）", "担当する")          # 音響を担当
    assert kind("S51")[0] == "教える" and kind("S51")[2] == "動詞と対象の組"   # 講座を担当する
    assert kind("S39")[0] is None                                     # マラソン大会の運営を担う（§4 に無い）→ 分からない
    s42 = run(items, "S42")
    c = s42["clauses"][0]
    assert s42["passed"] and c["述語の種類"] == "実施・担当（技術）" and set(c["述語の候補"]) == {"教える", "実施・担当（技術）"}


# ── 519 教える の枠 ───────────────────────────────────────────────────────────────────────
def test_t519_teaching_frame(items):
    s04 = run(items, "S04")
    assert not s04["passed"] and s04["where"] == "述語"                 # 必要は加工の工程（教えるは許可に無い）
    g28 = run(items, "G28")
    assert not g28["passed"] and g28["where"] == "枠" and "〈内容〉" in g28["why"]
    s51 = run(items, "S51")
    assert not s51["passed"] and s51["where"] == "枠" and "〈相手〉" in s51["why"]      # 高齢のお客さま（概念表に無い）
    s52 = run(items, "S52")
    assert not s52["passed"] and s52["where"] == "枠" and "相手の句に無い" in s52["why"]


# ── 520 概念表 ──────────────────────────────────────────────────────────────────────────
def test_t520_concepts(items):
    for i in ("S26", "S31", "S46"):
        r = run(items, i)
        assert r["passed"] and r["relation"] == "R4" and r["domain"].startswith("概念"), i
    s16 = run(items, "S16", mode="whole")
    assert s16["passed"] and s16["domain"] == "概念 place.community" and s16["kind"] == "所有"
    assert not run(items, "S16")["passed"]                               # 句に分けると「離れ」が前の句（報告77a §6）
    for i in ("G24", "G25", "G26", "G27"):
        assert not run(items, i)["passed"], i
    for i in ("G25", "G26", "G27"):
        assert run(items, i)["where"] == "領域", i
    sp = MT.Span(MT.tokenize("IT 技術が、若い世代に受け継がれることに関心がある"))
    assert not sp.has("技") and not sp.has("受け継ぐ") and sp.has("技術")    # 形態素の切れ目で当てる
    assert not MT.Span(MT.tokenize("若手の投資家が、活動を続けられる仕組み")).has("活動を続ける")


# ── 521 ［C］の種類 ────────────────────────────────────────────────────────────────────
def test_t521_c_kinds(items):
    for i in ("S17", "G30"):
        r = run(items, i, mode="whole")
        assert r["passed"] and r["relation"] == "R3" and r["kind"] == "使える立場", i
        assert r["C"] == "その場所を使える立場にある、と申告しています"
        assert not run(items, i)["passed"]                              # 句に分けると「任されていて」が前の句
    s16 = run(items, "S16", mode="whole")
    assert s16["C"] == "その資源を持っている、と書いています"
    for i, kind, period in (("G20", "継続", "十年間"), ("G21", "現職", None)):
        c = MT.split_clauses(items[i]["offer"])[0]
        p = MT.analyze_pred(c.toks, c.sentence)
        assert MT.clause_kind(p, c.span) == (kind, period), i
    g20 = run(items, "G20")
    assert not g20["passed"] and g20["clauses"][0]["句の種類"] == "継続" and g20["where"] == "枠"
    k = next(x for x in MT.load_tables()["clause_kinds"]["kinds"] if x["種類"] == "継続")
    assert k["C"].replace("〔期間〕", "十年間") == "その役割を十年間続けている、と書いています"


# ── 522 決定性・記録 ───────────────────────────────────────────────────────────────────
def test_t522_determinism_and_record(record):
    sims, info = T.sims_from_record(RECORD)
    a = T.measure(sims, info)
    b = T.measure(sims, info)
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(b, ensure_ascii=False, sort_keys=True)
    assert a["determinism"]["same"]
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert record["table"]["files"] == MT.table_digest()
    assert record["tools"]["fugashi"] == "1.5.2" and record["tools"]["unidic-lite"] == "1.0.8"
    assert record["nomic"]["revision_cached"] == T.NOMIC[1] and record["nomic"]["prefix"] == "search_query: "
    c = record["modes"]["split"]["counts"]["60"]
    assert (c["出した"], c["誤り"]["出した"], c["拾えた"]["出した"], c["拾えた"]["関係も一致"]) == (10, 0, 10, 10)
    g = record["modes"]["split"]["counts"]["gates"]["負例"]
    assert (g["組"], g["落とした"]) == (27, 27)


def test_t522_measure_does_not_read_local(monkeypatch):
    import builtins
    real = builtins.open

    def guard(path, *a, **k):
        assert "/local/" not in str(path).replace(os.sep, "/"), path
        return real(path, *a, **k)
    monkeypatch.setattr(builtins, "open", guard)
    sims, info = T.sims_from_record(RECORD)
    T.measure(sims, info)


# ── 523 ［B］と型の ① ──────────────────────────────────────────────────────────────────
B12 = {
    "食品の加工や衛生管理の工程を、現場で担ってきた人。": "食品の加工や衛生管理の工程を、現場で担ってきた経験",
    "加工品の販路を、通販や小売店とのつながりで広げてきた人。": "加工品の販路を、通販や小売店とのつながりで広げてきた経験",
    "子どもの居場所や学習支援の場を、運営してきた人。": "子どもの居場所や学習支援の場を、運営してきた経験",
    "活動に使える空き部屋や集会所を、地域で貸し出せる人。": "活動に使える空き部屋や集会所を、地域で貸し出せること",
    "職人の作業を、映像や手順書として記録してきた人。": "職人の作業を、映像や手順書として記録してきた経験",
    "ものづくりの技術が、世代を越えて受け継がれることに関心がある人。": "ものづくりの技術が、世代を越えて受け継がれることへの関心",
    "地域の歴史や、昔の暮らしの記録に関心がある人。": "地域の歴史や、昔の暮らしの記録への関心",
    "高齢の方から話を聞き取り、文章にまとめてきた人。": "高齢の方から話を聞き取り、文章にまとめてきた経験",
    "音響や照明など、イベントの裏方の技術を持つ人。": "音響や照明など、イベントの裏方の技術を持つこと",
    "若い表現者が活動を続けられる仕組みに関心がある人。": "若い表現者が活動を続けられる仕組みへの関心",
    "高齢の方に、機器の使い方を教えてきた人。": "高齢の方に、機器の使い方を教えてきた経験",
    "人と人の孤立を防ぐ、地域の仕組みに関心がある人。": "人と人の孤立を防ぐ、地域の仕組みへの関心",
}


def test_t523_nominalize_and_type1(items, record):
    needs = {it["need"] for it in items.values()}
    assert needs == set(B12)
    for n, b in B12.items():
        assert MT.nominalize(n) == b
        assert not b.endswith("人")
    assert MT.nominalize("食品安全の専門家。") is None                  # 名詞だけの必要は名詞化しない
    s = MT.type1_sentence(B12["食品の加工や衛生管理の工程を、現場で担ってきた人。"],
                          "ジャム工房で十年、仕込みから殺菌、衛生記録までを担当してきました", "経験")
    assert s == ("{N}が求める「食品の加工や衛生管理の工程を、現場で担ってきた経験」について、"
                 "{O}は「ジャム工房で十年、仕込みから殺菌、衛生記録までを担当してきました」と書いています（経験）。")
    t1 = record["modes"]["split"]["type1"]
    assert len(t1) == 10 and all(x["offer_quote の字数"] <= 40 for x in t1)
    shorts = {x["C の種類"]: x["①"][x["①"].rfind("（") + 1:-2] for x in t1}
    assert shorts == {"経験": "経験", "申告": "申告", "関心": "同じ関心"}


# ── 524 句に分ける ─────────────────────────────────────────────────────────────────────
def test_t524_split_clauses(items):
    texts = lambda i, mode="split": [c.text for c in MT.split_clauses(items[i]["offer"], mode)]   # noqa: E731
    assert texts("S11") == ["放課後の学習教室を五年間運営し", "学校に行けない子も受け入れてきた"]          # 〜し、
    assert texts("S36") == ["介護施設で、利用者の思い出話を聞いて", "冊子にまとめてきました"]          # 〜て（読点なし）
    assert texts("G08") == ["知人に、放課後の学習教室を五年間運営してきた人がいる"]                  # 述語の無い切れ端はつなぐ
    assert texts("S12") == ["週に一度なら、子どもたちの宿題を見る立場で関われる"]                    # 「〜なら、」はつなぐ
    assert texts("S27") == ["金型の職人として、四十年間で十人以上の弟子を育ててきた"]                # 「として」は切らない
    assert texts("S54") == ["祖父にスマートフォンの使い方を教えたいと思っている"]                    # 「使い方」は切らない
    assert texts("S01") == ["ジャム工房で十年、仕込みから殺菌、衛生記録までを担当してきました"]
    assert texts("S16", "whole") == ["自宅の離れが空いているので、平日の午後なら場所として提供できる"]
    for it in items.values():
        cs = MT.split_clauses(it["offer"])
        assert all(c.text and c.text in it["offer"] for c in cs)


def test_t524_clause_breaks_in_record(record):
    assert sorted(x["id"] for x in record["clause_breaks"]) == ["G30", "S16", "S17", "S36"]


# ── 525 nomic の線 ───────────────────────────────────────────────────────────────────────
def test_t525_threshold_and_disagreement(items, record):
    its = list(items.values())
    sims, _ = T.sims_from_record(RECORD)
    th, neg, dep = T.choose_threshold(its, "split", sims)
    assert th == record["modes"]["split"]["nomic"]["線"] == max(v for _, v in neg)
    assert sorted(dep) == ["G25", "G27", "S30"]                       # nomic が合うと言えば通ってしまう組（負例だけ）
    assert all(items[i]["gold"] == "説明できない" for i in dep)
    # 線を越えたものだけ合う。中心の名詞の種類が同じでなければ見ない
    fake = {k: (th + 0.01 if k[1].startswith("IT") else v) for k, v in sims.items()}
    r = MT.match(items["G25"]["need"], items["G25"]["offer"], sim=lambda a, b: fake.get((a, b)), threshold=th)
    assert r["passed"] and r["domain"] == "nomic"
    # 概念が「合わない」（両方に概念があって違う）とき、nomic が合うと言っても採らない（G26）
    r = MT.match(items["G26"]["need"], items["G26"]["offer"], sim=lambda a, b: 1.0, threshold=th)
    assert not r["passed"] and "食い違う" in r["why"]
    assert record["modes"]["split"]["nomic"]["nomic で通った組"] == []


# ── 526 道具の版の固定・本番から読まれない ─────────────────────────────────────────────────
def test_t526_pins_and_isolation():
    req = open(os.path.join(ROOT, "requirements-bench.txt"), encoding="utf-8").read().split()
    assert req == ["fugashi==1.5.2", "unidic-lite==1.0.8"]
    # 指示書81: 説明の生成の流れを本番（Render）で動かすので、本番の requirements.txt にも同じ版で入れた
    prod = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read().split()
    assert "fugashi==1.5.2" in prod and "unidic-lite==1.0.8" in prod
    ci = open(os.path.join(ROOT, ".github", "workflows", "tests.yml"), encoding="utf-8").read()
    assert "pip install -r requirements-bench.txt" in ci
    v = MT.tool_versions()
    assert (v["fugashi"], v["unidic-lite"]) == ("1.5.2", "1.0.8") and v["dictionary"]["version"] == 102
    for path in ("app.py",):
        assert "match_table" not in open(os.path.join(ROOT, path), encoding="utf-8").read()
    for name in os.listdir(os.path.join(ROOT, "src")):
        if name.endswith(".py") and name != "match_table.py":
            assert "match_table" not in open(os.path.join(ROOT, "src", name), encoding="utf-8").read(), name
