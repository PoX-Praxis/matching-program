"""指示書43/44 段階1 — closure 済み追記拒否・状態語彙・表示名解決・加入の可視性。"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod


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


def _admit(cid, founder, cand):
    tk = _cli(founder).post(f"/api/community/{cid}/talks",
                            json={"kind": "admission", "title": f"admit {cand}",
                                  "target": {"candidate": cand}}).get_json()["talk_id"]
    _cli(founder).post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    return tk


def _proposal(cid, member, title="x"):
    return _cli(member).post(f"/api/community/{cid}/talks",
                             json={"kind": "proposal", "title": title, "target": {}}).get_json()["talk_id"]


# ── §6-1/2: closure 済みのみ拒否（409＋理由）。審議中は拒否しない ──────────────
def test_closed_proposal_rejects_post_and_vote():
    cid = _community()                       # founder 単独 → 提議は即時合意
    tk = _proposal(cid, "u_alice")
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})   # 合意→closure
    assert _cli("u_alice").get(f"/api/talks/{tk}").get_json()["display_status"] == "合意済み"
    # 追記拒否（409＋理由）
    rp = _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "後から"})
    assert rp.status_code == 409 and rp.get_json()["error"] == "closed" and "detail" in rp.get_json()
    rv = _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    assert rv.status_code == 409 and rv.get_json()["error"] == "closed"


def test_open_proposal_allows_post_and_vote():
    cid = _community()
    _admit(cid, "u_alice", "u_bob")          # メンバー2人（分母2）→ 単独賛成では合意しない
    tk = _proposal(cid, "u_alice")
    # 審議中: 投稿も投票もできる
    assert _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "議論"}).status_code == 201
    rv = _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    assert rv.status_code == 200
    d = _cli("u_alice").get(f"/api/talks/{tk}").get_json()
    assert d["display_status"] == "審議中" and d["closed"] is False


# ── §6-4: 状態語彙の API 出力 ──────────────────────────────────────────────
def test_display_status_vocabulary():
    cid = _community()
    tk = _proposal(cid, "u_alice")
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    purpose = _cli("u_alice").get(f"/api/talks/{tk}").get_json()["result"]["purpose_event_hash"]
    lr = _cli("u_alice").post(f"/api/community/{cid}/projects/launch",
                              json={"title": "PJ", "purpose_ref": purpose}).get_json()
    # プロジェクト容器 → 実行中
    ptalk = [t for t in _cli("u_alice").get(f"/api/community/{cid}/talks").get_json()["talks"]
             if t["kind"] == "project"][0]
    assert ptalk["display_status"] == "実行中"
    # "open" は API の表示語彙に出さない
    assert all(t.get("display_status") != "open" for t in
               _cli("u_alice").get(f"/api/community/{cid}/talks").get_json()["talks"])


# ── §6-3: 生 id を返す API が残っていない（表示名を返す）────────────────────
def test_talk_view_resolves_display_names():
    cid = _community()
    _cli("u_alice").post("/api/my/display-name", json={"id": "u_alice", "name": "カオル"})
    tk = _proposal(cid, "u_alice")
    _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "はじめの発言"})
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    d = _cli("u_alice").get(f"/api/talks/{tk}").get_json()
    # 賛成者は {subject_id, display_name} で表示名つき
    assert d["approvals"] and d["approvals"][0]["display_name"] == "カオル"
    assert d["posts"][0]["author_name"] == "カオル"
    assert d["created_by_name"] == "カオル"


# ── §43 T-8/§1-6: 加入トークはメンバー限定（第三者非公開）──────────────────
def test_admission_talk_members_only():
    cid = _community()
    _admit(cid, "u_alice", "u_bob")          # bob をメンバーに（admission トークが1件できる）
    # 加入トークを取得: 第三者は 404
    atk = [t for t in _cli("u_alice").get(f"/api/community/{cid}/talks").get_json()["talks"]
           if t["kind"] == "admission"][0]["talk_id"]
    assert _cli().get(f"/api/talks/{atk}").status_code == 404
    assert _cli("u_stranger").get(f"/api/talks/{atk}").status_code == 404
    # 一覧でも第三者には加入トークが出ない
    third = _cli().get(f"/api/community/{cid}/talks").get_json()["talks"]
    assert all(t["kind"] != "admission" for t in third)
    # メンバーには見える
    assert _cli("u_alice").get(f"/api/talks/{atk}").status_code == 200
