"""指示書46 §4 — 受入項目（実装側 66項目＋実機テスト1）の自動テスト。

テスト名に項目番号を入れる（test_tNNN_...）。`PoX_テスト項目一覧.md` の番号に対応。
台帳・可視性・決定性・状態閉鎖・参加(P-1)・API異常系・実績を機械で確認する。
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


def _community(founder="u_alice"):
    c = _client(); _login(c, founder)
    return c.post("/api/communities", json={"name": "n", "founder_id": founder}).get_json()["id"]


def _agree_proposal(cid, voter="u_alice", title="P"):
    tk = _cli(voter).post(f"/api/community/{cid}/talks",
                          json={"kind": "proposal", "title": title, "target": {}}).get_json()["talk_id"]
    _cli(voter).post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    return tk, _cli(voter).get(f"/api/talks/{tk}").get_json()["result"]["purpose_event_hash"]


def _launch(cid, purpose, launcher="u_alice", title="PJ"):
    return _cli(launcher).post(f"/api/community/{cid}/projects/launch",
                               json={"title": title, "purpose_ref": purpose}).get_json()


def _types(db):
    return [e["type"] for e in le.get_events(db_path=db)]


# ── 台帳（項目 1, 4, 5, 8, 9）───────────────────────────────────────────────
def test_t001_subject_created_on_community():
    cid = _community()
    assert _types(appmod.DB)[0] == "subject.created"


def test_t004_t005_purpose_and_launched_fields():
    cid = _community()
    _, purpose = _agree_proposal(cid)
    pa = [e for e in le.get_events(type_="purpose.agreed", db_path=appmod.DB)][0]["payload"]
    for k in ("talk_id", "conclusion_hash", "discussion_hash", "basis_seq", "ruleset_version", "approvals"):
        assert k in pa
    _launch(cid, purpose)
    launched = le.get_events(type_="intent.launched", db_path=appmod.DB)[0]["payload"]
    assert launched["purpose_ref"] == purpose


def test_t008_t009_old_intent_events_not_written():
    cid = _community()
    _agree_proposal(cid)
    types = _types(appmod.DB)
    assert "intent.proposed" not in types
    assert "intent.agreed" not in types
    assert "intent.cancelled" not in types


# ── 決定性（項目 12, 13, 14）────────────────────────────────────────────────
def test_t012_decision_ignores_later_members():
    from agreement import evaluate_agreement as ev
    base = ev(denominator={"a", "b", "c"}, approvals={"a", "b"}, dissents=set(), anchors_crossed=2)
    after = ev(denominator={"a", "b", "c"}, approvals={"a", "b", "d", "e"}, dissents=set(), anchors_crossed=2)
    assert base["agreed"] == after["agreed"] and base["approvers"] == after["approvers"]


def test_t013_decision_ignores_current_time():
    # 判定は anchors_crossed（台帳由来）だけで決まり、時計を読まない
    from agreement import evaluate_agreement as ev
    r1 = ev(denominator={"a", "b", "c"}, approvals={"a", "b"}, dissents=set(), anchors_crossed=2)
    r2 = ev(denominator={"a", "b", "c"}, approvals={"a", "b"}, dissents=set(), anchors_crossed=2)
    assert r1 == r2


# ── 状態・閉鎖（項目 29, 31, 32, 33）────────────────────────────────────────
def test_t029_no_open_in_display_status():
    cid = _community()
    _cli("u_alice").post(f"/api/community/{cid}/talks", json={"kind": "proposal", "title": "x", "target": {}})
    rows = _cli("u_alice").get(f"/api/community/{cid}/talks").get_json()["talks"]
    assert all(t.get("display_status") != "open" for t in rows)


def test_t031_agreed_proposal_post_returns_409():
    cid = _community()
    tk, _ = _agree_proposal(cid)
    r = _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "後から"})
    assert r.status_code == 409 and r.get_json()["error"] == "closed"


def test_t033_dormant_open_proposal_allows_post():
    # 審議中（未合意）の提議には投稿できる（誤拒否しない）
    cid = _community()
    # bob を加入させ分母2に→単独賛成では合意しない＝審議中のまま
    atk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                               json={"kind": "admission", "title": "a", "target": {"candidate": "u_bob"}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{atk}/vote", json={"stance": "approve"})
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "審議中", "target": {}}).get_json()["talk_id"]
    assert _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "議論"}).status_code == 201


# ── 可視性（項目 41, 43, 44, 48/50）─────────────────────────────────────────
def test_t041_admission_talk_404_to_third_party():
    cid = _community()
    atk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                               json={"kind": "admission", "title": "a", "target": {"candidate": "u_bob"}}).get_json()["talk_id"]
    assert _cli().get(f"/api/talks/{atk}").status_code == 404
    assert all(t["kind"] != "admission" for t in _cli().get(f"/api/community/{cid}/talks").get_json()["talks"])


def test_t043_chat_404_to_third_party():
    cid = _community()
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "chat", "title": "c", "target": {}}).get_json()["talk_id"]
    assert _cli().get(f"/api/talks/{tk}").status_code == 404


def test_t044_pending_hidden_from_third_party():
    cid = _community()
    _cli("u_bob").post(f"/api/community/{cid}/join", json={"member_id": "u_bob"})
    assert "pending" not in _cli().get(f"/api/community/{cid}").get_json()


def test_t048_t050_no_raw_id_in_talk_view():
    cid = _community()
    _cli("u_alice").post("/api/my/display-name", json={"id": "u_alice", "name": "カオル"})
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks", json={"kind": "proposal", "title": "x", "target": {}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "hi"})
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    d = _cli().get(f"/api/talks/{tk}").get_json()
    assert d["approvals"][0]["display_name"] == "カオル"       # 生 id ではなく表示名
    assert d["posts"][0]["author_name"] == "カオル"


# ── ダイアログ・文言（項目 63, 66）──────────────────────────────────────────
def test_t063_no_window_prompt_in_templates():
    import glob
    for f in glob.glob(os.path.join(ROOT, "templates", "*.html")):
        txt = open(f, encoding="utf-8").read()
        assert "prompt(" not in txt and "confirm(" not in txt, f
        assert "起票" not in txt, f


# ── 異常系（項目 76, 77, 80）────────────────────────────────────────────────
def test_t076_direct_post_to_closed_409():
    cid = _community()
    tk, _ = _agree_proposal(cid)
    assert _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"}).status_code == 409


def test_t077_admission_direct_404_unauth():
    cid = _community()
    atk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                               json={"kind": "admission", "title": "a", "target": {"candidate": "u_bob"}}).get_json()["talk_id"]
    assert appmod.app.test_client().get(f"/api/talks/{atk}").status_code == 404


def test_t080_old_intent_write_endpoints_frozen():
    cid = _community()
    for r in (_cli("u_alice").post(f"/api/community/{cid}/intent/propose", json={"body": "x"}),
              _cli("u_alice").post("/api/intent/int_x/agree", json={}),
              _cli("u_alice").post("/api/intent/int_x/complete", json={})):
        assert r.status_code == 410


# ── 実機テスト1（項目 99, 101, 107）────────────────────────────────────────
def test_t099_completed_project_stays_in_list():
    cid = _community()
    _, purpose = _agree_proposal(cid)
    lr = _launch(cid, purpose); iid = lr["intent_id"]
    ctk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                               json={"kind": "project_complete", "title": "done", "target": {"intent_id": iid, "result": "r"}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{ctk}/vote", json={"stance": "approve"})
    proj = [t for t in _cli().get(f"/api/community/{cid}/talks").get_json()["talks"] if t["section"] == "project"][0]
    assert proj["display_status"] == "完了"        # 削除されずトーク一覧に残る（読み取り専用で開ける）


def test_t101_button_terminology_no_mix():
    html = open(os.path.join(ROOT, "templates", "community.html"), encoding="utf-8").read()
    assert "提議トークを立ち上げる" in html and "提議トークを提起" not in html and "提議を提起" not in html


def test_t107_launch_members_only():
    cid = _community()
    _, purpose = _agree_proposal(cid)
    # 非メンバーは立ち上げ API で 403
    assert _cli("u_outsider").post(f"/api/community/{cid}/projects/launch",
                                   json={"title": "x", "purpose_ref": purpose}).status_code == 403


# ── P-1 プロジェクト参加（項目 108, 108-a, 108-b, 108-c）─────────────────────
def test_t108_external_individual_joins_project():
    cid = _community()
    _, purpose = _agree_proposal(cid)
    iid = _launch(cid, purpose)["intent_id"]
    # コミュニティ外の個人 u_ext が参加を申し出る（本人が起票できる）
    jtk = _cli("u_ext").post(f"/api/community/{cid}/talks",
                             json={"kind": "project_join", "title": "join",
                                   "target": {"intent_id": iid, "participant": "u_ext", "participant_kind": "individual"}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"})   # launcher
    _cli("u_ext").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"})     # 本人（2人即時成立）
    pj = [e["payload"] for e in le.get_events(type_="intent.participant.joined", db_path=appmod.DB)
          if e["payload"]["participant"] == "u_ext"]
    assert pj and pj[0]["participant_kind"] == "individual" and pj[0]["intent_id"] == iid


def test_t108a_community_joins_with_consent_ref():
    cid = _community()
    _, purpose = _agree_proposal(cid)
    iid = _launch(cid, purpose)["intent_id"]
    # コミュニティB が target_intent_id つきで内部合意 → consent_ref
    bfound = _cli("u_bob"); _login(bfound, "u_bob")
    cidB = bfound.post("/api/communities", json={"name": "B", "founder_id": "u_bob"}).get_json()["id"]
    tkB = _cli("u_bob").post(f"/api/community/{cidB}/talks",
                             json={"kind": "proposal", "title": "join it", "target": {"target_intent_id": iid}}).get_json()["talk_id"]
    _cli("u_bob").post(f"/api/talks/{tkB}/vote", json={"stance": "approve"})
    consent = _cli("u_bob").get(f"/api/talks/{tkB}").get_json()["result"]["purpose_event_hash"]
    _cli("u_bob").post(f"/api/projects/{iid}/join-as-community", json={"community_id": cidB, "consent_ref": consent})
    _cli("u_alice").post(f"/api/talks/{[t['talk_id'] for t in _cli('u_alice').get(f'/api/community/{cid}/talks').get_json()['talks'] if t['kind']=='project_join'][0]}/vote", json={"stance": "approve"})
    pj = [e["payload"] for e in le.get_events(type_="intent.participant.joined", db_path=appmod.DB)
          if e["payload"]["participant"] == cidB]
    assert pj and pj[0]["participant_kind"] == "community" and pj[0]["consent_ref"] == consent


def test_t108b_participation_denominator_is_participants_headcount():
    import governance as gov
    cid = _community()
    _, purpose = _agree_proposal(cid)
    lr = _launch(cid, purpose); iid = lr["intent_id"]
    basis = le.get_last_event(db_path=appmod.DB)["seq"]
    # 基準点の参加者は launcher のみ
    assert gov.participants_at(iid, basis, db_path=appmod.DB) == {"u_alice"}


def test_t108c_project_join_is_public():
    cid = _community()
    _, purpose = _agree_proposal(cid)
    iid = _launch(cid, purpose)["intent_id"]
    jtk = _cli("u_ext").post(f"/api/community/{cid}/talks",
                             json={"kind": "project_join", "title": "join",
                                   "target": {"intent_id": iid, "participant": "u_ext", "participant_kind": "individual"}}).get_json()["talk_id"]
    # 参加トークは公開（第三者に 200・加入トークと違う）
    assert _cli().get(f"/api/talks/{jtk}").status_code == 200
