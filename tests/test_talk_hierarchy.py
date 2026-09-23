"""指示書43/42 段階2 — トーク木構造（出自の導出）・ルート一覧の規則・実行トーク。"""
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


def _agree_proposal(cid, title="利用者を増やす"):
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": title, "target": {}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    purpose = _cli("u_alice").get(f"/api/talks/{tk}").get_json()["result"]["purpose_event_hash"]
    return tk, purpose


# ── 42 §5-2/§5-3: プロジェクトの出自の導出、実行トークの紐づけと即時開始 ──────
def test_project_origin_and_execution_talk():
    cid = _community()
    proposal_tk, purpose = _agree_proposal(cid)
    lr = _cli("u_alice").post(f"/api/community/{cid}/projects/launch",
                              json={"title": "登録者を一人増す", "purpose_ref": purpose}).get_json()
    intent_id = lr["intent_id"]
    proj = lr["talk"]
    # 実行トークは intent_id で紐づき、親の提議トークを parent_talk_id に持つ
    assert proj["target"]["intent_id"] == intent_id
    assert proj["parent_talk_id"] == proposal_tk
    # intent.launched の直後に実行トークが作られている（launch 応答が talk を返す＝即時）
    assert le.get_events(type_="intent.launched", db_path=appmod.DB)[0]["payload"]["intent_id"] == intent_id
    # 出自が §2 の連鎖（purpose_ref → purpose.agreed.talk_id）で導出できる
    d = _cli().get(f"/api/talks/{proj['talk_id']}").get_json()
    assert d["origin"]["talk_id"] == proposal_tk and d["origin"]["title"] == "利用者を増やす"


# ── §2-2: 合意済み提議から子プロジェクトが導出できる（立ち上げ二重化の防止材料）──
def test_agreed_proposal_knows_its_child_project():
    cid = _community()
    proposal_tk, purpose = _agree_proposal(cid)
    # まだ子は無い
    d0 = _cli().get(f"/api/talks/{proposal_tk}").get_json()
    assert d0["child_project"] is None
    lr = _cli("u_alice").post(f"/api/community/{cid}/projects/launch",
                              json={"title": "PJ", "purpose_ref": purpose}).get_json()
    # 立ち上げ後は子が導出できる
    d1 = _cli().get(f"/api/talks/{proposal_tk}").get_json()
    assert d1["child_project"]["talk_id"] == lr["talk"]["talk_id"]


# ── §1-3: ルート一覧の3区分（プロジェクト／審議中の提議／合意済みの提議）──────
def test_root_list_sections():
    cid = _community()
    # 審議中の提議（分母2で単独賛成では合意しない）
    _cli("u_alice").post(f"/api/community/{cid}/talks",
                         json={"kind": "admission", "title": "admit bob", "target": {"candidate": "u_bob"}})
    # bob を承認して分母2に
    atk = [t for t in _cli("u_alice").get(f"/api/community/{cid}/talks").get_json()["talks"]
           if t["kind"] == "admission"][0]["talk_id"]
    _cli("u_alice").post(f"/api/talks/{atk}/vote", json={"stance": "approve"})
    _cli("u_alice").post(f"/api/community/{cid}/talks",
                         json={"kind": "proposal", "title": "審議中の提議", "target": {}})
    # 合意済みの提議（分母2なので両者賛成で成立）＋そこから立ち上げたプロジェクト
    atk2 = _cli("u_alice").post(f"/api/community/{cid}/talks",
                                json={"kind": "proposal", "title": "合意済みの提議", "target": {}}).get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{atk2}/vote", json={"stance": "approve"})
    _cli("u_bob").post(f"/api/talks/{atk2}/vote", json={"stance": "approve"})
    purpose = _cli("u_alice").get(f"/api/talks/{atk2}").get_json()["result"]["purpose_event_hash"]
    _cli("u_alice").post(f"/api/community/{cid}/projects/launch",
                         json={"title": "PJ", "purpose_ref": purpose})
    talks = _cli("u_alice").get(f"/api/community/{cid}/talks").get_json()["talks"]
    sections = {}
    for t in talks:
        sections.setdefault(t["section"], []).append(t["title"])
    assert "project" in sections
    assert "審議中の提議" in sections.get("proposal_live", [])
    assert "合意済みの提議" in sections.get("proposal_agreed", [])
    # プロジェクトは出自を常時1行で持つ
    proj = [t for t in talks if t["section"] == "project"][0]
    assert proj["origin"]["title"] == "合意済みの提議"
