"""指示書55-4 #122-a — 接続のうち台帳に触らない部分: 取り下げ・申し出の文・DM は接続後。

173 取り下げ（台帳は増えない）／174 二重申請は 200・画面に「申請済み」／177 接続前は DM を送れない
180 申し出の文（任意・上限）／181 受けた本人だけが見る／182 取り下げで文も消える
（終了の API と connection.closed の payload 確定＝175・176・195 は #122-b）
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


# ── 177 接続前は DM を送れない ────────────────────────────────────────────────
def test_t177_dm_requires_connection(db):
    r = _cli("u_a").post("/messages", json={"to_id": "u_b", "body": "こんにちは"})
    assert r.status_code == 403
    _offer("u_a", "u_b")
    assert _cli("u_a").post("/messages", json={"to_id": "u_b", "body": "x"}).status_code == 403   # 申し出中も不可
    _offer("u_b", "u_a")
    assert _cli("u_a").post("/messages", json={"to_id": "u_b", "body": "よろしく"}).status_code == 201


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
