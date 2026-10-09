"""指示書55-2 PR-C — 接続: 取り下げ・申し出の文・DM は接続後・終了（台帳に触る単独 PR）。

173 取り下げ（台帳は増えない）／174 二重申請は 200・画面に「申請済み」／175 終了（reason なし・日付と「終了」のみ）
176 connection.established／closed が型一覧に載っている／177 接続前は DM を送れない
180 申し出の文（任意・上限）／181 受けた本人だけが見る／182 取り下げで文も消える
195 connection.closed の payload は {a, b, by}（過去の行 0 件を確認したうえでの確定）
"""
import json, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import ledger
import ledger_events as le


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    # 根拠（profile.structured）の有無は成立の条件だが、この PR の関心ではないので満たしておく
    monkeypatch.setattr(appmod, "_conn_ref_resolver",
                        lambda s: {"profile_snapshot_hash": f"h_{s}", "necessity_hash": None})
    return appmod.DB


def _offer(frm, to, message=None):
    body = {"to_id": to}
    if message is not None:
        body["message"] = message
    return _cli(frm).post("/approve", json=body)


def _connect(a, b):
    _offer(a, b); _offer(b, a)
    assert ledger.is_connected(a, b, db_path=appmod.DB)


def _n_events(db):
    return len(le.get_events(db_path=db))


# ── 173・182 取り下げ ─────────────────────────────────────────────────────────
def test_t173_t182_withdraw_removes_request_and_message(db):
    _offer("u_a", "u_b", "一緒に翻訳の仕組みを作りたい")
    n = _n_events(db)
    assert ledger.connection_state("u_a", "u_b", db_path=db) == "pending_out"
    r = _cli("u_a").post("/api/connections/withdraw", json={"to_id": "u_b"})
    assert r.status_code == 200 and r.get_json()["withdrawn"] is True
    assert ledger.connection_state("u_a", "u_b", db_path=db) == "none"
    assert _n_events(db) == n                                                 # 台帳は増えない
    assert ledger.received_offers("u_b", db_path=db) == []                    # 182: 文も消える
    again = _cli("u_a").post("/api/connections/withdraw", json={"to_id": "u_b"})
    assert again.status_code == 200 and again.get_json()["withdrawn"] is False   # 冪等
    assert _cli().post("/api/connections/withdraw", json={"to_id": "u_b"}).status_code == 401


def test_t173_cannot_withdraw_others_request(db):
    _offer("u_a", "u_b")
    assert _cli("u_c").post("/api/connections/withdraw",
                            json={"from_id": "u_a", "to_id": "u_b"}).status_code == 403
    _cli("u_b").post("/api/connections/withdraw", json={"to_id": "u_a"})     # 自分の申し出ではない
    assert ledger.connection_state("u_a", "u_b", db_path=db) == "pending_out"


# ── 174 二重申請は 200・画面に「申請済み」──────────────────────────────────────
def test_t174_double_offer_is_idempotent_and_shows_pending(db):
    assert _offer("u_a", "u_b").status_code == 200
    assert _offer("u_a", "u_b").status_code == 200
    s = _cli("u_a").get("/api/connections/state?with=u_b").get_json()["state"]
    assert s == "pending_out"
    assert _cli("u_b").get("/api/connections/state?with=u_a").get_json()["state"] == "pending_in"
    html = open(os.path.join(ROOT, "templates", "profile.html"), encoding="utf-8").read()
    assert "申請済みです" in html and "取り下げる" in html
    mp = open(os.path.join(ROOT, "templates", "mypage.html"), encoding="utf-8").read()
    assert "申請済み（" in mp and "withdrawReq" in mp


# ── 175・195 終了 ─────────────────────────────────────────────────────────────
def test_t175_t195_close_writes_a_b_by_without_reason(db):
    _connect("u_a", "u_b")
    assert _cli("u_c").post("/api/connections/close", json={"with": "u_b"}).status_code == 409   # 当事者でない
    r = _cli("u_b").post("/api/connections/close", json={"with": "u_a"})
    assert r.status_code == 200
    ev = le.get_events(type_="connection.closed", db_path=db)
    assert len(ev) == 1 and set(ev[0]["payload"]) == {"a", "b", "by"}          # reason は書かない
    assert ev[0]["payload"]["by"] == "u_b"
    assert not ledger.is_connected("u_a", "u_b", db_path=db)
    assert _cli("u_b").post("/api/connections/close", json={"with": "u_a"}).status_code == 409   # 二重は 409
    assert le.verify_chain(db_path=db)["ok"] is True


def test_t175_display_is_date_and_end_only(db):
    _connect("u_a", "u_b")
    _cli("u_a").post("/api/connections/close", json={"with": "u_b"})
    vs = _cli("u_b").get("/api/my/vessels?id=u_b").get_json()
    blob = json.dumps(vs, ensure_ascii=False)
    assert "reason" not in blob and '"by"' not in blob                         # 誰が終了したかを返さない
    mp = open(os.path.join(ROOT, "templates", "mypage.html"), encoding="utf-8").read()
    assert "終了 ${(j.closed_at" in mp


def test_t195_close_connection_has_no_reason_parameter():
    import inspect
    assert "reason" not in inspect.signature(ledger.close_connection).parameters


# ── 176 型一覧への追記 ────────────────────────────────────────────────────────
def test_t176_connection_types_are_listed():
    doc = open(os.path.join(ROOT, "docs", "ledger_limits.md"), encoding="utf-8").read()
    row = next(l for l in doc.splitlines() if "`connection.closed`" in l and "41 §4" in l)
    assert "`connection.established`" in row and "{a, b, by}" in row


# ── 177 接続前は DM を送れない ────────────────────────────────────────────────
def test_t177_dm_requires_connection(db):
    r = _cli("u_a").post("/messages", json={"to_id": "u_b", "body": "こんにちは"})
    assert r.status_code == 403
    _offer("u_a", "u_b")
    assert _cli("u_a").post("/messages", json={"to_id": "u_b", "body": "x"}).status_code == 403   # 申し出中も不可
    _offer("u_b", "u_a")
    assert _cli("u_a").post("/messages", json={"to_id": "u_b", "body": "よろしく"}).status_code == 201
    _cli("u_a").post("/api/connections/close", json={"with": "u_b"})
    assert _cli("u_a").post("/messages", json={"to_id": "u_b", "body": "x"}).status_code == 403   # 終了後も不可


# ── 180・181 申し出の文 ───────────────────────────────────────────────────────
def test_t180_offer_message_optional_and_limited(db):
    assert _offer("u_a", "u_b").status_code == 200                            # 任意
    assert _offer("u_c", "u_b", "あ" * 401).status_code == 400                 # 上限 400 字
    assert _offer("u_c", "u_b", "あ" * 400).status_code == 200
    _offer("u_c", "u_b", "書き直し")                                            # 1 人 1 通（上書きしない）
    msgs = {o["from"]: o["message"] for o in ledger.received_offers("u_b", db_path=db)}
    assert msgs == {"u_a": "", "u_c": "あ" * 400}


def test_t181_only_recipient_reads_offer_message(db):
    _offer("u_a", "u_b", "秘密の申し出の文")
    mine = _cli("u_b").get("/api/my/offers").get_json()
    assert [o["message"] for o in mine] == ["秘密の申し出の文"]
    assert _cli("u_c").get("/api/my/offers?id=u_b").status_code == 403       # 第三者は読めない
    assert _cli("u_c").get("/api/my/offers").get_json() == []
    for path in ("/api/my/vessels?id=u_a", "/api/my/vessels?id=u_b"):
        who = path.rsplit("=", 1)[1]
        assert "秘密の申し出の文" not in _cli(who).get(path).get_data(as_text=True)
    assert "秘密の申し出の文" not in _cli().get("/api/profile/u_a").get_data(as_text=True)
    assert "秘密の申し出の文" not in _cli().get("/api/timeline/u_a").get_data(as_text=True)


def test_t180_offer_message_stays_at_top_of_conversation_and_can_be_retracted(db):
    _offer("u_a", "u_b", "つながりたい理由")
    _offer("u_b", "u_a")
    conv = _cli("u_b").get("/api/conversation?with=u_a").get_json()
    assert conv[0]["kind"] == "offer" and conv[0]["body"] == "つながりたい理由"
    r = _cli("u_a").post("/api/connections/offer-message/retract", json={"to_id": "u_b"})
    assert r.get_json()["retracted"] is True
    assert all(m.get("kind") != "offer" for m in _cli("u_b").get("/api/conversation?with=u_a").get_json())
