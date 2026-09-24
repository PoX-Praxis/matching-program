"""指示書48 — 行為権の確定・参加申し出の出し分け・提議トークの書き込み権限。

項目番号つき受入テスト（108-k〜p, 111, 112, 113, 113-a）と、渡された論点の回答テスト
（G-1: 提議トーク詳細で合意を確定できる／G-2: 折りたたみ「▼▼」の解消・実績の空理由文言）。

方針（§4）:
  - 権限は API で検証する（未ログイン 401／未参加 403／参加 201・画面に依存しない）。
  - 表示の出し分けはサーバーが返す構造化フラグ（join_offer / can_propose_complete）で検証し、
    文言の有無はテンプレートのレンダリング元（HTML）で検証する。
  - 台帳は既存の型のみ（新イベントを作らない）。分母は基準点の参加者頭数（決定性）。
"""
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


def _add_member(cid, cand, approver="u_alice"):
    """cand を承認済みメンバーにする（分母1＝代表単独承認で成立）。"""
    _cli(cand).post(f"/api/community/{cid}/join", json={"member_id": cand})
    atk = _cli(approver).post(f"/api/community/{cid}/talks",
                              json={"kind": "admission", "title": "a",
                                    "target": {"candidate": cand}}).get_json()["talk_id"]
    _cli(approver).post(f"/api/talks/{atk}/vote", json={"stance": "approve"})


def _project(cid, founder="u_alice"):
    """提議合意→プロジェクト立ち上げ。intent_id と project talk_id を返す（立ち上げ者＝当事者）。"""
    tk = _cli(founder).post(f"/api/community/{cid}/talks",
                            json={"kind": "proposal", "title": "P", "target": {}}).get_json()["talk_id"]
    _cli(founder).post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    purpose = _cli(founder).get(f"/api/talks/{tk}").get_json()["result"]["purpose_event_hash"]
    lr = _cli(founder).post(f"/api/community/{cid}/projects/launch",
                            json={"title": "PJ", "purpose_ref": purpose}).get_json()
    return lr["intent_id"], lr["talk"]["talk_id"]


def _proposal_talk(cid, founder="u_alice"):
    """審議中（未合意＝未 closure）の提議トークを作って talk_id を返す。"""
    return _cli(founder).post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "審議中", "target": {}}).get_json()["talk_id"]


# ── 108-k: 立ち上げ者（＝既に参加者）には「申し出る」導線が出ない ─────────────
def test_t108k_launcher_gets_no_join_offer():
    cid = _community()
    _iid, ptk = _project(cid)
    d = _cli("u_alice").get(f"/api/talks/{ptk}").get_json()   # u_alice が立ち上げ者
    assert d["join_offer"] is None


# ── 108-l: 詳細に現在の参加者と分母（基準点の N 名）を常時表示（未ログインでも）──
def test_t108l_participants_and_denominator_always_shown():
    cid = _community()
    _iid, ptk = _project(cid)
    d = _cli().get(f"/api/talks/{ptk}").get_json()            # 未ログインでも公開
    ids = [p["subject_id"] for p in d["participants"]]
    assert ids == ["u_alice"] and len(d["participants"]) == 1  # 立ち上げ者が参加者（頭数1）
    # テンプレートが「分母（基準点の参加者・N 名）」を常時描画する
    html = open(os.path.join(ROOT, "templates", "talk.html"), encoding="utf-8").read()
    assert "分母（基準点の参加者" in html
    assert 'id="participantsSec"' in html


# ── 108-m / 108-p: 所属コミュニティの一般メンバー（未参加）→ 個人のみ・コミュニティは出さない ──
def test_t108m_p_owning_member_individual_only():
    cid = _community()
    _iid, ptk = _project(cid)             # 立ち上げ（代表単独で合意）後にメンバーを増やす
    _add_member(cid, "u_carol")           # u_carol は所属コミュニティの一般メンバー・未参加
    off = _cli("u_carol").get(f"/api/talks/{ptk}").get_json()["join_offer"]
    assert off["individual"] is True      # 108-p: 「個人として参加を申し出る」が出る
    assert off["community"] is False      # 108-m: 「コミュニティとして参加」は出さない
    assert off["affiliation"] == "owning_member"


# ── 108-n: 所属コミュニティが無い（外部個人）→ 個人のみ＋「所属していない」旨 ──────
def test_t108n_unaffiliated_individual_note():
    cid = _community()
    _iid, ptk = _project(cid)
    off = _cli("u_ext").get(f"/api/talks/{ptk}").get_json()["join_offer"]  # u_ext は所属なし
    assert off["individual"] is True and off["community"] is False
    assert off["affiliation"] == "none"
    html = open(os.path.join(ROOT, "templates", "talk.html"), encoding="utf-8").read()
    assert "所属していない" in html      # 「所属していない」旨の文言をテンプレートが持つ


# ── 他コミュニティのメンバー（未参加）→ 個人＋コミュニティの両方が出る ──────────
def test_join_offer_other_community_member_gets_both():
    cid = _community("u_alice")
    _iid, ptk = _project(cid)
    # u_bob は別コミュニティ B のメンバー（この owning コミュニティには非所属・未参加）
    b = _cli("u_bob")
    b.post("/api/communities", json={"name": "B", "founder_id": "u_bob"})
    off = _cli("u_bob").get(f"/api/talks/{ptk}").get_json()["join_offer"]
    assert off["individual"] is True and off["community"] is True
    assert off["affiliation"] == "other_community"


# ── 108-o: プロジェクトの発言は当事者のみ（未ログイン401／非当事者403／参加者201）──
def test_t108o_only_participant_can_post_to_project():
    cid = _community()
    _iid, ptk = _project(cid)
    _add_member(cid, "u_carol")          # メンバーだが未参加
    # 未ログイン → 401
    assert _cli().post(f"/api/talks/{ptk}/posts", json={"body": "x"}).status_code == 401
    # メンバーでも未参加なら 403（メンバーであることは発言権の根拠にならない）
    assert _cli("u_carol").post(f"/api/talks/{ptk}/posts", json={"body": "x"}).status_code == 403
    # 非メンバー（ログイン済み・未参加）も 403
    assert _cli("u_ext").post(f"/api/talks/{ptk}/posts", json={"body": "x"}).status_code == 403
    # 参加者（立ち上げ者）は 201
    assert _cli("u_alice").post(f"/api/talks/{ptk}/posts", json={"body": "進捗"}).status_code == 201


# ── 111: 実行中プロジェクトの詳細で参加者が達成を提案できる（未参加者には出ない／403）──
def test_t111_participant_can_propose_complete():
    cid = _community()
    iid, ptk = _project(cid)
    _add_member(cid, "u_carol")
    # 参加者（立ち上げ者）: can_propose_complete=True、達成提案の起票は 201
    assert _cli("u_alice").get(f"/api/talks/{ptk}").get_json()["can_propose_complete"] is True
    r = _cli("u_alice").post(f"/api/community/{cid}/talks",
                             json={"kind": "project_complete", "title": "達成",
                                   "target": {"intent_id": iid, "result": "できた"}})
    assert r.status_code == 201
    # 未参加者（メンバー u_carol）: 導線は出ず、API 直叩きは 403
    assert _cli("u_carol").get(f"/api/talks/{ptk}").get_json()["can_propose_complete"] is False
    r2 = _cli("u_carol").post(f"/api/community/{cid}/talks",
                              json={"kind": "project_complete", "title": "達成",
                                    "target": {"intent_id": iid, "result": "x"}})
    assert r2.status_code == 403


# ── 112: 未ログインでは申し出の導線が出ず、API 直叩きは 401 ─────────────────────
def test_t112_guest_no_join_offer_and_api_401():
    cid = _community()
    iid, ptk = _project(cid)
    assert _cli().get(f"/api/talks/{ptk}").get_json()["join_offer"] is None   # 導線なし
    r = _cli().post(f"/api/community/{cid}/talks",
                    json={"kind": "project_join", "title": "x",
                          "target": {"intent_id": iid, "participant": "u_x"}})
    assert r.status_code == 401                                               # 行為は 401


# ── 113: 提議トークに未ログインで書き込むと 401 ───────────────────────────────
def test_t113_guest_post_to_proposal_401():
    cid = _community()
    tk = _proposal_talk(cid)
    assert _cli().post(f"/api/talks/{tk}/posts", json={"body": "x"}).status_code == 401


# ── 113-a: 提議トークに非メンバー（ログイン済み）で書き込むと 403 ────────────────
def test_t113a_nonmember_post_to_proposal_403():
    cid = _community()
    tk = _proposal_talk(cid)
    assert _cli("u_ext").post(f"/api/talks/{tk}/posts", json={"body": "x"}).status_code == 403
    # メンバー（代表）は書ける（§1-4 案A: 提議トークの書き込みはメンバー）
    assert _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "意見"}).status_code == 201


# ── G-1: 審議中の提議トーク詳細で合意を確定できる（収束案・賛成・合意の確定）──────
def test_g1_agree_on_proposal_detail():
    cid = _community()
    tk = _proposal_talk(cid)
    # 審議中（未合意）
    assert _cli().get(f"/api/talks/{tk}").get_json()["display_status"] == "審議中"
    # 賛成で合意が確定する（分母1＝代表単独賛成で成立）
    r = _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"}).get_json()
    assert r["commit"]["committed"] is True
    d = _cli().get(f"/api/talks/{tk}").get_json()
    assert d["display_status"] == "合意済み"
    assert d["result"]["purpose_event_hash"]          # 合意の確定（purpose.agreed が発行される）


# ── G-2: 折りたたみは <summary> 既定マーカーのみ（本文に「▼▼」が無い）／実績の空理由文言 ──
def test_g2_no_double_marker_and_achievement_empty_reason():
    html = open(os.path.join(ROOT, "templates", "community.html"), encoding="utf-8").read()
    assert "▼▼" not in html
    assert "まだ実績はありません" in html                      # 実績が空のときの理由文言
    assert "完了した取り組みから、できることを整理中です" in html  # 抽出前の理由文言


# ── §5 禁則: 人への判定語（拒否・不承認・却下）をテンプレートに書かない ────────────
def test_no_person_judgement_words_in_talk_templates():
    for fn in ("talk.html", "community.html"):
        html = open(os.path.join(ROOT, "templates", fn), encoding="utf-8").read()
        for w in ("拒否", "不承認", "却下"):
            assert w not in html, f"{fn}: {w}"
