"""指示書38: 未成立の関係（承認前の参加申請）を公開面から外す。

GET /api/community/<id> のロール別 allowlist を検証する。
判定はセッションの subject_id（指示書36）。クエリ引数の自己申告は使わない。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod


def _client():
    """新しい一時 DB を張り、appmod.DB を差し替える（以降のクライアントは同じ DB を共有）。"""
    os.environ.pop("POX_DEBUG", None)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _login(c, sid):
    with c.session_transaction() as sess:
        sess["subject_id"] = sid


def _cli(sid=None):
    """現在の appmod.DB を使う追加クライアント（sid を渡すとログイン）。"""
    c = appmod.app.test_client()
    if sid:
        _login(c, sid)
    return c


def _setup(founder="u_alice"):
    c = _client()
    _login(c, founder)
    cid = c.post("/api/communities", json={"name": "n", "founder_id": founder}).get_json()["id"]
    return cid


def _apply(cid, sid):
    """sid が参加申請（pending）を出す。"""
    _cli(sid).post(f"/api/community/{cid}/join", json={"member_id": sid})


def _post_message(cid, sid, body):
    return _cli(sid).post(f"/api/community/{cid}/message", json={"from_id": sid, "body": body})


# 旧 intent.* の書き込み API は凍結（指示書41 §8-1）。旧版の可視性ルール（指示書39）は
# 旧データに対して維持されるため、ここでは intent_ledger の関数で旧イベントを直接作って検証する。
def _propose(cid, sid, body="やること"):
    from intent_ledger import propose_intent
    return propose_intent(cid, sid, body=body, db_path=appmod.DB)["intent_id"]


def _agree(iid, sid):
    from intent_ledger import agree_intent
    return agree_intent(iid, sid, db_path=appmod.DB)


def _cancel(iid, sid):
    from intent_ledger import cancel_intent
    return cancel_intent(iid, sid, db_path=appmod.DB)


def _intent_ids_community(client, cid):
    return [it["intent_id"] for it in client.get(f"/api/community/{cid}").get_json().get("intents", [])]


def _intent_ids_list(client, cid):
    return [it["intent_id"] for it in client.get(f"/api/community/{cid}/intents").get_json().get("intents", [])]


# ── 指示書43 T-8 / 44 §4: 参加申請（pending）はメンバー限定（第三者・申請者に非公開）────
def test_pending_hidden_from_third_party():
    cid = _setup()
    _apply(cid, "u_bob")
    data = _cli().get(f"/api/community/{cid}").get_json()   # 未ログイン＝第三者
    assert "pending" not in data
    assert data["viewer_role"] == "guest"
    assert "members" in data and "declaration" in data and "intents" in data


def test_authenticated_nonmember_gets_no_pending():
    cid = _setup()
    _apply(cid, "u_bob")
    data = _cli("u_stranger").get(f"/api/community/{cid}").get_json()
    assert "pending" not in data
    assert data["viewer_role"] == "authenticated"


def test_applicant_gets_applied_flag_not_pending_list():
    cid = _setup()
    _apply(cid, "u_bob")
    # 申請者本人は自分が申請済みであることは分かるが、pending 一覧（他人含む）は見えない
    data = _cli("u_bob").get(f"/api/community/{cid}").get_json()
    assert data.get("applied") is True
    assert "pending" not in data


# ── §5-5: メンバーには pending 全件が返り、承認できる ───────────────────────
def test_member_sees_all_pending_and_can_approve():
    cid = _setup()
    _apply(cid, "u_bob")
    _apply(cid, "u_carol")
    # founder はメンバー → pending 全件
    data = _cli("u_alice").get(f"/api/community/{cid}").get_json()
    ids = sorted(p["member_id"] for p in data.get("pending", []))
    assert ids == ["u_bob", "u_carol"]
    assert data["viewer_role"] == "member"
    # 承認できる（見えているから承認判断ができる）
    r = _cli("u_alice").post(f"/api/community/{cid}/approve",
                             json={"member_id": "u_bob", "approver_id": "u_alice"})
    assert r.status_code == 200
    # 承認後: u_bob は members に入り、pending から消える
    data2 = _cli("u_alice").get(f"/api/community/{cid}").get_json()
    assert "u_bob" in [m["member_id"] for m in data2["members"]]
    assert "u_bob" not in [p["member_id"] for p in data2.get("pending", [])]


# ── §5-6: 却下・取り下げ後は公開面から痕跡が消える ──────────────────────────
def test_rejected_request_leaves_no_public_trace():
    cid = _setup()
    _apply(cid, "u_bob")
    # 却下・取り下げを模す: 通常 DB の行は残してよい（§2-2）が status を pending 以外にする。
    from community import _connect
    with _connect(appmod.DB) as con:
        con.execute("UPDATE community_members SET status='rejected' "
                    "WHERE community_id=%s AND member_id=%s", (cid, "u_bob"))
    # メンバー（alice）から見ても pending に残らない（判断済みを残さない）
    d_member = _cli("u_alice").get(f"/api/community/{cid}").get_json()
    assert "u_bob" not in [p["member_id"] for p in d_member.get("pending", [])]
    assert "u_bob" not in [m["member_id"] for m in d_member["members"]]   # active ではない
    # 第三者の公開 pending にも出ない（status!='pending' は get_pending_requests に出ない）
    assert "u_bob" not in [p["member_id"] for p in _cli().get(f"/api/community/{cid}").get_json().get("pending", [])]


# ── §2-3 / §5-4（完了条件）: messages（チャット）はメンバーのみ ─────────────
def test_messages_not_returned_to_third_party_or_applicant():
    cid = _setup()
    _post_message(cid, "u_alice", "founder note")     # founder はメンバー
    _apply(cid, "u_bob")
    # 第三者（未ログイン）: messages キーごと返さない
    assert "messages" not in _cli().get(f"/api/community/{cid}").get_json()
    # 申請者本人（未成立）にもチャットは返さない
    d_applicant = _cli("u_bob").get(f"/api/community/{cid}").get_json()
    assert "messages" not in d_applicant
    # 無関係ログインにも返さない
    assert "messages" not in _cli("u_stranger").get(f"/api/community/{cid}").get_json()


def test_messages_returned_to_member():
    cid = _setup()
    _post_message(cid, "u_alice", "hello members")
    data = _cli("u_alice").get(f"/api/community/{cid}").get_json()
    bodies = [m.get("body") for m in data.get("messages", [])]
    assert "hello members" in bodies


# ── 指示書39（§3-1）: 合意前の提起は第三者に見せない ─────────────────────────
def test_proposed_hidden_from_third_party_all_three_routes():
    cid = _setup()
    iid = _propose(cid, "u_alice")
    anon = _cli()   # 未ログイン＝第三者
    # 経路1: /api/community/<id>（intents）
    assert iid not in _intent_ids_community(anon, cid)
    # 経路2: /api/community/<id>/intents
    assert iid not in _intent_ids_list(anon, cid)
    # 経路3: /api/intent/<id> → 存在ごと隠す（404）
    assert anon.get(f"/api/intent/{iid}").status_code == 404
    # 無関係のログインユーザーにも見えない
    assert _cli("u_stranger").get(f"/api/intent/{iid}").status_code == 404


def test_member_sees_proposed_and_can_agree():
    cid = _setup()
    iid = _propose(cid, "u_alice")
    alice = _cli("u_alice")                       # founder＝メンバー
    assert iid in _intent_ids_community(alice, cid)
    assert iid in _intent_ids_list(alice, cid)
    assert alice.get(f"/api/intent/{iid}").status_code == 200
    # 合意できる（見えているから合意判断ができる）
    r = _agree(iid, "u_alice")
    assert r.get("agreed") is True
    # 合意後は第三者にも公開
    anon = _cli()
    assert anon.get(f"/api/intent/{iid}").get_json()["status"] == "agreed"
    assert iid in _intent_ids_community(anon, cid)


# ── §3-1: 合意前 cancel は痕跡を残さない（第三者にもメンバーにも出さない）──────
def test_cancel_before_agree_leaves_no_trace():
    cid = _setup()
    iid = _propose(cid, "u_alice")
    _cancel(iid, "u_alice")   # 合意前キャンセル
    # 第三者
    anon = _cli()
    assert iid not in _intent_ids_community(anon, cid)
    assert iid not in _intent_ids_list(anon, cid)
    assert anon.get(f"/api/intent/{iid}").status_code == 404
    # メンバー（痕跡を残さない → メンバーからも消える）
    alice = _cli("u_alice")
    assert iid not in _intent_ids_community(alice, cid)
    assert iid not in _intent_ids_list(alice, cid)
    assert alice.get(f"/api/intent/{iid}").status_code == 404


# ── §3-1: 合意後 cancel は公開のまま（成立した事実の後の終了）────────────────
def test_cancel_after_agree_stays_public():
    cid = _setup()
    iid = _propose(cid, "u_alice")
    assert _agree(iid, "u_alice").get("agreed") is True
    _cancel(iid, "u_alice")   # 合意後キャンセル
    anon = _cli()
    r = anon.get(f"/api/intent/{iid}")
    assert r.status_code == 200 and r.get_json()["status"] == "cancelled"
    assert iid in _intent_ids_community(anon, cid)
    assert iid in _intent_ids_list(anon, cid)
