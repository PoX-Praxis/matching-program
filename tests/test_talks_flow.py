"""指示書41 段階3 — トーク4種のデータモデルと合意コミット（HTTP レベル）。

提議→purpose.agreed／加入→member.joined／プロジェクト立ち上げ→参加(1対1)→達成 を通し、
合意が台帳に刻まれること・チャットの可視性・投票の権限を検証する。
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
    cid = c.post("/api/communities", json={"name": "n", "founder_id": founder}).get_json()["id"]
    return cid


def _admit(cid, founder, candidate):
    """加入トークで candidate を迎える（founder が起票＋賛成、候補も承認）。"""
    tk = _cli(founder).post(f"/api/community/{cid}/talks",
                            json={"kind": "admission", "title": f"admit {candidate}",
                                  "target": {"candidate": candidate}}).get_json()["talk_id"]
    _cli(founder).post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    return tk


def _types(db):
    return [e["type"] for e in le.get_events(db_path=db)]


# ── 提議トーク → purpose.agreed ─────────────────────────────────────────────
def test_proposal_talk_agrees_and_writes_purpose():
    cid = _community()   # founder u_alice が唯一のメンバー（分母1・§5-4）
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "翻訳基盤をやる",
                                    "target": {"conclusion": "やる"}}).get_json()["talk_id"]
    r = _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"}).get_json()
    # 分母1・全員賛成 → 即時成立
    assert r["commit"]["committed"] is True and r["commit"]["immediate"] is True
    assert r["status"] == "agreed"
    assert _types(appmod.DB).count("purpose.agreed") == 1
    # 冪等: もう一度投票してもイベントは増えない
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    assert _types(appmod.DB).count("purpose.agreed") == 1


# ── 加入トーク → member.joined（分母＝既存メンバー）────────────────────────
def test_admission_talk_adds_member():
    cid = _community()
    tk = _admit(cid, "u_alice", "u_bob")
    st = _cli("u_alice").get(f"/api/talks/{tk}").get_json()["status"]
    assert st == "agreed"
    # 台帳に member.joined（bob）が共通項目つきで刻まれる
    joined = [e for e in le.get_events(type_="member.joined", db_path=appmod.DB)
              if e["payload"].get("subject_id") == "u_bob"]
    assert len(joined) == 1
    p = joined[0]["payload"]
    assert "approvals" in p and "basis_seq" in p and "ruleset_version" in p
    # bob は以後メンバーとして扱われる（コミュニティ詳細で members に出る）
    d = _cli("u_alice").get(f"/api/community/{cid}").get_json()
    assert "u_bob" in [m["member_id"] for m in d["members"]]


# ── プロジェクト立ち上げ → 参加(1対1即時) → 達成 ───────────────────────────
def test_project_launch_join_complete():
    cid = _community()
    # 目的を合意（分母1）
    ptk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                               json={"kind": "proposal", "title": "P", "target": {}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{ptk}/vote", json={"stance": "approve"})
    purpose_ref = _cli("u_alice").get(f"/api/talks/{ptk}").get_json()["result"]["purpose_event_hash"]
    # プロジェクト立ち上げ
    lr = _cli("u_alice").post(f"/api/community/{cid}/projects/launch",
                              json={"title": "翻訳PJ", "purpose_ref": purpose_ref}).get_json()
    intent_id = lr["intent_id"]
    assert le.get_events(type_="intent.launched", db_path=appmod.DB)[0]["payload"]["purpose_ref"] == purpose_ref
    # 外部の個人 u_ext が参加。分母は既存参加者（launcher u_alice の1名）のみで、
    # 申し出た本人は含めない（指示書48 108-r）→ u_alice の賛成で即時成立。
    jtk = _cli("u_ext").post(f"/api/community/{cid}/talks",
                             json={"kind": "project_join", "title": "join",
                                   "target": {"intent_id": intent_id, "participant": "u_ext",
                                              "participant_kind": "individual"}}).get_json()["talk_id"]
    r = _cli("u_alice").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"}).get_json()
    assert r["commit"]["committed"] is True                 # 分母1の全員賛成で即時成立
    pj = le.get_events(type_="intent.participant.joined", db_path=appmod.DB)
    assert any(e["payload"]["participant"] == "u_ext" and e["payload"]["participant_kind"] == "individual"
               for e in pj)
    # 達成（参加者 u_alice, u_ext の合意）
    ctk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                               json={"kind": "project_complete", "title": "done",
                                     "target": {"intent_id": intent_id, "result": "成果"}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{ctk}/vote", json={"stance": "approve"})
    r2 = _cli("u_ext").post(f"/api/talks/{ctk}/vote", json={"stance": "approve"}).get_json()
    assert r2["status"] == "completed"
    comp = le.get_events(type_="intent.completed", db_path=appmod.DB)
    assert comp and comp[-1]["payload"]["intent_id"] == intent_id


# ── 反対で成立しない（§5-1）────────────────────────────────────────────────
def test_dissent_blocks_admission():
    cid = _community()
    _admit(cid, "u_alice", "u_bob")            # bob をメンバーに（分母を2に）
    # carol の加入を提起。alice 賛成・bob 反対 → 反対1人で不成立
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "admission", "title": "admit carol",
                                    "target": {"candidate": "u_carol"}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    r = _cli("u_bob").post(f"/api/talks/{tk}/vote", json={"stance": "dissent"}).get_json()
    assert r["commit"]["committed"] is False and r["status"] == "open"
    joined = [e for e in le.get_events(type_="member.joined", db_path=appmod.DB)
              if e["payload"].get("subject_id") == "u_carol"]
    assert joined == []


# ── チャットの可視性（§3）──────────────────────────────────────────────────
def test_chat_visible_only_to_members():
    cid = _community()
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "chat", "title": "雑談", "target": {}}).get_json()["talk_id"]
    # 第三者（未ログイン）にはチャットは存在ごと見えない
    assert _cli().get(f"/api/talks/{tk}").status_code == 404
    # 一覧でもチャットは第三者に出ない
    lst = _cli().get(f"/api/community/{cid}/talks").get_json()["talks"]
    assert all(t["kind"] != "chat" for t in lst)
    # メンバーには見える
    assert _cli("u_alice").get(f"/api/talks/{tk}").status_code == 200


# ── 公開トークは第三者に名前付きで見える（§3・§9 の確認）───────────────────
def test_public_talks_named_to_third_party():
    cid = _community()
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "公開の提議",
                                    "target": {}}).get_json()["talk_id"]
    d = _cli().get(f"/api/talks/{tk}").get_json()      # 未ログイン
    assert d["title"] == "公開の提議" and d["kind"] == "proposal"


# ── 非メンバーは投票できない ────────────────────────────────────────────────
def test_nonmember_cannot_vote_proposal():
    cid = _community()
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "x", "target": {}}).get_json()["talk_id"]
    r = _cli("u_stranger").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    assert r.status_code == 403
