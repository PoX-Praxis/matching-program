"""指示書55-5 — 承認の場面の根拠・申し出の文の表示・入力欄の統一（意志の下限は指示書56 で撤去）。"""
import json, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import ledger
import match_config
from db_v4 import MemoryStore
from embedding_config import MODEL_TAG
from matcher_v4 import passes_entry, public_axis
from necessity_gen import compute_gamma


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


def _vecs(x):
    return {k: list(x) for k in ("will_symmetric", "will_passage", "state_passage", "necessity_query")}


@pytest.fixture
def v4(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    store = MemoryStore()
    for pid, v in (("me", [1.0, 0.0]), ("c1", [0.8, 0.6]), ("c2", [0.8, 0.6])):
        store.save_profile(pid, fields={"will_text": "w", "state_have": "s"}, supporting_raw={},
                           supporting_redacted={}, pii_redaction_status="none", migrated_from=None,
                           generation_status="ready")
        store.save_necessity(pid, MODEL_TAG, {"necessity_text": f"必要像{pid}", "gate_s": 0.9, "gate_u": 0.3,
                                              "p_sharpness": 0.0, "alpha": 1.2, "beta": 1.0,
                                              "gamma": compute_gamma(0.9, 0.3), "evidence_span": ""})
        store.save_vectors(pid, MODEL_TAG, _vecs(v))
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)
    monkeypatch.setattr(appmod, "_v4_store", lambda: store)
    monkeypatch.setattr(appmod, "_matching_available", lambda: True)
    monkeypatch.setattr(appmod, "_linked_ids", lambda ids: set(ids))
    monkeypatch.setattr(appmod, "_seeker_live_necessity_id", lambda sid, db_path=None: None)
    import migrate_v4
    monkeypatch.setattr(migrate_v4, "ensure_migrated", lambda *a, **k: None)
    return store


def _pending(frm, to):
    with ledger._connect(appmod.DB) as con:
        con.execute("INSERT INTO connection_requests (id, from_subject, to_subject, status, created_at, offer_message) "
                    "VALUES (%s,%s,%s,'pending',%s,%s)", (f"r_{frm}_{to}", frm, to, "2026-10-04T00:00:00Z", "理由の文"))


# ── §1 承認の場面の根拠 ─────────────────────────────────────────────────────────
def test_reason_for_incoming_offer(v4):
    _pending("c1", "me")
    assert [r["candidate_id"] for r in _cli("me").post("/v4/match", json={}).get_json()["results"]] == ["c2"]  # 186
    d = _cli("me").get("/api/connections/reason?with=c1").get_json()
    r = d["reason"]
    assert r["axis"] in {"will", "fill", "mutual"} and r["reasons"]
    blob = json.dumps(d, ensure_ascii=False)
    for k in ("score", "sim", "gate", "alpha", "0."):
        assert k not in blob, k                                                # 数値を出さない


def test_reason_only_for_engaged_pairs(v4):
    assert _cli().get("/api/connections/reason?with=c1").status_code == 401
    assert _cli("me").get("/api/connections/reason?with=c2").status_code == 403   # 申し出も接続も無い相手
    _pending("me", "c2")
    assert _cli("me").get("/api/connections/reason?with=c2").status_code == 200    # 自分の申し出中も可
    assert _cli("c1").get("/api/connections/reason?me=me&with=c2").status_code == 403   # 他人の分は引けない


def test_reason_absent_when_matching_unavailable(v4, monkeypatch):
    _pending("c1", "me")
    monkeypatch.setattr(appmod, "_matching_available", lambda: False)
    assert _cli("me").get("/api/connections/reason?with=c1").get_json() == {"reason": None}


def test_approval_screens_show_reason_and_offer_message():
    """承認の場面（根拠・申し出の文）は受信箱だけ（指示書63 段階1 §4-3 でマイページ・プロフィールから外した）。"""
    tpl = lambda n: open(os.path.join(ROOT, "templates", n), encoding="utf-8").read()
    t = tpl("inbox.html")
    assert '{% include "_match_reason.html" %}' in t
    assert "PoXReason.fetchReason" in t and "PoXReason.offerHtml" in t          # §2 申し出の文
    for name in ("mypage.html", "profile.html"):                                 # 受信箱へ案内するだけ
        t = tpl(name)
        assert 'href="/inbox"' in t and "PoXReason.fetchReason" not in t, name
    part = tpl("_match_reason.html")
    assert "/api/connections/reason" in part and "/api/my/offers" in part


# ── §3 申し出の文の入力欄は経路によらない ─────────────────────────────────────────
def test_connect_card_has_offer_message_box():
    t = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert "PoXReason.composeHtml" in t and "message: ((box && box.value)" in t
    part = open(os.path.join(ROOT, "templates", "_match_reason.html"), encoding="utf-8").read()
    assert 'maxlength="400"' in part and "任意・400字まで" in part


# §4（意志の下限）・§5 の「意志も見る相互」は指示書56 で撤去（tests/test_t56_matching_stage1.py の 201・199）
