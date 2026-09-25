"""指示書48 — 行為権の確定・参加申し出の出し分け・提議トークの書き込み権限。

項目番号つき受入テスト（108-k〜p・108-r・108-s, 111〜117, 113-a）と、渡された論点の回答テスト
（G-1: 提議トーク詳細で合意を確定できる／G-2: 折りたたみ「▼▼」の解消・実績の空理由文言）。

方針（§4）:
  - 権限は API で検証する（未ログイン 401／未参加 403／参加 201・画面に依存しない）。
  - 表示の可否は GET /talk/<id> のレンダリング結果（HTML）で検証する。行為の導線は閲覧者ごとに
    サーバーが描画し、不要な閲覧者には要素そのものを出さない（id="..." の有無で判定）。
  - 台帳は既存の型のみ（新イベントを作らない）。分母は基準点の参加者頭数（決定性）。
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


def _page(sid, talk_id):
    """閲覧者 sid（None＝未ログイン）で詳細ページを開き、レンダリング結果の HTML を返す。"""
    r = _cli(sid).get(f"/talk/{talk_id}")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _offer(cid, intent_id, who):
    return _cli(who).post(f"/api/community/{cid}/talks",
                          json={"kind": "project_join", "title": "参加の申し出",
                                "target": {"intent_id": intent_id, "participant": who,
                                           "participant_kind": "individual"}})


def _proposal_talk(cid, founder="u_alice"):
    """審議中（未合意＝未 closure）の提議トークを作って talk_id を返す。"""
    return _cli(founder).post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "審議中", "target": {}}).get_json()["talk_id"]


# ── 108-k: 立ち上げ者（＝既に参加者）には「申し出る」導線が出ない ─────────────
def test_t108k_launcher_gets_no_join_offer():
    cid = _community()
    _iid, ptk = _project(cid)
    assert _cli("u_alice").get(f"/api/talks/{ptk}").get_json()["join_offer"] is None
    html = _page("u_alice", ptk)                           # u_alice が立ち上げ者
    assert 'id="participateSec"' not in html
    assert 'id="joinIndividualBtn"' not in html and 'id="joinCommunityBtn"' not in html


# ── 108-l / 108-s: 詳細に現在の参加者と分母（N 名）を常時表示（未ログインでも）──
def test_t108l_participants_and_denominator_always_shown():
    cid = _community()
    _cli("u_alice").post("/api/my/display-name", json={"id": "u_alice", "name": "カオル"})
    _iid, ptk = _project(cid)
    for viewer in (None, "u_ext", "u_alice"):              # 未ログイン・未参加・参加者の全員に
        html = _page(viewer, ptk)
        assert 'id="participantsSec"' in html
        assert "参加者（1名）: カオル" in html               # 参加者名と頭数
        assert "合意の分母" in html                          # 分母の説明


def test_t108s_participant_list_on_project_detail_after_join():
    cid = _community()
    iid, ptk = _project(cid)
    _cli("u_bob").post("/api/my/display-name", json={"id": "u_bob", "name": "ボブ"})
    jtk = _offer(cid, iid, "u_bob").get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"})   # 分母1で成立
    html = _page(None, ptk)
    assert "参加者（2名）" in html and "ボブ" in html      # 審議画面だけでなく詳細にも一覧


# ── 108-m / 108-p: 所属コミュニティの一般メンバー（未参加）→ 個人のみ・コミュニティは出さない ──
def test_t108m_no_community_join_on_own_project():
    cid = _community()
    _iid, ptk = _project(cid)             # 立ち上げ（代表単独で合意）後にメンバーを増やす
    _add_member(cid, "u_carol")           # u_carol は所属コミュニティの一般メンバー・未参加
    assert _cli("u_carol").get(f"/api/talks/{ptk}").get_json()["join_offer"]["affiliation"] == "owning_member"
    html = _page("u_carol", ptk)
    assert 'id="joinCommunityBtn"' not in html            # 108-m
    assert "コミュニティとして参加を申し出る" not in html


def test_t108p_owning_member_can_offer_as_individual():
    cid = _community()
    _iid, ptk = _project(cid)
    _add_member(cid, "u_carol")
    html = _page("u_carol", ptk)
    assert 'id="joinIndividualBtn"' in html               # 108-p
    assert "個人として参加を申し出る" in html


# ── 108-n: 所属コミュニティが無い（外部個人）→ 個人のみ＋「所属していない」旨 ──────
def test_t108n_unaffiliated_individual_note():
    cid = _community()
    _iid, ptk = _project(cid)
    html = _page("u_ext", ptk)                             # u_ext はどこにも所属なし
    assert 'id="affiliationNote"' in html and "所属していない" in html
    assert 'id="joinIndividualBtn"' in html
    assert 'id="joinCommunityBtn"' not in html


# ── 他コミュニティのメンバー（未参加）→ 個人＋コミュニティの両方が出る ──────────
def test_join_offer_other_community_member_gets_both():
    cid = _community("u_alice")
    _iid, ptk = _project(cid)
    _cli("u_bob").post("/api/communities", json={"name": "B", "founder_id": "u_bob"})
    html = _page("u_bob", ptk)
    assert 'id="joinIndividualBtn"' in html and 'id="joinCommunityBtn"' in html
    assert 'id="affiliationNote"' not in html


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


# ── 117: コミュニティのメンバーでもプロジェクト未参加なら書き込めない（403）──────
def test_t117_member_not_participant_cannot_post_403():
    cid = _community()
    _iid, ptk = _project(cid)
    _add_member(cid, "u_carol")
    assert _cli("u_carol").post(f"/api/talks/{ptk}/posts", json={"body": "x"}).status_code == 403


# ── 115: 未参加者には発言欄を出さない／API 直叩きは 403 ───────────────────────
def test_t115_no_post_box_for_non_participant():
    cid = _community()
    _iid, ptk = _project(cid)
    _add_member(cid, "u_carol")
    for viewer in ("u_ext", "u_carol", None):              # 外部・未参加メンバー・未ログイン
        assert 'id="actionSec"' not in _page(viewer, ptk)
        assert 'id="postBody"' not in _page(viewer, ptk)
    assert 'id="actionSec"' in _page("u_alice", ptk)        # 当事者には出る
    assert _cli("u_ext").post(f"/api/talks/{ptk}/posts", json={"body": "x"}).status_code == 403


# ── 116: 未ログインの発言 POST は 401 ─────────────────────────────────────────
def test_t116_guest_post_401():
    cid = _community()
    _iid, ptk = _project(cid)
    assert _cli().post(f"/api/talks/{ptk}/posts", json={"body": "x"}).status_code == 401


# ── 111: 実行中プロジェクトの詳細で参加者が達成を提案できる（未参加者には出ない／403）──
def test_t111_participant_can_propose_complete():
    cid = _community()
    iid, ptk = _project(cid)
    _add_member(cid, "u_carol")
    # 参加者（立ち上げ者）: 詳細に「達成を提案」があり、起票は 201
    html = _page("u_alice", ptk)
    assert 'id="completeAction"' in html and "達成を提案" in html
    r = _cli("u_alice").post(f"/api/community/{cid}/talks",
                             json={"kind": "project_complete", "title": "達成",
                                   "target": {"intent_id": iid, "result": "できた"}})
    assert r.status_code == 201


def test_t111_non_participant_has_no_complete_action():
    cid = _community()
    iid, ptk = _project(cid)
    _add_member(cid, "u_carol")
    for viewer in ("u_carol", "u_ext", None):
        html = _page(viewer, ptk)
        assert 'id="completeAction"' not in html and 'id="completeModal"' not in html
    r = _cli("u_carol").post(f"/api/community/{cid}/talks",
                             json={"kind": "project_complete", "title": "達成",
                                   "target": {"intent_id": iid, "result": "x"}})
    assert r.status_code == 403                             # メンバーでも未参加なら 403


# ── 112: 未ログインでは申し出の導線が出ず、API 直叩きは 401 ─────────────────────
def test_t112_guest_no_join_offer_and_api_401():
    cid = _community()
    iid, ptk = _project(cid)
    html = _page(None, ptk)
    assert 'id="participateSec"' not in html and 'id="joinIndividualBtn"' not in html
    r = _cli().post(f"/api/community/{cid}/talks",
                    json={"kind": "project_join", "title": "x",
                          "target": {"intent_id": iid, "participant": "u_x"}})
    assert r.status_code == 401                                               # 行為は 401


# ── 108-r: 参加の合意の分母に申し出者自身を含めない（既存参加者のみ）──────────────
def test_t108r_offerer_not_in_denominator():
    cid = _community()
    iid, ptk = _project(cid)
    jtk = _offer(cid, iid, "u_ext").get_json()["talk_id"]
    d = _cli().get(f"/api/talks/{jtk}").get_json()
    assert [x["subject_id"] for x in d["denominator"]] == ["u_alice"]
    # 申し出た本人は票を持たない（403）が、申し出の説明は書ける（201）
    assert _cli("u_ext").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"}).status_code == 403
    assert _cli("u_ext").post(f"/api/talks/{jtk}/posts", json={"body": "よろしく"}).status_code == 201
    assert 'id="voteButtons"' not in _page("u_ext", jtk)
    assert 'id="voteButtons"' in _page("u_alice", jtk)
    # 既存参加者（分母1）の賛成だけで成立する
    r = _cli("u_alice").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"}).get_json()
    assert r["commit"]["committed"] is True
    ev = [e for e in le.get_events(type_="intent.participant.joined", db_path=appmod.DB)
          if e["payload"]["participant"] == "u_ext"][0]
    assert ev["payload"]["approvals"] == ["u_alice"]


# ── §4-4 決定性: 分母＝基準点の時点の参加者頭数（現在の参加者を分母にしない）──────
def test_t108b_denominator_is_basis_not_current_participants():
    cid = _community()
    iid, _ptk = _project(cid)
    j1 = _offer(cid, iid, "u_ext").get_json()["talk_id"]     # 基準点: 参加者 {u_alice}
    j2 = _offer(cid, iid, "u_bob").get_json()["talk_id"]
    _cli("u_alice").post(f"/api/talks/{j2}/vote", json={"stance": "approve"})  # u_bob が参加
    # u_bob が加わった後も、j1 の分母は基準点の {u_alice} のまま（現在の {u_alice, u_bob} ではない）
    d = _cli().get(f"/api/talks/{j1}").get_json()
    assert [x["subject_id"] for x in d["denominator"]] == ["u_alice"]
    r = _cli("u_alice").post(f"/api/talks/{j1}/vote", json={"stance": "approve"}).get_json()
    assert r["commit"]["committed"] is True                  # u_bob の票を待たずに成立


# ── 114: 参加・加入系の画面・API で生 id を出さない（表示名未設定のフォールバック）──
def test_t114_no_raw_id_for_unnamed_accounts():
    cid = _community()                                       # u_alice も u_ext も表示名未設定
    iid, ptk = _project(cid)
    jtk = _offer(cid, iid, "u_ext").get_json()["talk_id"]
    _cli("u_ext").post(f"/api/talks/{jtk}/posts", json={"body": "よろしく"})
    d = _cli().get(f"/api/talks/{jtk}").get_json()
    shown = ([d["created_by_name"]] + [p["author_name"] for p in d["posts"]]
             + [x["display_name"] for x in d["denominator"]])
    assert shown and all(n == "表示名未設定のアカウント" for n in shown)
    # プロジェクト詳細の参加者一覧（サーバー描画）にも生 id が出ない
    html = _page(None, ptk)
    assert "u_alice" not in html and "表示名未設定のアカウント" in html
    # 加入申請者（pending）の表示名もフォールバック（メンバーが見る画面）
    _cli("u_new").post(f"/api/community/{cid}/join", json={"member_id": "u_new"})
    c = _cli("u_alice").get(f"/api/community/{cid}").get_json()
    assert [p["display_name"] for p in c["pending"]] == ["表示名未設定のアカウント"]
    assert c["founder_name"] == "表示名未設定のアカウント"


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
