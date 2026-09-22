"""指示書23 §4/§6-8: 台帳書き込み系のゲートと、意志形成の通し（HTTP レベル）。

通し: コミュニティ作成 → 宣言を含む提起 → 合意（宣言確定）→ 完了。
各段階で台帳に何が書かれるかを検証する。ゲート（未ログイン401 / id不一致403）も確認。
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
    with c.session_transaction() as sess:
        sess["subject_id"] = sid


def _types(db):
    return [e["type"] for e in le.get_events(db_path=db)]


def _make_community(name="n", founder="u_alice"):
    """ログイン済みクライアントでコミュニティを作る（作成は指示書25 でゲート済み）。"""
    c = _client()
    _login(c, founder)
    cid = c.post("/api/communities", json={"name": name, "founder_id": founder}).get_json()["id"]
    return c, cid


# ── ゲート ────────────────────────────────────────────────────────────────
def test_community_create_and_leave_require_login():
    # 指示書25 §1/§3: 作成・離脱は台帳に書くため未ログインは 401
    c = _client()
    assert c.post("/api/communities", json={"name": "n", "founder_id": "u_alice"}).status_code == 401
    # ログインして作成
    _login(c, "u_alice")
    cid = c.post("/api/communities", json={"name": "n", "founder_id": "u_alice"}).get_json()["id"]
    # 未ログインの別クライアントで離脱 → 401
    assert appmod.app.test_client().post(f"/api/community/{cid}/leave", json={"member_id": "u_alice"}).status_code == 401
    # なりすまし作成（セッション≠founder_id）→ 403
    c2 = _client(); _login(c2, "u_alice")
    assert c2.post("/api/communities", json={"name": "n", "founder_id": "u_hacker"}).status_code == 403


def test_write_endpoints_require_login():
    c, cid = _make_community()
    # 未ログインの別クライアントでは書き込み系はすべて 401
    a = appmod.app.test_client()
    # 旧 propose は凍結（410）。現行の書き込み系（トーク作成・加入・接続承認）は未ログイン 401。
    assert a.post(f"/api/community/{cid}/talks",
                  json={"kind": "proposal", "title": "x", "target": {}}).status_code == 401
    assert a.post(f"/api/community/{cid}/join", json={"member_id": "u_bob"}).status_code == 401
    assert a.post("/approve", json={"from_id": "u_alice", "to_id": "u_bob"}).status_code == 401


def test_api_me_200_and_401():
    c = _client()
    assert c.get("/api/me").status_code == 401           # 未ログイン
    _login(c, "u_alice")
    r = c.get("/api/me")
    assert r.status_code == 200 and r.get_json()["subject_id"] == "u_alice"


def test_write_endpoints_403_on_impersonation():
    c, cid = _make_community()
    # セッション u_alice が他人になりすまして加入申請 → 403（require_self）
    r = c.post(f"/api/community/{cid}/join", json={"member_id": "u_hacker"})
    assert r.status_code == 403
    # 他人として接続承認 → 403
    assert c.post("/approve", json={"from_id": "u_hacker", "to_id": "u_bob"}).status_code == 403


# ── 旧 intent フローの通し（指示書41 §8-1 で API は凍結。台帳関数は旧データ読取のため残る）──
# 提起・合意・完了の「新規書き込み」API は 410。ここでは intent_ledger の関数を直接呼び、
# 旧版の台帳メカニクス（読み取り・鎖の健全性・読取 API 表示）が保たれることを確認する。
def test_old_flow_via_ledger_functions_still_reads():
    from intent_ledger import propose_intent, agree_intent, complete_intent
    c = _client()
    db = appmod.DB
    _login(c, "u_alice")
    cid = c.post("/api/communities", json={"name": "翻訳基盤の会", "founder_id": "u_alice"}).get_json()["id"]
    assert _types(db) == ["subject.created", "member.joined"]

    decl = {"kind": "policy", "will_text": "現場を実装に翻訳する", "state_have": "知識と現場"}
    iid = propose_intent(cid, "u_alice", body="全体方針を定める", declaration=decl, db_path=db)["intent_id"]
    assert _types(db) == ["subject.created", "member.joined", "intent.proposed"]

    ag = agree_intent(iid, "u_alice", db_path=db)
    assert ag["agreed"] is True
    assert _types(db) == ["subject.created", "member.joined", "intent.proposed", "intent.agreed", "profile.structured"]

    cp = complete_intent(iid, "u_alice", result="v1 を公開した", db_path=db)
    assert cp["status"] == "completed"
    assert _types(db)[-1] == "intent.completed"
    complete_intent(iid, "u_alice", result="再送", db_path=db)      # 冪等
    assert _types(db).count("intent.completed") == 1
    assert le.verify_chain(db_path=db)["ok"] is True

    # 読取 API（凍結対象外）で第三者に宣言と実績が見える
    view = appmod.app.test_client().get(f"/api/community/{cid}").get_json()
    assert view["declaration"]["will_text"] == "現場を実装に翻訳する"
    done = [i for i in view["intents"] if i["status"] == "completed"]
    assert len(done) == 1 and done[0]["result"] == "v1 を公開した"


def test_old_intent_write_endpoints_are_frozen():
    """§4-4/§8-1: 旧 intent.* の新規書き込み API は 410（提議・合意・完了・取消・参加・declare）。"""
    c, cid = _make_community("凍結の会")
    for resp in (
        c.post(f"/api/community/{cid}/intent/propose", json={"body": "x", "declaration": {}}),
        c.post(f"/api/community/{cid}/declare", json={"payload": {}}),
        c.post("/api/intent/int_x/agree", json={}),
        c.post("/api/intent/int_x/complete", json={"result": "r"}),
        c.post("/api/intent/int_x/cancel", json={}),
        c.post("/api/intent/int_x/participant/join", json={"participant": "u_bob"}),
    ):
        assert resp.status_code == 410, resp.get_data(as_text=True)


def test_recruit_flow_publishes_intent_necessity():
    from intent_ledger import propose_intent, agree_intent
    c, cid = _make_community("募集の会")
    db = appmod.DB
    decl = {"kind": "recruit", "will_text": "翻訳基盤を作る", "necessity_text": "現場と実装を繋げる人"}
    iid = propose_intent(cid, "u_alice", body="人を募る", declaration=decl, db_path=db)["intent_id"]
    agree_intent(iid, "u_alice", db_path=db)
    pub = [e for e in le.get_events(type_="necessity.published", db_path=db)
           if e["payload"]["owner_ref"] == iid]
    assert len(pub) == 1 and pub[0]["payload"]["owner_kind"] == "intent"


def test_connection_flow_mutual_approve_establishes_once():
    """§8 接続の通し: 双方が profile.structured を持つ状態で相互承認 → connection.established 1件。
    セッションを from_id とし（body に id を送らない）、再承認しても二重に成立しない（§5-4）。"""
    import subject_ledger as SL
    c = _client(); db = appmod.DB
    # grounding のため両者に profile.structured（接続は両者登録が前提・指示書18）
    SL.publish_profile_structured("u_alice", {"will_text": "A の意志"}, db_path=db)
    SL.publish_profile_structured("u_bob", {"will_text": "B の意志"}, db_path=db)

    ca = appmod.app.test_client(); _login(ca, "u_alice")
    cb = appmod.app.test_client(); _login(cb, "u_bob")

    r1 = ca.post("/approve", json={"to_id": "u_bob"})
    assert r1.status_code == 200 and r1.get_json().get("established") in (False, None)
    r2 = cb.post("/approve", json={"to_id": "u_alice"})
    assert r2.status_code == 200 and r2.get_json().get("established") is True
    assert _types(db).count("connection.established") == 1
    # 再承認しても active な間は二重成立しない（§5-4）
    cb.post("/approve", json={"to_id": "u_alice"})
    assert _types(db).count("connection.established") == 1
    assert le.verify_chain(db_path=db)["ok"] is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n意志形成 通し/ゲート テスト: {len(tests)} 件 全 PASS")
