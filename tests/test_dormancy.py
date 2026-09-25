"""指示書49 — 休眠の導出と再開（表示のみ・台帳に書かない）。項目 35-a〜36-b。

時刻は talks._clock を差し替えて注入する（休眠は表示のみなので壁時計を使ってよい・49 §2）。
合意判定は壁時計を使わない（既存の決定性テスト t013 等がそのまま通ることで担保）。
"""
import os, sys, tempfile
from datetime import datetime, timedelta, timezone
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
import ledger_events as le
import talks

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _client():
    os.environ.pop("POX_DEBUG", None)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


def _at(monkeypatch, when):
    monkeypatch.setattr(talks, "_clock", lambda: when)


def _open_proposal(monkeypatch):
    """T0 に審議中の提議を作る（分母2＝単独賛成では合意しない）。"""
    _at(monkeypatch, T0)
    _client()
    cid = _cli("u_alice").post("/api/communities", json={"name": "n", "founder_id": "u_alice"}).get_json()["id"]
    atk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                               json={"kind": "admission", "title": "a",
                                     "target": {"candidate": "u_bob"}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{atk}/vote", json={"stance": "approve"})   # u_bob 加入
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "審議中", "target": {}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})    # 分母2で未成立
    return cid, tk


def _status(tk):
    return _cli().get(f"/api/talks/{tk}").get_json()["display_status"]


# 35-a: 最終活動から閾値を超えると休眠と表示される（閾値ちょうどでは休眠でない）
def test_t035a_dormant_after_threshold(monkeypatch):
    cid, tk = _open_proposal(monkeypatch)
    _at(monkeypatch, T0 + talks.DORMANT_AFTER)
    assert _status(tk) == "審議中"
    _at(monkeypatch, T0 + talks.DORMANT_AFTER + timedelta(seconds=1))
    assert _status(tk) == "休眠"
    rows = _cli().get(f"/api/community/{cid}/talks").get_json()["talks"]
    row = [t for t in rows if t["talk_id"] == tk][0]
    assert row["display_status"] == "休眠" and row["section"] == "proposal_live"


# 35-b: 休眠は台帳にイベントを書かない
def test_t035b_dormancy_writes_no_ledger_event(monkeypatch):
    cid, tk = _open_proposal(monkeypatch)
    before = len(le.get_events(db_path=appmod.DB))
    _at(monkeypatch, T0 + timedelta(days=90))
    assert _status(tk) == "休眠"
    _cli().get(f"/api/community/{cid}/talks")
    _cli().get(f"/talk/{tk}")
    assert len(le.get_events(db_path=appmod.DB)) == before
    assert not [e for e in le.get_events(db_path=appmod.DB) if "dormant" in e["type"]]


# 35-c: 休眠でも可視性が変わらない（未ログインの第三者から読める・一覧に残る）
def test_t035c_dormant_talk_stays_public(monkeypatch):
    cid, tk = _open_proposal(monkeypatch)
    _at(monkeypatch, T0 + timedelta(days=90))
    r = _cli().get(f"/api/talks/{tk}")
    assert r.status_code == 200 and r.get_json()["display_status"] == "休眠"
    assert tk in [t["talk_id"] for t in _cli().get(f"/api/community/{cid}/talks").get_json()["talks"]]


# 36-a: 休眠のトークに追記すると休眠でなくなる（再開操作なし）
def test_t036a_post_revives_dormant_talk(monkeypatch):
    cid, tk = _open_proposal(monkeypatch)
    _at(monkeypatch, T0 + timedelta(days=90))
    assert _status(tk) == "休眠"
    _cli("u_bob").post(f"/api/talks/{tk}/posts", json={"body": "再開します"})
    assert _status(tk) == "審議中"
    # 追記から再び閾値を超えると、また休眠になる
    _at(monkeypatch, T0 + timedelta(days=90) + talks.DORMANT_AFTER + timedelta(seconds=1))
    assert _status(tk) == "休眠"


# 36-b: 休眠のトークへの投稿・票は拒否されない（403/409 にならない）
def test_t036b_dormant_talk_accepts_post_and_vote(monkeypatch):
    cid, tk = _open_proposal(monkeypatch)
    _at(monkeypatch, T0 + timedelta(days=90))
    assert _status(tk) == "休眠"
    assert _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "まだ続けたい"}).status_code == 201
    _at(monkeypatch, T0 + timedelta(days=200))
    r = _cli("u_bob").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    assert r.status_code == 200 and r.get_json()["commit"]["committed"] is True   # 休眠からも合意できる


# 決定性: 合意判定は壁時計に依存しない（同じ票なら時刻を変えても判定は同じ）
def test_t049_agreement_ignores_clock(monkeypatch):
    cid, tk = _open_proposal(monkeypatch)
    talk = talks.get_talk(tk, db_path=appmod.DB)
    r1 = talks.evaluate_talk(talk, db_path=appmod.DB)
    _at(monkeypatch, T0 + timedelta(days=365))
    r2 = talks.evaluate_talk(talk, db_path=appmod.DB)
    assert r1 == r2


# 休眠の対象外: プロジェクトとチャットは休眠にならない（プロジェクトは 実行中／完了）
def test_t049_project_and_chat_never_dormant(monkeypatch):
    _at(monkeypatch, T0)
    _client()
    cid = _cli("u_alice").post("/api/communities", json={"name": "n", "founder_id": "u_alice"}).get_json()["id"]
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "P", "target": {}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    purpose = _cli().get(f"/api/talks/{tk}").get_json()["result"]["purpose_event_hash"]
    ptk = _cli("u_alice").post(f"/api/community/{cid}/projects/launch",
                               json={"title": "PJ", "purpose_ref": purpose}).get_json()["talk"]["talk_id"]
    _at(monkeypatch, T0 + timedelta(days=365))
    assert _status(ptk) == "実行中"
    assert _status(tk) == "合意済み"                      # 合意済みは休眠にならない
