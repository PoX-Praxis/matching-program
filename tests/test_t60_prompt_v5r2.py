"""指示書60 — 構造化プロンプト v5 改訂2 への差し替え（登録画面・受信側の検証・与え像の確認）。テスト 228〜242。"""
import html as htmlmod
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import drafts  # noqa: E402
import ledger_events as le  # noqa: E402
import v5  # noqa: E402

STORY = "私は ＡＩ と仕組みを作るのが好きです。事業を一緒に立ち上げる人がほしい。論理の穴を指摘してくれる人も。"


def _doc(**over):
    d = {
        "id": "kaoru_2026",
        "schema_version": "v5",
        "generator": "Claude Opus 4.8",
        "purposes": [
            {"purpose_id": "p1", "向かう先": "自然に辿り着ける状態を実現したい。", "手段": "照合の仕組みを根づかせる。",
             "必要像": [{"文": "事業を立ち上げる局面で、構想を実行に移す側として関わってきた人。", "必須": True, "型": "関わり方"}],
             "数値": {"gate_s": 0.9, "gate_u": 0.3, "alpha": 1.2, "beta": 1.0},
             "根拠": "事業を一緒に立ち上げる人がほしい。"}],
        "与え像": [{"文": "まだ形になっていない仕組みを試作として形にする立場で関われる。", "型": "関わり方"},
                 {"文": "照合の仕組みと、そこに集まる人と機会という場を提供できる。", "型": "資源"},
                 {"文": "論点を構造として整理する役回りで関われる。", "型": "関わり方"}],
        "現状": {"持っているもの": "試作", "できること_型": "形にする", "縛られているもの": "時間", "未分類": ""},
        "supporting_material": {"一行紹介": "つなぐ仕組みをつくる", "要約文": "仕組みを作る人。", "生テキスト": [STORY]},
    }
    d.update(over)
    return d


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.setattr(appmod, "_v5_sentence_job", lambda *a, **k: None)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    # 確定の Postgres 部分（profiles_v4・全文ベクトル）は通さない。台帳・与え像・本文の保存は本物で動かす。
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)
    monkeypatch.setattr(appmod, "_ingest_v4_from_flat",
                        lambda flat, profile_id=None, publish_ledger=True:
                        (profile_id, {"necessity_text": flat.get("necessity_text")}, False))
    return appmod.DB


def _cli(sid="u_a"):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["subject_id"] = sid
    return c


def _draft(c, doc):
    return c.post("/v4/drafts", json={"raw_text": json.dumps(doc, ensure_ascii=False)})


def _confirm(c, did, **body):
    return c.post(f"/v4/drafts/{did}/confirm", json={"handle": "kaoru_2026", **body})


def _page():
    return appmod.app.test_client().get("/register").get_data(as_text=True)


def _file(name):
    return open(os.path.join(ROOT, "docs", "prompts", name), encoding="utf-8").read()


def _pre(page, pid):
    m = re.search(rf'<pre id="{pid}">(.*?)</pre>', page, re.S)
    return htmlmod.unescape(m.group(1)) if m else None


# ── 228〜231 登録画面 ─────────────────────────────────────────────────────────────
def test_t228_two_prompts_dialogue_is_default():
    page = _page()
    a, b = page.index('id="promptA"'), page.index('id="promptB"')
    assert a < b                                                     # A が先（推奨・既定）
    assert "◉ 対話して作る（推奨）" in page and "○ 自分で書いて作る（簡易・任意）" in page
    details = page[page.rindex("<details", 0, b):b]
    assert "open" not in details.split(">")[0]                       # B は折りたたみ（既定は A）
    assert "末尾の『■ 本人の語り』に自分の記述を貼ってから AI に送ってください" in page


def test_t229_prompt_text_matches_files_exactly():
    page = _page()
    assert _pre(page, "promptA") == _file("v5_A_dialogue.txt")
    assert _pre(page, "promptB") == _file("v5_B_selfwrite.txt")
    tpl = open(os.path.join(ROOT, "templates", "register.html"), encoding="utf-8").read()
    assert "第一相の最優先原則" not in tpl and "{{ prompt_a }}" in tpl  # 本文はテンプレートに直書きしない


def test_t230_no_separate_narrative_field():
    page = _page()
    step2 = page[page.index("STEP 2"):]
    assert 'id="narrative"' not in step2 and "本人の語り（v5 のとき）" not in step2


def test_t231_handle_notice_near_step2():
    page = _page()
    s2, ta = page.index("STEP 2"), page.index('id="seekerJson"')
    note = page.index('id="handleNote"', s2)
    assert s2 < note < ta
    assert "ハンドルは後から変更できません。実名ではなく、公開されて構わない名前を選んでください。" in page[note:ta]


# ── 232・233 与え像の文数（0〜5）────────────────────────────────────────────────────
def test_t232_zero_offers_accepted(db):
    c = _cli()
    assert _draft(c, _doc(与え像=[])).status_code == 201
    d = _doc()
    del d["与え像"]                                                  # キーが無くても 0 文として受ける
    assert _draft(c, d).status_code == 201


def test_t233_six_or_more_offers_rejected(db):
    r = _draft(_cli(), _doc(与え像=[{"文": f"関われる{i}。", "型": "関わり方"} for i in range(6)]))
    assert r.status_code == 400 and "与え像は 0〜5 文" in r.get_json()["error"]


# ── 234〜237 与え像の確認（外せるが、足せない）───────────────────────────────────────
def test_t234_removed_offers_are_not_saved(db):
    c = _cli()
    did = _draft(c, _doc()).get_json()["draft_id"]
    assert _confirm(c, did, offer_keep=[0, 2]).status_code == 202
    kept = [x["文"] for x in v5.latest_offer("u_a", db_path=db)["sentences"]]
    assert kept == ["まだ形になっていない仕組みを試作として形にする立場で関われる。", "論点を構造として整理する役回りで関われる。"]
    assert [x["文"] for x in v5.get_doc("u_a", db_path=db)["与え像"]] == kept
    ev = le.get_events(type_="necessity.published", db_path=db)[-1]["payload"]
    assert ev["offer_hash"] == v5.offer_hash([{"文": t, "型": "関わり方"} for t in kept])   # c3 も残した文で


def test_t235_all_offers_removed_confirms_with_zero(db):
    c = _cli()
    did = _draft(c, _doc()).get_json()["draft_id"]
    assert _confirm(c, did, offer_keep=[]).status_code == 202
    assert v5.latest_offer("u_a", db_path=db)["sentences"] == []
    assert c.get("/api/my/purposes").get_json()["has_offer"] is False   # 0 文は現状で照合（v4 互換と同じ）


def test_t236_no_input_to_add_or_rewrite_offers(db):
    tpl = open(os.path.join(ROOT, "templates", "register.html"), encoding="utf-8").read()
    block = tpl[tpl.index("与え像の確認（指示書60 §3）"):tpl.index("すべて外した場合")]
    assert 'type="checkbox" class="offerKeep"' in block
    assert "<textarea" not in block and 'type="text"' not in block and "contenteditable" not in block
    assert "AI があなたの語りから読み取った『力になれること』です。この形で提供したくない文は外してください。文を足すことはできません。" in block
    # サーバーも足さない: 範囲外の番号・本文側から足した文は無視する
    c = _cli()
    did = _draft(c, _doc()).get_json()["draft_id"]
    _confirm(c, did, offer_keep=[1, 9, -1, True], 与え像=[{"文": "足した文。", "型": "資源"}])
    assert [x["文"] for x in v5.latest_offer("u_a", db_path=db)["sentences"]] == \
        ["照合の仕組みと、そこに集まる人と機会という場を提供できる。"]


def test_t237_removing_offers_does_not_bump_attempt(db):
    c = _cli()
    did = _draft(c, _doc()).get_json()["draft_id"]
    before = drafts.get_draft(did, db_path=db)["attempt_n"]
    r = _confirm(c, did, offer_keep=[0])
    assert r.get_json()["attempt_n"] == before == drafts.get_draft(did, db_path=db)["attempt_n"]
    ev = le.get_events(type_="necessity.published", db_path=db)[-1]["payload"]
    assert ev["attempt_n"] == before


# ── 238〜240 検証9（根拠が生テキストに実在）──────────────────────────────────────────────
def test_t238_evidence_matches_after_normalization(db):
    d = _doc()
    d["purposes"][0]["根拠"] = "AIと仕組みを作るのが、好きです"           # 全角半角・空白・句読点が違う
    assert _draft(_cli(), d).status_code == 201


def test_t239_missing_evidence_rejected_with_one_line(db):
    d = _doc()
    d["purposes"][0]["根拠"] = "語りに無い文章。"
    r = _draft(_cli(), d)
    msg = r.get_json()["error"]
    assert r.status_code == 400 and "根拠が生テキストに見つかりません" in msg and "\n" not in msg


def test_t240_evidence_saved_verbatim(db):
    raw = "ＡＩ と仕組みを作るのが好きです"
    d = _doc()
    d["purposes"][0]["根拠"] = raw
    c = _cli()
    did = _draft(c, d).get_json()["draft_id"]
    assert drafts.get_draft(did, db_path=db)["payload"]["purposes"][0]["根拠"] == raw
    _confirm(c, did)
    assert v5.get_doc("u_a", db_path=db)["purposes"][0]["根拠"] == raw     # 正規化した文字列ではない
    assert v5.get_doc("u_a", db_path=db)["supporting_material"]["生テキスト"] == [STORY]


# ── 241 p_sharpness・gamma は捨てる（拒否しない）───────────────────────────────────────
def test_t241_p_sharpness_and_gamma_dropped(db):
    d = _doc(p_sharpness=0.2, gamma=0.5)
    d["purposes"][0]["数値"].update(p_sharpness=-0.5, gamma=0.3)
    d["purposes"][0]["gamma"] = 0.1
    c = _cli()
    r = _draft(c, d)
    assert r.status_code == 201
    blob = json.dumps(drafts.get_draft(r.get_json()["draft_id"], db_path=db)["payload"], ensure_ascii=False)
    assert "p_sharpness" not in blob and "gamma" not in blob
    _confirm(c, r.get_json()["draft_id"])
    blob = json.dumps(v5.get_doc("u_a", db_path=db), ensure_ascii=False)
    assert "p_sharpness" not in blob and "gamma" not in blob


# ── 242 v4 の JSON は今までどおり ─────────────────────────────────────────────────────
def test_t242_v4_json_still_registers(db):
    v4 = {"id": "alice_2025", "schema_version": "v4",
          "seeker": {"意志": "つながりをつくる", "現状": {"持っているもの": "試作"}},
          "necessity": {"necessity_text": "実装できる人", "gate_s": 0.3, "gate_u": 0.3,
                        "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0, "evidence_span": ""}}
    c = _cli()
    r = _draft(c, v4)
    assert r.status_code == 201 and not r.get_json().get("v5")
    assert _confirm(c, r.get_json()["draft_id"]).status_code == 202


# ── 改訂2 §3 の 11・12（番号なし）───────────────────────────────────────────────────
@pytest.mark.parametrize("mut,why", [
    (lambda d: d.pop("id"), "id（ハンドル）"),
    (lambda d: d["supporting_material"].pop("一行紹介"), "一行紹介"),
    (lambda d: d["supporting_material"].update(要約文=" "), "要約文"),
    (lambda d: d["supporting_material"].update(生テキスト=[]), "生テキスト"),
])
def test_t60_rules_11_12(db, mut, why):
    d = _doc()
    mut(d)
    r = _draft(_cli(), d)
    assert r.status_code == 400 and why in r.get_json()["error"]


def test_t60_forbidden_wording_does_not_apply_to_quoted_evidence(db):
    """規則 8 は AI が書いた文だけを見る。本人の言葉の引用（根拠）に「〜年以上」等があっても弾かない。"""
    d = _doc(supporting_material={"一行紹介": "x", "要約文": "y", "生テキスト": ["現場に10年以上いた。"]})
    d["purposes"][0]["根拠"] = "現場に10年以上いた。"
    assert _draft(_cli(), d).status_code == 201
