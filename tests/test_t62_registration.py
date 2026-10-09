"""指示書62 — 登録不能の再発防止（プロンプト rev4・補正の範囲・登録画面の案内）。テスト 268〜276。"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import drafts  # noqa: E402
import ledger_events as le  # noqa: E402
from profile_view import parse_registration_text  # noqa: E402

RAW = ["私は仕組みを作るのが好きです。", "事業を一緒に立ち上げる人がほしい。", "論理の穴を指摘してくれる人も。"]


def _doc(source="v5r4-A", ev="私は仕組みを作るのが好きです。／事業を一緒に立ち上げる人がほしい。"):
    return {
        "id": "kaoru_2026", "schema_version": "v5", "generator": "GPT-6", "_meta": {"source": source},
        "purposes": [
            {"purpose_id": "p1", "向かう先": "自然に辿り着ける状態を実現したい。", "手段": "照合の仕組みを根づかせる。",
             "必要像": [{"文": "事業を立ち上げる局面で、構想を実行に移す側として関わってきた人。", "必須": True, "型": "関わり方"}],
             "数値": {"gate_s": 0.3, "gate_u": 0.3}, "根拠": ev}],
        "与え像": [{"文": "仕組みを試作として形にする立場で関われる。", "型": "関わり方"},
                 {"文": "照合の場を提供できる。", "型": "資源"}],
        "関心": [{"文": "構造に関心がある。"}],
        "現状": {"持っているもの": "試作", "できること_型": "形にする", "縛られているもの": "時間", "未分類": ""},
        "supporting_material": {"一行紹介": "つなぐ仕組みをつくる", "意志_なぜ": "構造が阻むべきではない。",
                                "要約文": "仕組みを作る人。", "生テキスト": list(RAW)},
    }


def _one_line(doc):
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def _damaged(doc):
    """実例と同じ形式の壊れ方: purposes が `\\[` `\\]`・与え像と生テキストの `[` `]` が無い。"""
    s = _one_line(doc)
    s = s.replace('"purposes":[', '"purposes":\\[', 1)
    s = s.replace(']}],"与え像"', ']}\\],"与え像"', 1)
    offers = _one_line(doc["与え像"])
    s = s.replace('"与え像":' + offers, '"与え像":' + offers[1:-1], 1)
    raw = _one_line(doc["supporting_material"]["生テキスト"])
    s = s.replace('"生テキスト":' + raw, '"生テキスト":' + raw[1:-1], 1)
    return s


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.setattr(appmod, "_v5_sentence_job", lambda *a, **k: None)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
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


def _post(text, sid="u_a"):
    return _cli(sid).post("/v4/drafts", json={"raw_text": text})


# ── 268〜270 rev4・rev3・source ─────────────────────────────────────────────────────
def test_t268_rev4_code_block_registers(db):
    r = _post("```json\n" + _one_line(_doc()) + "\n```")
    assert r.status_code == 201 and "repairs" not in r.get_json()      # 包みの除去は補正の告知に入れない


def test_t269_rev3_without_code_block_registers(db):
    r = _post(_one_line(_doc(source="v5r3-A")))
    assert r.status_code == 201


@pytest.mark.parametrize("src,ok", [("v5r4-A", True), ("v5r4-B", True), ("v5r3-A", True), ("v5r3-B", True),
                                    ("v5r2-A", True), ("v5r9-A", False)])
def test_t270_meta_source_values(db, src, ok):
    r = _post(_one_line(_doc(source=src)))
    assert (r.status_code == 201) is ok
    if not ok:
        assert "_meta.source" in r.get_json()["error"]                  # 現行どおり（一覧に無い値は理由を返す）


# ── 271〜273 根拠の区切り ─────────────────────────────────────────────────────────
def test_t271_slash_separated_quotes_all_present(db):
    assert _post(_one_line(_doc(ev="私は仕組みを作るのが好きです。／論理の穴を指摘してくれる人も。"))).status_code == 201


def test_t272_newline_separated_quotes_still_pass(db):
    assert _post(_one_line(_doc(ev="私は仕組みを作るのが好きです。\n論理の穴を指摘してくれる人も。"))).status_code == 201


def test_t273_missing_quote_rejected_without_auto_add(db):
    r = _post(_one_line(_doc(ev="私は仕組みを作るのが好きです。／生テキストに無い一文。")))
    msg = r.get_json()["error"]
    assert r.status_code == 400 and "「生テキストに無い一文。」" in msg and "\n" not in msg
    assert "根拠に使った言葉を生テキストにも入れて出し直して" in msg     # 次にすること
    assert drafts.list_drafts("u_a", db_path=db) == []                   # 下書きを作らない（生テキストへの自動追加もしない）


# ── 274〜276 形式の補正と告知 ───────────────────────────────────────────────────────
def test_t274_format_repaired_with_notice_and_counts(db):
    r = _post(_damaged(_doc()))
    d = r.get_json()
    assert r.status_code == 201
    assert any("\\[" in k for k in d["repairs"]) and any("配列" in k for k in d["repairs"])
    assert d["array_counts"] == {"purposes": 1, "必要像": [1], "与え像": 2, "関心": 1, "生テキスト": 3}
    tpl = open(os.path.join(ROOT, "templates", "register.html"), encoding="utf-8").read()
    assert "形式を自動で補正しました" in tpl and "内容に変わりがないか確認してください" in tpl and "読み取った数" in tpl


def test_t275_no_notice_for_valid_json(db):
    for text in (_one_line(_doc()), json.dumps(_doc(), ensure_ascii=False, indent=2)):
        d = _post(text).get_json()
        assert "repairs" not in d and "array_counts" not in d


def test_t276_repair_does_not_affect_ledger(db):
    assert parse_registration_text(_damaged(_doc())) == parse_registration_text(_one_line(_doc()))
    c = _cli()
    d1 = _post(_damaged(_doc())).get_json()
    assert "repairs" not in json.dumps(d1["payload"], ensure_ascii=False)   # 下書きに補正の印を残さない
    assert c.post(f"/v4/drafts/{d1['draft_id']}/confirm", json={"handle": "kaoru_2026"}).status_code == 202
    before = le.get_events(db_path=db)
    d2 = _post(_one_line(_doc())).get_json()
    r2 = c.post(f"/v4/drafts/{d2['draft_id']}/confirm", json={})
    assert r2.status_code == 202 and r2.get_json()["unchanged"] is True    # 補正あり・なしで同じハッシュ（台帳に足されない）
    after = le.get_events(db_path=db)
    assert len(after) == len(before)
    blob = json.dumps([e["payload"] for e in after], ensure_ascii=False)
    assert "repair" not in blob and "補正" not in blob


def test_t62_copy_guidance_and_parse_error_next_step(db):
    """登録画面の案内（コピーボタン）と、読めなかったときの次の一手（番号なし）。"""
    page = appmod.app.test_client().get("/register").get_data(as_text=True)
    assert "右上のコピーボタン" in page and "記号が欠けることがあります" in page
    r = _post('{"id": "x", "purposes": [ {"a": 1,, } ]}')
    msg = r.get_json()["error"]
    assert r.status_code == 400 and "コードブロックの中に1行で出し直して" in msg and "\n" not in msg
