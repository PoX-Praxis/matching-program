"""指示書41 段階6（§6）— コミュニティとしてプロジェクトに参加する。

参加するコミュニティが自分の提議トークで合意（purpose.agreed with target_intent_id）→
その event_hash を consent_ref として participant.joined(kind=community) が刻まれる。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
import ledger_events as le


def _client():
    os.environ.pop("POX_DEBUG", None)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _login(c, sid):
    with c.session_transaction() as s:
        s["subject_id"] = sid


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        _login(c, sid)
    return c


def _make_community(founder):
    c = _client() if founder == "u_alice" else _cli(founder)
    # 最初の呼び出しだけ appmod.DB を張る（_client）。以降は同じ DB を共有。
    _login(c, founder)
    return c.post("/api/communities", json={"name": "n", "founder_id": founder}).get_json()["id"]


def _proposal_agree(cid, member, target=None):
    tk = _cli(member).post(f"/api/community/{cid}/talks",
                           json={"kind": "proposal", "title": "p", "target": target or {}}).get_json()["talk_id"]
    _cli(member).post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    return _cli(member).get(f"/api/talks/{tk}").get_json()


def test_community_joins_project_as_participant():
    # コミュニティA（founder alice）がプロジェクトを立ち上げる
    cidA = _make_community("u_alice")
    pA = _proposal_agree(cidA, "u_alice")
    launch = _cli("u_alice").post(f"/api/community/{cidA}/projects/launch",
                                  json={"title": "PJ", "purpose_ref": pA["result"]["purpose_event_hash"]}).get_json()
    intent_id = launch["intent_id"]

    # コミュニティB（founder bob）が「この intent に参加する」ことに内部合意
    cidB = _cli("u_bob")
    _login(cidB, "u_bob")
    cidB = cidB.post("/api/communities", json={"name": "B", "founder_id": "u_bob"}).get_json()["id"]
    pB = _proposal_agree(cidB, "u_bob", target={"target_intent_id": intent_id})
    consent_ref = pB["result"]["purpose_event_hash"]

    # B のメンバーが、B としてプロジェクトへの参加を起票（consent_ref を渡す）
    r = _cli("u_bob").post(f"/api/projects/{intent_id}/join-as-community",
                           json={"community_id": cidB, "consent_ref": consent_ref})
    assert r.status_code == 201
    talk = r.get_json()["talk"]

    # プロジェクト側の既存参加者（launcher=alice）が賛成 → 成立
    _cli("u_alice").post(f"/api/talks/{talk['talk_id']}/vote", json={"stance": "approve"})

    # participant.joined（kind=community・consent_ref つき）が台帳に刻まれる（§6）
    pj = [e["payload"] for e in le.get_events(type_="intent.participant.joined", db_path=appmod.DB)
          if e["payload"].get("participant") == cidB]
    assert len(pj) == 1
    assert pj[0]["participant_kind"] == "community"
    assert pj[0]["consent_ref"] == consent_ref
    assert pj[0]["intent_id"] == intent_id


def test_invalid_consent_ref_rejected():
    cidA = _make_community("u_alice")
    pA = _proposal_agree(cidA, "u_alice")
    launch = _cli("u_alice").post(f"/api/community/{cidA}/projects/launch",
                                  json={"title": "PJ", "purpose_ref": pA["result"]["purpose_event_hash"]}).get_json()
    intent_id = launch["intent_id"]
    cidB = _cli("u_bob")
    _login(cidB, "u_bob")
    cidB = cidB.post("/api/communities", json={"name": "B", "founder_id": "u_bob"}).get_json()["id"]
    # B が target_intent_id を持たない合意（無効な consent）
    pB = _proposal_agree(cidB, "u_bob")   # target_intent_id 無し
    r = _cli("u_bob").post(f"/api/projects/{intent_id}/join-as-community",
                           json={"community_id": cidB, "consent_ref": pB["result"]["purpose_event_hash"]})
    assert r.status_code == 400
