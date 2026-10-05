"""指示書50 v3 — 軌跡（版ごとのツリー）。項目 122〜144・129-a。

版＝user_snapshots（台帳 profile.structured の n で番号づけ）。枝＝その版から生まれた公開済みの関係。
第三者にも本文を見せる（F5 撤回）。根拠・内部数値・raw は本人のみ。伏せ（vulnerable_hidden）は
第三者にだけ効く。台帳には書かない。
"""
import json, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import ledger_events as le
import snapshots
import subject_ledger
from ledger import approve


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.setenv("POX_NECESSITY_PUBLIC_SINCE", "2000-01-01T00:00:00+00:00")
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


def _version(uid, will, *, nec="翻訳できる開発者", ev="根拠SECRET", raw="RAW秘密", bg="背景の文"):
    """版を 1 つ作る（台帳 profile.structured ＋ user_snapshots。確定経路と同じ content_hash）。"""
    sup = {"背景": bg, "生テキスト": [raw]}
    pi = {"will_text": will, "state_have": "知識", "supporting_raw": sup}
    subject_ledger.publish_profile_structured(uid, pi, db_path=appmod.DB)
    return snapshots.save_snapshot(
        uid, will_text=will, state={"state_have": "知識"}, supporting=sup,
        necessity={"necessity_text": nec, "evidence_span": ev, "gate_s": 0.5, "alpha": 1.0},
        content_hash=subject_ledger.profile_content_hash(pi), db_path=appmod.DB)


def _connect(a, b):
    for x, y in ((a, b), (b, a)):
        approve(x, y, db_path=appmod.DB, establish_hook=appmod._snapshot_pair_resolver,
                ref_resolver=appmod._conn_ref_resolver)


def _tl(uid, viewer=None):
    r = _cli(viewer).get(f"/api/timeline/{uid}")
    assert r.status_code == 200
    return r.get_json()


def _two_versions_and_connection():
    """u_a: 第1版 → u_b と接続 → 第2版（現在）。u_b: 第1版。"""
    _version("u_a", "つなぎたい（第1版）")
    _version("u_b", "Bの意志")
    _connect("u_a", "u_b")
    _version("u_a", "翻訳の場をつくる（第2版）")


# 122: 版ごとのツリー（版＝意志・現状・必要像、枝＝その版から生まれた関係）
def test_t122_version_tree():
    _two_versions_and_connection()
    d = _tl("u_a", "u_a")
    assert [v["n"] for v in d["versions"]] == [1, 2]
    assert d["versions"][0]["content"]["will_text"] == "つなぎたい（第1版）"
    assert [b["kind"] for b in d["versions"][0]["branches"]] == ["connection"]
    assert d["versions"][1]["branches"] == []
    assert d["version_count"] == 2 and d["change_count"] == 1


# 123: 第三者（未ログイン）にも本文（意志・現状・必要像・表示用項目）が出る
def test_t123_third_party_sees_text():
    _two_versions_and_connection()
    c = _tl("u_a")["versions"][0]["content"]
    assert c["will_text"] == "つなぎたい（第1版）" and c["state"]["state_have"] == "知識"
    assert c["necessity_text"] == "翻訳できる開発者" and c["supporting"] == {"背景": "背景の文"}


# 124・125: 根拠・内部数値・raw は第三者にも相手にも出ない（API でも）。本人には出る
def test_t124_t125_evidence_numbers_raw_owner_only():
    _two_versions_and_connection()
    for viewer in (None, "u_b", "u_x"):
        d = _tl("u_a", viewer)
        blob = json.dumps(d, ensure_ascii=False)
        assert "根拠SECRET" not in blob and "RAW秘密" not in blob and "gate_s" not in blob
        assert "evidence_span" not in blob and "numbers" not in blob
    own = _tl("u_a", "u_a")["versions"][0]["content"]
    assert own["evidence_span"] == "根拠SECRET" and own["numbers"]["gate_s"] == 0.5
    assert own["supporting"]["生テキスト"] == ["RAW秘密"]


# 126: 接続は「自分 第N版 × 相手 第M版」
def test_t126_connection_shows_both_versions():
    _two_versions_and_connection()
    b = _tl("u_a")["versions"][0]["branches"][0]
    assert (b["self_version"], b["other_version"]) == (1, 1)
    b2 = _tl("u_b")["versions"][0]["branches"][0]
    assert (b2["self_version"], b2["other_version"]) == (1, 1)
    html = open(os.path.join(ROOT, "templates", "_trajectory.html"), encoding="utf-8").read()
    assert "（自分 第${ver(b.self_version)}版" in html and "第${ver(b.other_version)}版" in html
    assert "目的: ${esc(b.self_purpose)}" in html          # 指示書57: 接続の枝は目的ごと


# 127・128: 伏せると第三者に本文が出ず、接続の相手には出る。事実（日付・種別・枝）は残る
def test_t127_t128_hidden_version():
    _two_versions_and_connection()
    sid = _tl("u_a", "u_a")["versions"][0]["snapshot_id"]
    r = _cli("u_a").post(f"/api/snapshot/{sid}/visibility", json={"id": "u_a", "hidden": True})
    assert r.status_code == 200
    third = _tl("u_a")["versions"][0]
    assert third["content"] is None and third["hidden"] is True
    assert third["at"] and third["n"] == 1 and third["branches"][0]["kind"] == "connection"   # 128
    partner = _tl("u_a", "u_b")["versions"][0]
    assert partner["content"]["will_text"] == "つなぎたい（第1版）"
    assert _tl("u_a", "u_x")["versions"][0]["content"] is None              # 無関係のログインも第三者


def _community_project():
    """u_a がコミュニティを作り、提議→合意→プロジェクトを立ち上げる。"""
    cid = _cli("u_a").post("/api/communities", json={"name": "C1", "founder_id": "u_a"}).get_json()["id"]
    tk = _cli("u_a").post(f"/api/community/{cid}/talks",
                          json={"kind": "proposal", "title": "登録者を増やす", "target": {}}).get_json()["talk_id"]
    _cli("u_a").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    purpose = _cli().get(f"/api/talks/{tk}").get_json()["result"]["purpose_event_hash"]
    lr = _cli("u_a").post(f"/api/community/{cid}/projects/launch",
                          json={"title": "登録者PJ", "purpose_ref": purpose}).get_json()
    return cid, tk, lr["intent_id"]


def _offer(cid, iid, who):
    return _cli(who).post(f"/api/community/{cid}/talks",
                          json={"kind": "project_join", "title": "参加",
                                "target": {"intent_id": iid, "participant": who}}).get_json()["talk_id"]


def _kinds(uid, viewer=None):
    d = _tl(uid, viewer)
    return [b["kind"] for v in d["versions"] for b in v["branches"]] + \
        [b["kind"] for b in d["before_first_version"]]


# 129: 見送り・却下は載らない（プロジェクト参加の見送り・加入の見送りの両方）
def test_t129_setbacks_not_on_trajectory():
    _version("u_b", "B")
    cid, _tk, iid = _community_project()
    jtk = _offer(cid, iid, "u_b")
    _cli("u_a").post(f"/api/talks/{jtk}/vote", json={"stance": "dissent"})      # 参加の見送り
    _cli("u_b").post(f"/api/community/{cid}/join", json={"member_id": "u_b"})
    atk = _cli("u_a").post(f"/api/community/{cid}/talks",
                           json={"kind": "admission", "title": "a", "target": {"candidate": "u_b"}}).get_json()["talk_id"]
    _cli("u_a").post(f"/api/talks/{atk}/vote", json={"stance": "dissent"})
    assert _cli("u_a").post(f"/api/talks/{atk}/decline", json={}).status_code == 200   # 加入の見送り
    assert _kinds("u_b") == [] and _kinds("u_b", "u_b") == []


# 129-a: 加入の申請中は枝に出ない（成立した member.joined だけ）
def test_t129a_pending_admission_not_a_branch():
    _version("u_b", "B")
    cid, _tk, _iid = _community_project()
    _cli("u_b").post(f"/api/community/{cid}/join", json={"member_id": "u_b"})
    assert "joined_community" not in _kinds("u_b", "u_b")
    atk = _cli("u_a").post(f"/api/community/{cid}/talks",
                           json={"kind": "admission", "title": "a", "target": {"candidate": "u_b"}}).get_json()["talk_id"]
    _cli("u_a").post(f"/api/talks/{atk}/vote", json={"stance": "approve"})
    assert "joined_community" in _kinds("u_b")                                    # 成立後は出る


# 130: 台帳イベントが増えない（表示・伏せの操作で）
def test_t130_no_ledger_events():
    _two_versions_and_connection()
    n = len(le.get_events(db_path=appmod.DB))
    sid = _tl("u_a", "u_a")["versions"][0]["snapshot_id"]
    _tl("u_a"); _tl("u_a", "u_b"); _cli().get("/trajectory/u_a")
    _cli("u_a").post(f"/api/snapshot/{sid}/visibility", json={"id": "u_a", "hidden": True})
    _cli("u_a").post(f"/api/snapshot/{sid}/visibility", json={"id": "u_a", "hidden": False})
    assert len(le.get_events(db_path=appmod.DB)) == n


# 131: 追記型（過去版を書き換えない）。伏せの変更は履歴として残る
def test_t131_append_only_and_visibility_history():
    _two_versions_and_connection()
    before = _tl("u_a", "u_a")["versions"][0]["content"]["will_text"]
    _version("u_a", "第3版の意志")
    assert _tl("u_a", "u_a")["versions"][0]["content"]["will_text"] == before      # 過去版は不変
    sid = _tl("u_a", "u_a")["versions"][0]["snapshot_id"]
    for h in (True, False, True):
        _cli("u_a").post(f"/api/snapshot/{sid}/visibility", json={"id": "u_a", "hidden": h})
    assert [x["hidden"] for x in snapshots.get_visibility_log(sid, db_path=appmod.DB)] == [True, False, True]
    rules = [(r.rule, r.methods) for r in appmod.app.url_map.iter_rules() if "snapshot" in r.rule]
    assert all(not ({"PUT", "PATCH", "DELETE"} & m) for _, m in rules)          # 過去版を編集する経路が無い


# 132: 装飾（表示名）の変更では版が増えない。省略は n の欠番として検出できる
def test_t132_decoration_does_not_create_version_and_gaps_visible():
    _version("u_a", "第1版")
    _cli("u_a").post("/api/my/display-name", json={"id": "u_a", "name": "カオル"})
    assert _tl("u_a")["version_count"] == 1
    # 台帳に第2版があるのにスナップショットが欠けていると、番号の欠番（1, 3）として現れる
    subject_ledger.publish_profile_structured("u_a", {"will_text": "欠けた第2版"}, db_path=appmod.DB)
    _version("u_a", "第3版")
    assert [v["n"] for v in _tl("u_a")["versions"]] == [1, 3]


# 133: 現行版のノードは本文を出さず「上に表示中」
def test_t133_current_version_shown_above():
    _two_versions_and_connection()
    for viewer in (None, "u_a"):
        cur = _tl("u_a", viewer)["versions"][-1]
        assert cur["current"] is True and cur["content"] is None and cur["shown_above"] is True
    html = open(os.path.join(ROOT, "templates", "_trajectory.html"), encoding="utf-8").read()
    assert "本文は上の「いまの姿」に表示中" in html


# 134: 過去版は単独ページを作らずアンカー（/trajectory/<id>#vN）。noindex
def test_t134_anchor_page_noindex():
    _two_versions_and_connection()
    r = _cli().get("/trajectory/u_a")
    assert r.status_code == 200 and r.headers.get("X-Robots-Tag") == "noindex, nofollow"
    assert _cli().get("/api/timeline/u_a").headers.get("X-Robots-Tag") == "noindex, nofollow"
    assert [v["anchor"] for v in _tl("u_a")["versions"]] == ["v1", "v2"]
    assert _cli().get("/trajectory/u_a/1").status_code == 404                     # 版ごとの単独ページは無い


# 135: 他人の軌跡を横断して並べられない・参加者で引けない
def test_t135_no_cross_listing_routes():
    for r in appmod.app.url_map.iter_rules():
        if "timeline" in r.rule or "trajectory" in r.rule:
            assert r.arguments == {"user_id"}, r.rule                           # 1 人ずつしか引けない
    assert _cli().get("/api/timeline").status_code == 404
    assert _cli().get("/api/trajectories").status_code == 404


# 136: 接続の相手が表示名で返る（未設定は「表示名未設定のアカウント」）
def test_t136_connection_other_display_name():
    _two_versions_and_connection()
    b = _tl("u_a")["versions"][0]["branches"][0]
    assert b["other_name"] == "表示名未設定のアカウント"
    _cli("u_b").post("/api/my/display-name", json={"id": "u_b", "name": "ボブ"})
    assert _tl("u_a")["versions"][0]["branches"][0]["other_name"] == "ボブ"
    conn = [i for i in _tl("u_a")["items"] if i["kind"] == "connection"][0]
    assert conn["other_name"] == "ボブ"


# 137: 注記「並び順は時間順であり、因果ではありません。」が出る（本人・相手・第三者・専用ページの全部）
def test_t137_causality_note():
    part = open(os.path.join(ROOT, "templates", "_trajectory.html"), encoding="utf-8").read()
    assert "並び順は時間順であり、因果ではありません。" in part
    for t in ("mypage.html", "profile.html", "trajectory.html"):
        assert '{% include "_trajectory.html" %}' in open(os.path.join(ROOT, "templates", t), encoding="utf-8").read()
    assert "並び順は時間順であり、因果ではありません。" in _cli().get("/trajectory/u_a").get_data(as_text=True)


# 138: 伏せの既定はオフ（新しい版は公開から始まる）
def test_t138_hidden_default_off():
    _version("u_a", "第1版"); _version("u_a", "第2版")
    d = _tl("u_a", "u_a")
    assert all(v["hidden"] is False for v in d["versions"])
    assert _tl("u_a")["versions"][0]["content"] is not None


# 139: 宣言・完了・参加・加入・接続・再構造化が版／枝として入る
def test_t139_all_kinds_present():
    _version("u_a", "Aの第1版"); _version("u_b", "Bの第1版")
    _connect("u_a", "u_b")
    cid, tk, iid = _community_project()
    jtk = _offer(cid, iid, "u_b")
    _cli("u_a").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"})
    ctk = _cli("u_a").post(f"/api/community/{cid}/talks",
                           json={"kind": "project_complete", "title": "達成",
                                 "target": {"intent_id": iid, "result": "r"}}).get_json()["talk_id"]
    _cli("u_a").post(f"/api/talks/{ctk}/vote", json={"stance": "approve"})
    _cli("u_b").post(f"/api/talks/{ctk}/vote", json={"stance": "approve"})
    _version("u_a", "Aの第2版")
    a = _kinds("u_a")
    for k in ("connection", "declared", "joined_community", "completed"):
        assert k in a, k
    assert {"connection", "joined_project", "completed"} <= set(_kinds("u_b"))
    d = _tl("u_a")
    assert [v["source"] for v in d["versions"]] == ["登録", "再構造化"]
    decl = [b for v in d["versions"] for b in v["branches"] if b["kind"] == "declared"][0]
    assert decl["title"] == "登録者を増やす" and decl["url"] == f"/talk/{tk}"


# 140・141: 色は種別のみ（変化の大小で濃淡・太さを変えない）。時間を等間隔にしない
def test_t140_t141_no_magnitude_styling_or_time_scale():
    html = open(os.path.join(ROOT, "templates", "_trajectory.html"), encoding="utf-8").read()
    js = html[html.index("<script>"):]
    assert 'class="tj-tag tj-${esc(b.kind)}"' in js                     # 色は種別のクラスだけ
    for w in ("opacity", "font-weight:${", "width:${", "height:${", "getTime", "margin-top:${", "flex-grow"):
        assert w not in js, w                                            # 量・時間幅から見た目を計算しない


# 142: 削除のある時点に「一部が伏せられています」
def test_t142_partially_redacted_marker():
    import redaction, talks
    _version("u_a", "Aの第1版")
    cid, tk, _iid = _community_project()
    decl = lambda: [b for v in _tl("u_a")["versions"] for b in v["branches"] if b["kind"] == "declared"][0]
    assert decl()["partially_redacted"] is False
    tk2 = _cli("u_a").post(f"/api/community/{cid}/talks",
                           json={"kind": "proposal", "title": "伏せる提議", "target": {}}).get_json()["talk_id"]
    pid = _cli("u_a").post(f"/api/talks/{tk2}/posts", json={"body": "他人の属性"}).get_json()["post_id"]
    _cli("u_a").post(f"/api/talks/{tk2}/vote", json={"stance": "approve"})
    ref = _cli().get(f"/api/talks/{tk2}").get_json()["result"]["purpose_event_hash"]
    with talks._connect(appmod.DB) as con:
        con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", (redaction.REDACTED_BODY, pid))
    redaction.record_redaction(tk2, ref, redacted_post_ids=[pid], reason_class="subject_request",
                               decided_by="u_op", db_path=appmod.DB)
    red = [b for v in _tl("u_a")["versions"] for b in v["branches"]
           if b["kind"] == "declared" and b["title"] == "伏せる提議"][0]
    assert red["partially_redacted"] is True
    assert "一部が伏せられています" in open(os.path.join(ROOT, "templates", "_trajectory.html"), encoding="utf-8").read()


# 143: 目的別の必要像は個人の軌跡に出ない（standing のみ）
def test_t143_only_standing_necessity():
    _version("u_a", "第1版", nec="standingの必要像"); _version("u_a", "第2版")
    cid, _tk, iid = _community_project()
    d = _tl("u_a", "u_a")
    necs = [v["content"]["necessity_text"] for v in d["versions"] if v["content"]]
    assert necs == ["standingの必要像"]
    for v in d["versions"]:
        for b in v["branches"]:
            assert "necessity_text" not in b                              # 枝に必要像を持たない


# 144: 「本人より」（view_overrides）の変更は版を作らない
def test_t144_view_overrides_no_version():
    pi = {"will_text": "同じ本文", "state_have": "知識", "supporting_raw": {}}
    ch = subject_ledger.profile_content_hash(pi)
    common = dict(will_text="同じ本文", state={"state_have": "知識"}, supporting={},
                  necessity={"necessity_text": "n"}, content_hash=ch, db_path=appmod.DB)
    assert snapshots.save_snapshot("u_a", view_overrides={"note": "1"}, **common)
    assert snapshots.save_snapshot("u_a", view_overrides={"note": "2 本人より書き直し"}, **common) is None
    assert _tl("u_a")["version_count"] == 1
