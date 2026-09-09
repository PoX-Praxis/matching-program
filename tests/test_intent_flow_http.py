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
    assert a.post(f"/api/community/{cid}/intent/propose", json={"body": "x"}).status_code == 401
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
    # セッション u_alice が他人になりすまして提起 → 403
    r = c.post(f"/api/community/{cid}/intent/propose", json={"proposer": "u_hacker", "body": "x"})
    assert r.status_code == 403
    # 他人として接続承認 → 403
    assert c.post("/approve", json={"from_id": "u_hacker", "to_id": "u_bob"}).status_code == 403


# ── 通し（§6-8）────────────────────────────────────────────────────────────
def test_full_flow_create_propose_agree_complete_ledger_writes():
    c = _client()
    db = appmod.DB
    _login(c, "u_alice")
    # 1) コミュニティ作成 → member.joined（創設者・members_before 空）
    cid = c.post("/api/communities", json={"name": "翻訳基盤の会", "founder_id": "u_alice"}).get_json()["id"]
    assert _types(db) == ["member.joined"]

    # 2) 宣言（全体方針）を含む提起 → intent.proposed（body に id を送らずセッションで解決）
    decl = {"kind": "policy", "will_text": "現場を実装に翻訳する", "state_have": "知識と現場"}
    pr = c.post(f"/api/community/{cid}/intent/propose",
                json={"body": "全体方針を定める", "declaration": decl})
    assert pr.status_code == 200
    iid = pr.get_json()["intent_id"]
    assert _types(db) == ["member.joined", "intent.proposed"]

    # 3) 合意 → intent.agreed ＋（宣言確定）profile.structured
    ag = c.post(f"/api/intent/{iid}/agree", json={})
    assert ag.status_code == 200 and ag.get_json()["agreed"] is True
    assert _types(db) == ["member.joined", "intent.proposed", "intent.agreed", "profile.structured"]

    # 4) 完了 → intent.completed
    cp = c.post(f"/api/intent/{iid}/complete", json={"result": "v1 を公開した"})
    assert cp.status_code == 200 and cp.get_json()["status"] == "completed"
    assert _types(db)[-1] == "intent.completed"

    # §5-4 一意性: 同じ intent を再度 complete しても completed は増えない（二重押下の受け皿）
    c.post(f"/api/intent/{iid}/complete", json={"result": "再送"})
    assert _types(db).count("intent.completed") == 1

    # 台帳の鎖は健全
    assert le.verify_chain(db_path=db)["ok"] is True

    # 第三者（未ログイン）に宣言と実績が見える
    c2 = appmod.app.test_client()
    view = c2.get(f"/api/community/{cid}").get_json()
    assert view["declaration"]["will_text"] == "現場を実装に翻訳する"
    done = [i for i in view["intents"] if i["status"] == "completed"]
    assert len(done) == 1 and done[0]["result"] == "v1 を公開した"
    assert done[0]["body"] == "全体方針を定める"


def test_recruit_flow_publishes_intent_necessity():
    c, cid = _make_community("募集の会")
    db = appmod.DB
    decl = {"kind": "recruit", "will_text": "翻訳基盤を作る", "necessity_text": "現場と実装を繋げる人"}
    iid = c.post(f"/api/community/{cid}/intent/propose",
                 json={"body": "人を募る", "declaration": decl}).get_json()["intent_id"]
    c.post(f"/api/intent/{iid}/agree", json={})
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
