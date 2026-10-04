"""指示書55-5 — 承認の場面の根拠・申し出の文の表示・入力欄の統一・意志の下限・「互いに埋める」。"""
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
from matcher_v4 import (will_requirement, will_required, will_floor, passes_entry, public_axis,
                        rank_candidates)
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
    tpl = lambda n: open(os.path.join(ROOT, "templates", n), encoding="utf-8").read()
    for name in ("inbox.html", "mypage.html", "profile.html"):
        t = tpl(name)
        assert '{% include "_match_reason.html" %}' in t, name
        assert "PoXReason.fetchReason" in t or "fillReasons" in t, name
        assert "PoXReason.offerHtml" in t, name                                  # §2 申し出の文
    part = tpl("_match_reason.html")
    assert "/api/connections/reason" in part and "/api/my/offers" in part


# ── §3 申し出の文の入力欄は経路によらない ─────────────────────────────────────────
def test_connect_card_has_offer_message_box():
    t = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert "PoXReason.composeHtml" in t and "message: ((box && box.value)" in t
    part = open(os.path.join(ROOT, "templates", "_match_reason.html"), encoding="utf-8").read()
    assert 'maxlength="400"' in part and "任意・400字まで" in part


# ── §4 意志の下限（gate_s × (1 − gate_u)）──────────────────────────────────────────
def test_will_requirement_combines_s_and_u():
    assert will_requirement(0.9, 0.3) == pytest.approx(0.63)
    assert will_required(0.9, 0.3) is True
    assert will_required(0.9, 0.6) is False          # 不確実性が高い人には立てない（広げる設計と逆行しない）
    assert will_required(0.0, 0.0) is False and will_required(0.3, 0.0) is False


def test_will_floor_is_unset_until_measured(monkeypatch):
    assert match_config.WILL_FLOOR_G is None and will_floor(0.9, 0.3) is None
    monkeypatch.setattr(match_config, "WILL_FLOOR_G", 0.8)
    assert will_floor(0.9, 0.3) == 0.8 and will_floor(0.9, 0.6) is None


def test_will_floor_excludes_when_set(v4, monkeypatch):
    assert [r["candidate_id"] for r in _cli("me").post("/v4/match", json={}).get_json()["results"]] == ["c1", "c2"]
    monkeypatch.setattr(match_config, "WILL_FLOOR_G", 0.95)                    # ga=0.9 の相手は下限未満
    assert _cli("me").post("/v4/match", json={}).get_json()["results"] == []
    r = {"score": 0.9, "score_b": 0.9, "attribution": {"ga": 0.9}}
    assert passes_entry(r) and not passes_entry(r, floor=0.95)


# ── §5 「足りないところを互いに埋める」は意志も見る ───────────────────────────────
def test_mutual_requires_will_for_required_people():
    attr = {"ga": 0.6, "gb": 0.8, "gc": 0.6, "gd": 0.8, "c_log_contrib": 0.0}
    assert public_axis(attr) == "mutual"                                       # 必須でない人は従来どおり
    assert public_axis(attr, require_will=True) != "mutual"                    # 必須の人では意志が低いと出さない
    assert public_axis({**attr, "ga": 0.75}, require_will=True) == "mutual"
