"""指示書47 — プロジェクト単位の参加（P-1）。項目番号つき受入テスト（108-d〜108-j）。

台帳は既存の intent.participant.joined のみ。申し出・合意・見送りは公開。分母は基準点の参加者頭数。
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


def _project(founder="u_alice"):
    """コミュニティ作成→提議合意→プロジェクト立ち上げ。intent_id と project talk_id を返す。"""
    c = _client(); _login(c, founder)
    cid = c.post("/api/communities", json={"name": "n", "founder_id": founder}).get_json()["id"]
    tk = _cli(founder).post(f"/api/community/{cid}/talks",
                            json={"kind": "proposal", "title": "P", "target": {}}).get_json()["talk_id"]
    _cli(founder).post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    purpose = _cli(founder).get(f"/api/talks/{tk}").get_json()["result"]["purpose_event_hash"]
    lr = _cli(founder).post(f"/api/community/{cid}/projects/launch",
                            json={"title": "PJ", "purpose_ref": purpose}).get_json()
    return cid, lr["intent_id"], lr["talk"]["talk_id"]


def _offer(cid, intent_id, who, kind="individual"):
    return _cli(who).post(f"/api/community/{cid}/talks",
                          json={"kind": "project_join", "title": "参加の申し出",
                                "target": {"intent_id": intent_id, "participant": who,
                                           "participant_kind": kind}})


# 108-d: 非メンバー（ログイン済み）が参加を申し出られる（一覧表示だけでない）
def test_t108d_nonmember_can_offer_to_join():
    cid, iid, _ = _project()
    r = _offer(cid, iid, "u_ext")            # u_ext はコミュニティ非メンバー
    assert r.status_code == 201


# 108-e: 申し出→合意の後に intent.participant.joined が書かれる
def test_t108e_participant_joined_written():
    cid, iid, _ = _project()
    jtk = _offer(cid, iid, "u_ext").get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"})
    _cli("u_ext").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"})
    pj = [e["payload"] for e in le.get_events(type_="intent.participant.joined", db_path=appmod.DB)
          if e["payload"]["participant"] == "u_ext"]
    assert pj and pj[0]["intent_id"] == iid and pj[0]["participant_kind"] == "individual"


# 108-g: 最初の外部の申し出は 立ち上げ者（分母の初期値）＋当人 の合意で成立
def test_t108g_denominator_initial_is_launcher():
    cid, iid, _ = _project()
    jtk = _offer(cid, iid, "u_ext").get_json()["talk_id"]
    d = _cli().get(f"/api/talks/{jtk}").get_json()      # 参加トークは公開＝未ログインでも読める
    ids = sorted(x["subject_id"] for x in d["denominator"])
    assert ids == ["u_alice", "u_ext"]                  # 立ち上げ者 u_alice ＋ 申し出た u_ext（頭数2）


# 108-c/108-h: 申し出・合意は公開 ／ 未ログインでは申し出られない
def test_t108h_unauthenticated_cannot_offer():
    cid, iid, _ = _project()
    r = appmod.app.test_client().post(f"/api/community/{cid}/talks",
                                      json={"kind": "project_join", "title": "x",
                                            "target": {"intent_id": iid, "participant": "u_x"}})
    assert r.status_code == 401                         # 行為はログイン必須


def test_t108c_join_talk_is_public():
    cid, iid, _ = _project()
    jtk = _offer(cid, iid, "u_ext").get_json()["talk_id"]
    assert _cli().get(f"/api/talks/{jtk}").status_code == 200   # 加入トークと違い公開


# 108-f: 未ログインでコミュニティ参加申請の導線はあるが pending の中身・加入トークは見えない
def test_t108f_community_join_link_but_no_pending_to_guest():
    cid, iid, _ = _project()
    # 誰かが加入申請（pending）+ 加入トーク
    _cli("u_bob").post(f"/api/community/{cid}/join", json={"member_id": "u_bob"})
    atk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                               json={"kind": "admission", "title": "a", "target": {"candidate": "u_bob"}}).get_json()["talk_id"]
    guest = _cli().get(f"/api/community/{cid}").get_json()
    assert "pending" not in guest                       # pending の中身は第三者に見えない
    assert _cli().get(f"/api/talks/{atk}").status_code == 404   # 加入トークも見えない


# 108-i: 見送られた主体が同じプロジェクトに再度申し出られる
def test_t108i_can_reoffer_after_setback():
    cid, iid, _ = _project()
    jtk1 = _offer(cid, iid, "u_ext").get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{jtk1}/vote", json={"stance": "dissent"})   # 見送り（成立せず）
    # u_ext はまだ参加者でない → 再度申し出られる
    r2 = _offer(cid, iid, "u_ext")
    assert r2.status_code == 201 and r2.get_json()["talk_id"] != jtk1


# 108-j: 見送りの表示に人への判定語（拒否・不承認・却下）が無い
def test_t108j_no_person_judgement_words_in_templates():
    tk = open(os.path.join(ROOT, "templates", "talk.html"), encoding="utf-8").read()
    for w in ("拒否", "不承認", "却下"):
        assert w not in tk, w
    assert "見送られても" in tk                          # 中立語で参加見送りを説明


# 108-a: コミュニティとしての参加で consent_ref が当該 purpose.agreed を指す（join-options 経由）
def test_t108a_community_join_options_and_consent():
    cid, iid, _ = _project()
    # コミュニティB が target_intent_id つきで内部合意
    b = _cli("u_bob"); _login(b, "u_bob")
    cidB = b.post("/api/communities", json={"name": "B", "founder_id": "u_bob"}).get_json()["id"]
    tkB = _cli("u_bob").post(f"/api/community/{cidB}/talks",
                             json={"kind": "proposal", "title": "join", "target": {"target_intent_id": iid}}).get_json()["talk_id"]
    _cli("u_bob").post(f"/api/talks/{tkB}/vote", json={"stance": "approve"})
    consent = _cli("u_bob").get(f"/api/talks/{tkB}").get_json()["result"]["purpose_event_hash"]
    # join-options が B の consent_ref を返す
    opts = _cli("u_bob").get(f"/api/projects/{iid}/join-options").get_json()["communities"]
    row = [o for o in opts if o["community_id"] == cidB][0]
    assert row["consent_ref"] == consent
    # 参加を申し出 → intent.participant.joined(kind=community, consent_ref)
    _cli("u_bob").post(f"/api/projects/{iid}/join-as-community", json={"community_id": cidB, "consent_ref": consent})
    jtk = [t["talk_id"] for t in _cli("u_alice").get(f"/api/community/{cid}/talks").get_json()["talks"]
           if t["kind"] == "project_join"][0]
    _cli("u_alice").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"})
    pj = [e["payload"] for e in le.get_events(type_="intent.participant.joined", db_path=appmod.DB)
          if e["payload"]["participant"] == cidB]
    assert pj and pj[0]["participant_kind"] == "community" and pj[0]["consent_ref"] == consent
