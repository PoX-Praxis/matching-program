"""指示書45 A群 — 加入の closure・本人面・公開衛生・鍵の確認（項目 81〜85, 88〜90）。

台帳には触れない（新しいイベント型を作らない・加入の見送りを台帳に書かない）。
"""
import os, sys, tempfile
from datetime import datetime, timedelta, timezone
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
import ledger_events as le
import talks


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


def _community_with_two_members():
    """u_alice（代表）＋u_bob の2名コミュニティ。以後の加入は分母2。"""
    _client()
    cid = _cli("u_alice").post("/api/communities", json={"name": "n", "founder_id": "u_alice"}).get_json()["id"]
    _cli("u_bob").post(f"/api/community/{cid}/join", json={"member_id": "u_bob"})
    atk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                               json={"kind": "admission", "title": "a", "target": {"candidate": "u_bob"}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{atk}/vote", json={"stance": "approve"})
    return cid


def _apply_and_open(cid, cand="u_carol"):
    _cli(cand).post(f"/api/community/{cid}/join", json={"member_id": cand})
    return _cli("u_alice").post(f"/api/community/{cid}/talks",
                                json={"kind": "admission", "title": "加入",
                                      "target": {"candidate": cand}}).get_json()["talk_id"]


def _decline(cid, cand="u_carol", summary="今回は活動内容が合わないため"):
    tk = _apply_and_open(cid, cand)
    _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "審議の中身（本人に見せない）"})
    _cli("u_bob").post(f"/api/talks/{tk}/vote", json={"stance": "dissent"})
    r = _cli("u_alice").post(f"/api/talks/{tk}/decline", json={"summary": summary})
    return tk, r


# ── 81: 加入トークは決定後に追記不可／休眠は拒否しない ─────────────────────────
def test_t081_admission_closed_after_decision():
    cid = _community_with_two_members()
    tk, r = _decline(cid)
    assert r.status_code == 200 and r.get_json()["display_status"] == "決定済み"
    assert _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "x"}).status_code == 409
    assert _cli("u_bob").post(f"/api/talks/{tk}/vote", json={"stance": "approve"}).status_code == 409
    # 承認で決定した加入トークも追記不可
    tk2 = _apply_and_open(cid, "u_dave")
    _cli("u_alice").post(f"/api/talks/{tk2}/vote", json={"stance": "approve"})
    _cli("u_bob").post(f"/api/talks/{tk2}/vote", json={"stance": "approve"})
    assert _cli("u_alice").get(f"/api/talks/{tk2}").get_json()["display_status"] == "決定済み"
    assert _cli("u_alice").post(f"/api/talks/{tk2}/posts", json={"body": "x"}).status_code == 409
    # 決定後もトークは削除されず、メンバーには読める
    assert _cli("u_alice").get(f"/api/talks/{tk}").status_code == 200


def test_t081_dormant_admission_is_not_rejected(monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(talks, "_clock", lambda: t0)
    cid = _community_with_two_members()
    tk = _apply_and_open(cid)
    monkeypatch.setattr(talks, "_clock", lambda: t0 + timedelta(days=60))
    # 加入トークには休眠の語彙を出さない（45A 追補 §1-4）。長く無活動でも審議中のまま、投稿も拒否しない
    assert _cli("u_alice").get(f"/api/talks/{tk}").get_json()["display_status"] == "審議中"
    assert _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "再開"}).status_code == 201


def test_t081_decline_requires_dissent_and_membership():
    cid = _community_with_two_members()
    tk = _apply_and_open(cid)
    # 反対が表明されていなければ見送りは確定できない（409）
    assert _cli("u_alice").post(f"/api/talks/{tk}/decline", json={}).status_code == 409
    _cli("u_bob").post(f"/api/talks/{tk}/vote", json={"stance": "dissent"})
    # 非メンバー・申請者本人には存在ごと見せない（404）、未ログインは 401
    assert _cli("u_ext").post(f"/api/talks/{tk}/decline", json={}).status_code == 404
    assert _cli("u_carol").post(f"/api/talks/{tk}/decline", json={}).status_code == 404
    assert _cli().post(f"/api/talks/{tk}/decline", json={}).status_code == 401
    # 画面: 反対があるときだけメンバーに「見送りを確定する」が出る
    html = _cli("u_alice").get(f"/talk/{tk}").get_data(as_text=True)
    assert 'id="declineBox"' in html and "見送りを確定する" in html


# ── 82: 加入の見送りは台帳に書かれない（admission.rejected 等が無い）─────────────
def test_t082_decline_not_written_to_ledger():
    cid = _community_with_two_members()
    before = len(le.get_events(db_path=appmod.DB))
    _decline(cid)
    events = le.get_events(db_path=appmod.DB)
    assert len(events) == before
    assert not [e for e in events if "reject" in e["type"] or "declin" in e["type"]]


# ── 83: 加入の承認は既存の member.joined で記録される ───────────────────────────
def test_t083_admission_approval_writes_member_joined():
    cid = _community_with_two_members()
    tk = _apply_and_open(cid, "u_dave")
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    _cli("u_bob").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    joined = [e["payload"] for e in le.get_events(type_="member.joined", db_path=appmod.DB)
              if e["payload"]["subject_id"] == "u_dave"]
    assert len(joined) == 1
    assert joined[0]["ctx"] == cid and joined[0]["approvals"] == ["u_alice", "u_bob"]
    assert joined[0]["discussion_hash"]                      # 加入トークの合意として記録


# ── 84: 本人面 — 申請者は自分の申請状態だけを見られ、審議内容は見えない ──────────
def test_t084_applicant_sees_only_own_outcome():
    cid = _community_with_two_members()
    tk = _apply_and_open(cid)
    d = _cli("u_carol").get(f"/api/community/{cid}").get_json()
    assert d["my_application"] == {"status": "審議中", "summary": None, "can_reapply": False}
    _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "審議の中身（本人に見せない）"})
    _cli("u_bob").post(f"/api/talks/{tk}/vote", json={"stance": "dissent"})
    _cli("u_alice").post(f"/api/talks/{tk}/decline", json={"summary": "今回は見送ります"})
    d = _cli("u_carol").get(f"/api/community/{cid}").get_json()
    assert d["my_application"]["status"] == "見送り"
    assert d["my_application"]["summary"] == "今回は見送ります"
    apps = _cli("u_carol").get("/api/my/applications").get_json()["applications"]
    assert [(a["community_id"], a["status"], a["summary"]) for a in apps] == [(cid, "見送り", "今回は見送ります")]
    # 審議の内容（トーク本体・発言・票・反対した人）は本人にも見えない
    assert _cli("u_carol").get(f"/api/talks/{tk}").status_code == 404
    shown = str(d["my_application"]) + str(apps)              # 本人に伝わる内容はこれだけ
    for leaked in ("審議の中身", "u_bob", "u_alice", "dissent", "反対", tk):
        assert leaked not in shown, leaked
    assert set(d["my_application"]) == {"status", "summary", "can_reapply", "message"}
    assert "pending" not in d and "messages" not in d
    # 他人の申請状態は見えない（第三者・別の申請者）
    assert "my_application" not in _cli("u_ext").get(f"/api/community/{cid}").get_json()
    assert _cli("u_ext").get("/api/my/applications").get_json()["applications"] == []


# ── 85: 加入系のページ・API に X-Robots-Tag: noindex, nofollow ───────────────────
def test_t085_noindex_on_member_only_surfaces():
    cid = _community_with_two_members()
    tk = _apply_and_open(cid)
    member, guest = _cli("u_alice"), _cli()
    for resp in (member.get(f"/api/talks/{tk}"), member.get(f"/talk/{tk}"),
                 guest.get(f"/api/talks/{tk}"), guest.get(f"/talk/{tk}"),
                 member.get(f"/api/community/{cid}"), member.get(f"/api/community/{cid}/talks"),
                 _cli("u_carol").get(f"/api/community/{cid}"),
                 _cli("u_carol").get("/api/my/applications")):
        assert resp.headers.get("X-Robots-Tag") == "noindex, nofollow", resp.request.path
    # 公開面（提議トーク・第三者向けのコミュニティ）には付けない
    ptk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                               json={"kind": "proposal", "title": "P", "target": {}}).get_json()["talk_id"]
    assert "X-Robots-Tag" not in guest.get(f"/api/talks/{ptk}").headers
    assert "X-Robots-Tag" not in guest.get(f"/talk/{ptk}").headers
    assert "X-Robots-Tag" not in guest.get(f"/api/community/{cid}").headers


def test_t085_robots_txt_exists():
    r = _client().get("/robots.txt")
    assert r.status_code == 200 and r.mimetype == "text/plain"
    body = r.get_data(as_text=True)
    assert "User-agent: *" in body and "Disallow: /api/my/" in body
    assert "Disallow: /talk" not in body          # 公開トークと共通の URL なので robots.txt では分けない


# ── 88: approvals は鍵なしで機能する（署名・公開鍵を要求しない）──────────────────
def test_t088_approvals_work_without_keys():
    cid = _community_with_two_members()
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "P", "target": {}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    r = _cli("u_bob").post(f"/api/talks/{tk}/vote", json={"stance": "approve"}).get_json()
    assert r["commit"]["committed"] is True
    ev = [e for e in le.get_events(type_="purpose.agreed", db_path=appmod.DB)][-1]
    assert ev["payload"]["approvals"] == ["u_alice", "u_bob"]            # アカウント列のみ
    assert not {"sig", "signature", "pubkey"} & set(ev["payload"])


# ── 89 / 90: attestations の有無と basis_seq が申告値である旨がドキュメントにある ──
def test_t089_t090_documented():
    doc = open(os.path.join(ROOT, "docs", "ledger_limits.md"), encoding="utf-8").read()
    assert "署名機構は無い" in doc and "attestations" in doc          # 89
    assert "basis_seq" in doc and "申告値" in doc                     # 90


# ── 45A 追補2: 見送り後の再申請（項目 118〜121）──────────────────────────────
def test_t118_declined_applicant_can_reapply():
    cid = _community_with_two_members()
    _decline(cid)                                          # u_carol が見送られる
    r = _cli("u_carol").post(f"/api/community/{cid}/join", json={"member_id": "u_carol"})
    assert r.status_code == 200 and r.get_json()["status"] == "pending"
    # メンバーの画面には再び参加申請として現れる（通常の審議に戻る）
    pend = _cli("u_alice").get(f"/api/community/{cid}").get_json()["pending"]
    assert [p["member_id"] for p in pend] == ["u_carol"]
    # 再申請から通常どおり承認まで進める（承認は既存の member.joined）
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "admission", "title": "再申請",
                                    "target": {"candidate": "u_carol"}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    _cli("u_bob").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    assert "u_carol" in [m["member_id"] for m in _cli().get(f"/api/community/{cid}").get_json()["members"]]


def test_t119_self_view_shows_decline_and_reapply():
    cid = _community_with_two_members()
    _decline(cid, summary="活動の時期が合わないため")
    mine = _cli("u_carol").get(f"/api/community/{cid}").get_json()["my_application"]
    assert mine["status"] == "見送り" and mine["summary"] == "活動の時期が合わないため"
    assert mine["can_reapply"] is True
    assert mine["message"] == "今回の参加申請は見送りになりました。このコミュニティには、もう一度参加を申請できます。"
    apps = _cli("u_carol").get("/api/my/applications").get_json()["applications"]
    assert apps[0]["can_reapply"] is True and "もう一度参加を申請できます" in apps[0]["message"]
    # 要約は確定者が書いた場合のみ
    _decline(cid, cand="u_dave", summary="")
    assert _cli("u_dave").get(f"/api/community/{cid}").get_json()["my_application"]["summary"] is None
    # 画面: 見送りの本人面に再申請の導線がある
    html = open(os.path.join(ROOT, "templates", "community.html"), encoding="utf-8").read()
    assert 'id="reapplyBtn"' in html and "もう一度参加を申請する" in html
    # 再申請後の審議中には、前回の要約を出さない
    _cli("u_carol").post(f"/api/community/{cid}/join", json={"member_id": "u_carol"})
    assert _cli("u_carol").get(f"/api/community/{cid}").get_json()["my_application"] == \
        {"status": "審議中", "summary": None, "can_reapply": False}


def test_t120_reapply_is_normal_review_and_duplicate_409():
    cid = _community_with_two_members()
    _cli("u_carol").post(f"/api/community/{cid}/join", json={"member_id": "u_carol"})
    # 審議中の二重申請は 409
    r = _cli("u_carol").post(f"/api/community/{cid}/join", json={"member_id": "u_carol"})
    assert r.status_code == 409
    # 見送り→再申請は自動承認されない（メンバーにならず、審議中に戻るだけ）
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "admission", "title": "加入",
                                    "target": {"candidate": "u_carol"}}).get_json()["talk_id"]
    _cli("u_bob").post(f"/api/talks/{tk}/vote", json={"stance": "dissent"})
    _cli("u_alice").post(f"/api/talks/{tk}/decline", json={})
    assert _cli("u_carol").post(f"/api/community/{cid}/join", json={"member_id": "u_carol"}).status_code == 200
    d = _cli().get(f"/api/community/{cid}").get_json()
    assert "u_carol" not in [m["member_id"] for m in d["members"]]
    assert _cli("u_carol").get(f"/api/community/{cid}").get_json()["my_application"]["status"] == "審議中"
    # 再申請の審議中にも二重申請は 409
    assert _cli("u_carol").post(f"/api/community/{cid}/join", json={"member_id": "u_carol"}).status_code == 409


def test_t121_decline_and_reapply_not_in_ledger():
    cid = _community_with_two_members()
    before = len(le.get_events(db_path=appmod.DB))
    _decline(cid)
    _cli("u_carol").post(f"/api/community/{cid}/join", json={"member_id": "u_carol"})
    _cli("u_carol").post(f"/api/community/{cid}/join", json={"member_id": "u_carol"})   # 409
    events = le.get_events(db_path=appmod.DB)
    assert len(events) == before
    assert not [e for e in events if e["type"].startswith("admission.")]
    # 第三者の画面に見送り・再申請の事実は出ない
    guest = _cli().get(f"/api/community/{cid}").get_json()
    assert "my_application" not in guest and "pending" not in guest
    assert "見送り" not in str(guest)
