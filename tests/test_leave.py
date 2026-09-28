"""指示書51 — 離脱（leave）。項目 145〜156 と、発注者の決定 (a)〜(c)（2026-09-28）。

(a) 代表も離脱できる。最後の 1 人は離脱できない（409）。代表の joined_ref は intent.launched。
(b) 51 では個人の離脱だけ（コミュニティとしての離脱は未決）。
(c) 創設者の権限（議決・操作）は在籍中だけ有効。作成者という事実の表示は残す。
    初回の intent.completed の前は、創設者は離脱できない（409）。
"""
import json, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import governance as gov
import ledger_events as le


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


def _community(founder="u_a"):
    return _cli(founder).post("/api/communities", json={"name": "C", "founder_id": founder}).get_json()["id"]


def _add_member(cid, cand):
    _cli(cand).post(f"/api/community/{cid}/join", json={"member_id": cand})
    members = [m["member_id"] for m in _cli().get(f"/api/community/{cid}").get_json()["members"]]
    atk = _cli("u_a").post(f"/api/community/{cid}/talks",
                           json={"kind": "admission", "title": "a", "target": {"candidate": cand}}).get_json()["talk_id"]
    for m in members:
        _cli(m).post(f"/api/talks/{atk}/vote", json={"stance": "approve"})


def _project(cid, launcher="u_a"):
    tk = _cli(launcher).post(f"/api/community/{cid}/talks",
                             json={"kind": "proposal", "title": "P", "target": {}}).get_json()["talk_id"]
    members = [m["member_id"] for m in _cli().get(f"/api/community/{cid}").get_json()["members"]]
    for m in members:
        _cli(m).post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    purpose = _cli().get(f"/api/talks/{tk}").get_json()["result"]["purpose_event_hash"]
    lr = _cli(launcher).post(f"/api/community/{cid}/projects/launch",
                             json={"title": "PJ", "purpose_ref": purpose}).get_json()
    return lr["intent_id"], lr["talk"]["talk_id"]


def _join(cid, iid, who):
    jtk = _cli(who).post(f"/api/community/{cid}/talks",
                         json={"kind": "project_join", "title": "参加",
                               "target": {"intent_id": iid, "participant": who}}).get_json()["talk_id"]
    for p in gov.participants_at(iid, 10**18, db_path=appmod.DB):
        _cli(p).post(f"/api/talks/{jtk}/vote", json={"stance": "approve"})
    assert who in gov.participants_at(iid, 10**18, db_path=appmod.DB)


def _complete(cid, iid):
    ctk = _cli("u_a").post(f"/api/community/{cid}/talks",
                           json={"kind": "project_complete", "title": "達成",
                                 "target": {"intent_id": iid, "result": "r"}}).get_json()["talk_id"]
    for p in gov.participants_at(iid, 10**18, db_path=appmod.DB):
        _cli(p).post(f"/api/talks/{ctk}/vote", json={"stance": "approve"})


def _leave_project(iid, who):
    return _cli(who).post(f"/api/projects/{iid}/leave")


def _leave_community(cid, who):
    return _cli(who).post(f"/api/community/{cid}/leave", json={"member_id": who})


# ── 145: コミュニティの離脱で member.left が台帳に書かれる ─────────────────────────
def test_t145_community_leave_writes_member_left():
    cid = _community(); _add_member(cid, "u_b")
    assert _leave_community(cid, "u_b").status_code == 200
    left = [e["payload"] for e in le.get_events(type_="member.left", db_path=appmod.DB)]
    assert [(p["ctx"], p["subject_id"]) for p in left] == [(cid, "u_b")]
    assert "u_b" not in [m["member_id"] for m in _cli().get(f"/api/community/{cid}").get_json()["members"]]


# ── 146: プロジェクトの離脱で台帳に書かれ、joined_ref が対応する joined を指す ──────────
def test_t146_project_leave_writes_event_with_joined_ref():
    cid = _community(); iid, _ptk = _project(cid)
    _join(cid, iid, "u_b")
    joined = [e for e in le.get_events(type_="intent.participant.joined", db_path=appmod.DB)
              if e["payload"]["participant"] == "u_b"][-1]
    r = _leave_project(iid, "u_b")
    assert r.status_code == 200 and r.get_json()["joined_ref"] == joined["event_hash"]
    ev = le.get_events(type_="intent.participant.left", db_path=appmod.DB)[-1]["payload"]
    assert set(ev) == {"intent_id", "participant", "participant_kind", "joined_ref", "recorded_at"}
    assert (ev["intent_id"], ev["participant"], ev["participant_kind"]) == (iid, "u_b", "individual")


# ── (a) 代表も離脱できる（joined_ref は intent.launched）。最後の 1 人は 409 ─────────────
def test_t051a_launcher_can_leave_but_not_last_one():
    cid = _community(); iid, _ptk = _project(cid)
    r = _leave_project(iid, "u_a")
    assert r.status_code == 409 and r.get_json()["error"] == "last_participant"   # 代表 1 人だけ
    _join(cid, iid, "u_b")
    launched = le.get_events(type_="intent.launched", db_path=appmod.DB)[-1]
    r = _leave_project(iid, "u_a")                          # 代表を特別扱いしない
    assert r.status_code == 200 and r.get_json()["joined_ref"] == launched["event_hash"]
    assert gov.participants_at(iid, 10**18, db_path=appmod.DB) == {"u_b"}
    assert _leave_project(iid, "u_b").status_code == 409     # 残った 1 人も抜けられない


# ── 147: 離脱後、以後の合意の分母から外れる ───────────────────────────────────────
def test_t147_left_participant_not_in_later_denominator():
    cid = _community(); iid, _ptk = _project(cid)
    _join(cid, iid, "u_b"); _join(cid, iid, "u_c")
    _leave_project(iid, "u_c")
    jtk = _cli("u_d").post(f"/api/community/{cid}/talks",
                           json={"kind": "project_join", "title": "j",
                                 "target": {"intent_id": iid, "participant": "u_d"}}).get_json()["talk_id"]
    denom = [x["subject_id"] for x in _cli().get(f"/api/talks/{jtk}").get_json()["denominator"]]
    assert sorted(denom) == ["u_a", "u_b"]
    # 離脱者がいなくても定足数に届く（残る当事者の全員賛成で成立）
    for p in ("u_a", "u_b"):
        r = _cli(p).post(f"/api/talks/{jtk}/vote", json={"stance": "approve"}).get_json()
    assert r["commit"]["committed"] is True


# ── 148: 過去の合意の分母は遡及して変わらない ───────────────────────────────────────
def test_t148_past_denominator_not_retroactive():
    cid = _community(); iid, _ptk = _project(cid)
    _join(cid, iid, "u_b"); _join(cid, iid, "u_c")
    jtk = _cli("u_d").post(f"/api/community/{cid}/talks",
                           json={"kind": "project_join", "title": "j",
                                 "target": {"intent_id": iid, "participant": "u_d"}}).get_json()["talk_id"]
    before = sorted(x["subject_id"] for x in _cli().get(f"/api/talks/{jtk}").get_json()["denominator"])
    _leave_project(iid, "u_c")                               # 提起の後に離脱
    after = sorted(x["subject_id"] for x in _cli().get(f"/api/talks/{jtk}").get_json()["denominator"])
    assert before == after == ["u_a", "u_b", "u_c"]


# ── 149・150: 離脱しても発言は残る・参加者一覧から外れる ───────────────────────────────
def test_t149_t150_posts_remain_and_removed_from_list():
    cid = _community(); iid, ptk = _project(cid)
    _join(cid, iid, "u_b")
    _cli("u_b").post(f"/api/talks/{ptk}/posts", json={"body": "u_b の発言"})
    _leave_project(iid, "u_b")
    d = _cli().get(f"/api/talks/{ptk}").get_json()
    assert "u_b の発言" in [p["body"] for p in d["posts"]]                          # 149
    assert "u_b" not in [p["subject_id"] for p in d["participants"]]               # 150


# ── 151: 離脱後の発言は 403／未ログインは 401 ───────────────────────────────────
def test_t151_after_leave_post_forbidden():
    cid = _community(); iid, ptk = _project(cid)
    _join(cid, iid, "u_b"); _leave_project(iid, "u_b")
    assert _cli("u_b").post(f"/api/talks/{ptk}/posts", json={"body": "x"}).status_code == 403
    assert _cli().post(f"/api/talks/{ptk}/posts", json={"body": "x"}).status_code == 401
    assert _cli().post(f"/api/projects/{iid}/leave").status_code == 401
    assert _cli("u_z").post(f"/api/projects/{iid}/leave").status_code == 403        # 当事者でない


# ── 152: 二重離脱は 409（プロジェクト・コミュニティとも）──────────────────────────
def test_t152_double_leave_409():
    cid = _community(); iid, _ptk = _project(cid)
    _join(cid, iid, "u_b")
    assert _leave_project(iid, "u_b").status_code == 200
    r = _leave_project(iid, "u_b")
    assert r.status_code == 409 and r.get_json()["error"] == "already_left"
    _add_member(cid, "u_c")
    assert _leave_community(cid, "u_c").status_code == 200
    r = _leave_community(cid, "u_c")
    assert r.status_code == 409 and r.get_json()["error"] == "not_member"           # 以前は 200 だった
    assert _leave_community(cid, "u_never").status_code == 409


# ── 153: 除名の型・API が存在しない ─────────────────────────────────────────────
def test_t153_no_expulsion():
    for r in appmod.app.url_map.iter_rules():
        for w in ("expel", "kick", "remove", "removal", "expulsion", "ban"):
            assert w not in r.rule, r.rule
    # 他人を離脱させることはできない（本人の離脱だけ）
    cid = _community(); _add_member(cid, "u_b")
    assert _cli("u_a").post(f"/api/community/{cid}/leave", json={"member_id": "u_b"}).status_code == 403
    types = {e["type"] for e in le.get_events(db_path=appmod.DB)}
    assert not [t for t in types if "remov" in t or "expel" in t]


# ── 154: 離脱で権利（実績・持ち分・将来の発行）が消えない・凍結されない ─────────────────────
def test_t154_rights_remain_after_leave():
    cid = _community(); iid1, _ = _project(cid)
    _join(cid, iid1, "u_b"); _complete(cid, iid1)            # u_b が関わった完了
    iid2, _ = _project(cid)
    _join(cid, iid2, "u_b"); _leave_project(iid2, "u_b")
    # 完了の記録と u_b の賛成は残る（削除・書き換え無し）
    comp = [e["payload"] for e in le.get_events(type_="intent.completed", db_path=appmod.DB)]
    assert comp and "u_b" in comp[0]["approvals"]
    joined = [e["payload"]["intent_id"] for e in le.get_events(type_="intent.participant.joined", db_path=appmod.DB)
              if e["payload"]["participant"] == "u_b"]
    assert set(joined) == {iid1, iid2}
    # 軌跡の参加・完了の枝も消えない
    branches = [b["kind"] for b in _cli().get("/api/timeline/u_b").get_json()["before_first_version"]]
    assert branches.count("joined_project") == 2 and "completed" in branches


# ── 155: 台帳の新設は 1 つだけ（intent.participant.left）─────────────────────────────
def test_t155_only_one_new_event_type():
    cid = _community(); iid, _ = _project(cid)
    _join(cid, iid, "u_b"); _add_member(cid, "u_c")
    known = {e["type"] for e in le.get_events(db_path=appmod.DB)}
    _leave_project(iid, "u_b"); _leave_community(cid, "u_c")
    new = {e["type"] for e in le.get_events(db_path=appmod.DB)} - known
    # 新しく現れた型は intent.participant.left と、既存型 member.left（41 §4）だけ
    assert new <= {"intent.participant.left", "member.left"}
    assert "intent.participant.left" in new


# ── 156: 離脱はトークの可視性を変えない ───────────────────────────────────────────
def test_t156_visibility_unchanged():
    cid = _community(); iid, ptk = _project(cid)
    _join(cid, iid, "u_b")
    before = _cli().get(f"/api/talks/{ptk}")
    _leave_project(iid, "u_b")
    after = _cli().get(f"/api/talks/{ptk}")
    assert before.status_code == after.status_code == 200
    assert after.get_json()["display_status"] == "実行中"


# ── (c) 創設者: 権限は在籍中だけ・作成者の表示は残る・初回完了前は離脱できない ────────────────
def test_t051c_founder_bootstrap_cannot_leave():
    cid = _community(); _add_member(cid, "u_b")
    r = _leave_community(cid, "u_a")
    assert r.status_code == 409 and r.get_json()["error"] == "founder_bootstrap"
    # 一般のメンバーは初回完了前でも離脱できる
    assert _leave_community(cid, "u_b").status_code == 200


def test_t051c_founder_rights_end_but_fact_remains():
    cid = _community(); _add_member(cid, "u_b")
    iid, _ = _project(cid); _join(cid, iid, "u_b"); _complete(cid, iid)   # 初回の完了
    assert _leave_community(cid, "u_a").status_code == 200
    d = _cli("u_a").get(f"/api/community/{cid}").get_json()
    assert d["viewer_role"] != "member"                                   # 権限は失う
    assert d["founder"] == "u_a"                                          # 作成者という事実は残る
    # 議決・操作ができない
    tk = _cli("u_b").post(f"/api/community/{cid}/talks",
                          json={"kind": "proposal", "title": "Q", "target": {}}).get_json()["talk_id"]
    assert _cli("u_a").post(f"/api/talks/{tk}/vote", json={"stance": "approve"}).status_code == 403
    assert _cli("u_a").post(f"/api/community/{cid}/talks",
                            json={"kind": "proposal", "title": "x", "target": {}}).status_code == 403
    assert _cli("u_a").post(f"/api/community/{cid}/approve",
                            json={"member_id": "u_x", "approver_id": "u_a"}).status_code == 403
    r = _cli("u_a").patch(f"/api/community/{cid}", json={"name": "改名", "description": "", "requester_id": "u_a"})
    assert r.status_code in (403, 404)                                    # 編集もできない
    assert _cli().get(f"/api/community/{cid}").get_json()["name"] == "C"


def test_t051_last_member_cannot_leave_community():
    cid = _community()
    r = _leave_community(cid, "u_a")
    assert r.status_code == 409 and r.get_json()["error"] in ("last_member", "founder_bootstrap")


# ── §8-1: 離脱後の再申し出を塞がない ────────────────────────────────────────────
def test_t051_reoffer_after_leave_allowed():
    cid = _community(); iid, ptk = _project(cid)
    _join(cid, iid, "u_b"); _leave_project(iid, "u_b")
    assert _cli("u_b").get(f"/api/talks/{ptk}").get_json()["join_offer"] is not None
    _join(cid, iid, "u_b")                                                  # 改めて参加が承認される
    assert "u_b" in gov.participants_at(iid, 10**18, db_path=appmod.DB)
    assert gov.has_left_project(iid, "u_b", db_path=appmod.DB) is False


# ── 画面: 当事者に離脱の導線・権利の一行／最後の 1 人は押せない ────────────────────────────
def test_t051_leave_affordance_rendered():
    cid = _community(); iid, ptk = _project(cid)
    html = _cli("u_a").get(f"/talk/{ptk}").get_data(as_text=True)
    assert 'id="leaveSec"' in html and 'id="leaveLast"' in html and 'id="leaveBtn"' not in html
    _join(cid, iid, "u_b")
    html = _cli("u_b").get(f"/talk/{ptk}").get_data(as_text=True)
    assert 'id="leaveBtn"' in html and "離脱しても、実績と持ち分は残ります" in html
    assert 'id="leaveSec"' not in _cli("u_z").get(f"/talk/{ptk}").get_data(as_text=True)
    assert 'id="leaveSec"' not in _cli().get(f"/talk/{ptk}").get_data(as_text=True)
    comm = open(os.path.join(ROOT, "templates", "community.html"), encoding="utf-8").read()
    assert "このコミュニティから離脱する" in comm and "離脱しても、実績と持ち分は残ります" in comm
    assert "window.confirm" not in comm


# ── 付随の修正: コミュニティの編集はログイン必須・本人のみ（以前は body の自己申告を信じていた）──────
def test_t051c_community_edit_requires_session_founder():
    cid = _community()
    body = {"name": "乗っ取り", "description": "", "requester_id": "u_a"}
    assert _cli().patch(f"/api/community/{cid}", json=body).status_code == 401            # 未ログイン
    assert _cli("u_evil").patch(f"/api/community/{cid}", json=body).status_code == 403    # なりすまし
    assert _cli().get(f"/api/community/{cid}").get_json()["name"] == "C"
    r = _cli("u_a").patch(f"/api/community/{cid}", json={**body, "name": "改名"})
    assert r.status_code == 200 and r.get_json()["name"] == "改名"
