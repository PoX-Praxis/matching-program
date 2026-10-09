"""指示書63 段階1 PR-D — 受信箱のカード化・承認を1か所に・見送る・申し出の経路（テスト 294〜303）。

見送りは通常DBの状態（declined）だけで、台帳には書かない。どちらかが新しい版を作るまで、二人の間では申し出られず、
照合の結果からも互いに外し続ける（#129 の除外に足すだけ）。承認は受信箱だけ。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import ledger  # noqa: E402
import ledger_events as le  # noqa: E402
import subject_ledger as SL  # noqa: E402
import app as appmod  # noqa: E402
import v5  # noqa: E402
from test_t61_resonance_gate import X, Z, _cli, _confirm, _doc, _groups, _v5_person, db, world  # noqa: E402,F401

TPL = lambda name: open(os.path.join(ROOT, "templates", name), encoding="utf-8").read()  # noqa: E731


def _offer(frm, to="me", **body):
    pid = v5.live_necessities_v5(frm, db_path=appmod.DB)
    payload = {"to_id": to, **({"purpose_id": pid[0]["purpose_id"]} if pid else {}), **body}
    return _cli(frm).post("/approve", json=payload)


def _row(frm, to):
    with ledger._connect(appmod.DB) as con:
        return con.execute("SELECT status, offer_message, channel FROM connection_requests "
                           "WHERE from_subject=%s AND to_subject=%s ORDER BY created_at DESC", (frm, to)).fetchone()


def _new_version(sid):
    """新しい版（profile.structured）を作る（作り直し・v4 の編集と同じ記録）。"""
    SL.publish_profile_structured(sid, {"will_text": f"作り直した意志 {len(le.get_events(db_path=appmod.DB))}"},
                                  actor=sid, db_path=appmod.DB)


def _reason(me, other):
    return _cli(me).get(f"/api/connections/reason?with={other}").get_json()["reason"]


# ── 294 見送る: 通常DBの状態だけ・台帳に書かない・受信箱から消える ─────────────────────────
def test_t294_decline_only_in_normal_db(world):
    assert _offer("c_ok", message="一緒に考えたい").status_code == 200
    assert [o["from"] for o in _cli("me").get("/api/my/offers").get_json()] == ["c_ok"]
    n_events = len(le.get_events(db_path=appmod.DB))
    r = _cli("me").post("/api/connections/decline", json={"to_id": "c_ok"})
    assert r.status_code == 200 and r.get_json() == {"declined": True}
    assert len(le.get_events(db_path=appmod.DB)) == n_events                 # 台帳に書かない
    status, msg, _ = _row("c_ok", "me")
    assert status == "declined" and msg is None                                # 申し出の文も消える
    assert _cli("me").get("/api/my/offers").get_json() == []                   # 受信箱から消える
    assert ledger.connection_state("me", "c_ok", db_path=appmod.DB) == "declined_in"
    assert ledger.connection_state("c_ok", "me", db_path=appmod.DB) == "declined_out"
    assert _cli("me").get("/api/connections/reason?with=c_ok").status_code == 403   # 根拠も引けない


# ── 295 見送った相手は、照合の結果に出直さない（どちら向きでも） ─────────────────────────────
def test_t295_exclusion_continues_after_decline(world):
    _, by = _groups("me")
    assert any("c_ok" in ids for ids in by.values())
    _offer("c_ok")
    _cli("me").post("/api/connections/decline", json={"to_id": "c_ok"})
    _, by = _groups("me")
    assert not any("c_ok" in ids for ids in by.values())
    _, by_ok = _groups("c_ok")
    assert not any("me" in ids for ids in by_ok.values())
    blocked, _ = ledger.engaged_by_purpose("me", db_path=appmod.DB)
    assert "c_ok" in blocked and "c_ok" in ledger.engaged_counterparts("me", db_path=appmod.DB)


# ── 296 どちらかが新しい版を作るまで申し出られない。作れば再び申し出られる ─────────────────────
def test_t296_reoffer_blocked_until_new_version(world):
    _offer("c_ok")
    _cli("me").post("/api/connections/decline", json={"to_id": "c_ok"})
    r = _offer("c_ok")
    assert r.status_code == 409 and r.get_json()["error"] == "declined"
    assert "今回は見送られました" in r.get_json()["message"]
    assert _cli("me").post("/approve", json={"to_id": "c_ok"}).status_code == 409   # 見送った側からも
    assert _row("c_ok", "me")[0] == "declined"                                        # 申し出は増えない
    _new_version("me")                                                                # 見送った側が作り直しても解ける
    assert ledger.connection_state("c_ok", "me", db_path=appmod.DB) == "none"
    assert _offer("c_ok").status_code == 200 and _row("c_ok", "me")[0] == "pending"
    assert ledger.connection_state("me", "c_ok", db_path=appmod.DB) == "pending_in"


def test_t296b_new_version_by_offerer_also_lifts(world):
    _offer("c_ok")
    _cli("me").post("/api/connections/decline", json={"to_id": "c_ok"})
    _new_version("c_ok")
    assert _offer("c_ok").status_code == 200


# ── 297 見送れるのは受けた側だけ ─────────────────────────────────────────────────
def test_t297_only_receiver_can_decline(world):
    _offer("c_ok")
    assert _cli("c_ok").post("/api/connections/decline", json={"to_id": "me"}).status_code == 404  # 申し出た側
    assert _cli("c_gate").post("/api/connections/decline", json={"me": "me", "to_id": "c_ok"}).status_code == 403
    assert appmod.app.test_client().post("/api/connections/decline", json={"to_id": "c_ok"}).status_code == 401
    assert _row("c_ok", "me")[0] == "pending"
    assert _cli("me").post("/api/connections/decline", json={"to_id": "c_low"}).status_code == 404  # 申し出が無い


# ── 298 申し出た側には「今回は見送られました」 ─────────────────────────────────────────
def test_t298_offerer_sees_declined(world):
    _offer("c_ok")
    _cli("me").post("/api/connections/decline", json={"to_id": "c_ok"})
    d = _cli("c_ok").get("/api/my/declined").get_json()
    assert [x["to"] for x in d] == ["me"] and d[0]["to_name"]
    assert _cli("me").get("/api/my/declined").get_json() == []                # 見送った側には出ない
    assert _cli("me").get("/api/my/declined?id=c_ok").status_code == 403       # 本人だけ
    my = TPL("mypage.html")
    assert "今回は見送られました" in my and "/api/my/declined" in my
    prof = TPL("profile.html")
    assert 'state === "declined_out" ? "今回は見送られました。' in prof
    _new_version("c_ok")
    assert _cli("c_ok").get("/api/my/declined").get_json() == []              # 作り直せば消え、また申し出られる


# ── 299 申し出の経路（channel）を記録する。画面には出さない ────────────────────────────────
def test_t299_channel_recorded_not_shown(world):
    _offer("c_ok", channel="match")
    assert _row("c_ok", "me")[2] == "match"
    _offer("c_gate", channel="profile")
    assert _row("c_gate", "me")[2] == "profile"
    _offer("c_low", channel="なにか")
    assert _row("c_low", "me")[2] is None                                       # 決めた 2 つ以外は記録しない
    blob = json.dumps([e["payload"] for e in le.get_events(db_path=appmod.DB)], ensure_ascii=False)
    assert '"channel"' not in blob                                              # 台帳ではない
    assert 'channel: "match"' in TPL("connect.html") and 'channel: "profile"' in TPL("profile.html")
    for t in ("inbox.html", "mypage.html", "_match_reason.html"):
        assert "channel" not in TPL(t)
    assert "channel" not in json.dumps(_cli("me").get("/api/my/offers").get_json())


# ── 300 受信箱のカードの根拠: 方向ごとの目的・欠かせない要素・片方向・現在の版 ───────────────────
def test_t300_card_reason_fields_one_way(world):
    _offer("c_ok")
    r = _reason("me", "c_ok")
    assert {x["kind"] for x in r["reasons"]} == {"fill_mine"}                  # 片方向だけ（「互いに」の節は無い）
    x = r["reasons"][0]
    assert x["purpose_label"] == "あなたの目的 1" and x["purpose_text"] == "自然に辿り着ける状態を実現したい。"
    assert x["need_label"] == "あなたが求めていること" and x["offer_label"] == "相手が応えるもの"
    assert x["must"] is True and r["basis"] == "current" and r["current_profile"] is True
    for k in ("score", "cos", "gate", "0.7"):
        assert k not in json.dumps(r, ensure_ascii=False)


def test_t300b_mutual_direction_b_first_with_purpose(world):
    _v5_person(world, "c_mut", needs1=[Z, Z], needs2=[[0.0, -1.0, 0.0]], offers=[X, X], dest=X)
    _offer("c_mut")
    r = _reason("me", "c_mut")
    first, second = r["reasons"]
    assert first["kind"] == "fill_theirs" and first["purpose_label"] == "相手の目的"
    assert first["purpose_text"] == "自然に辿り着ける状態を実現したい。" and first["must"] is True
    assert second["kind"] == "fill_mine" and second["purpose_label"].startswith("あなたの目的")


def test_t300c_offer_time_basis_after_offerer_restructures(world):
    _offer("c_ok")
    _confirm("c_ok", _doc(与え像=[{"文": "作り直した後の、力になれることの文。", "型": "関わり方"}]))
    r = _reason("me", "c_ok")
    assert r["basis"] == "offer_time" and r["current_profile"] is False


# ── 301 v4 の相手（現状と比べた方向）は「照合は相手の現状の全体で行っています」 ─────────────────────
def test_t301_v4_counterpart_whole_state_note(world):
    assert _cli("c_v4ok").post("/approve", json={"to_id": "me"}).status_code == 200
    x = _reason("me", "c_v4ok")["reasons"][0]
    assert x["kind"] == "fill_mine" and x["offer_label"] == "相手が応えるもの"
    assert x["state_note"] == "照合は相手の現状の全体で行っています"


# ── 302 根拠が無いときは「照合による根拠はありません」だけ ───────────────────────────────────
def test_t302_no_reason(world):
    _offer("c_gate")                                                            # 門で通らない相手
    assert _cli("me").get("/api/connections/reason?with=c_gate").get_json() == {"reason": None}
    part = TPL("_match_reason.html")
    body = part[part.index("function approvalHtml("):part.index("/* 申し出の文（受けた本人だけに届く）。 */")]
    assert "照合による根拠はありません" in body and "互いに" not in body and "mr-arrow" not in body
    assert "現在のプロフィールにもとづく照合です" in body


# ── 303 承認は受信箱だけ（マイページは「承認待ち N件」、プロフィールは受信箱へのリンク） ───────────────
def test_t303_single_approval_place():
    inbox = TPL("inbox.html")
    assert "<table>" in inbox and inbox.count("<table>") == 1                  # 表は未読メッセージだけ
    assert 'onclick="doApprove(' in inbox and 'onclick="askDecline(this)"' in inbox
    assert "見送ると相手に伝わります。元に戻せません" in inbox
    cards = inbox[inbox.index("function renderApprovals("):inbox.index("function renderMessages(")]
    assert "に申し出が届きました" in cards and "badge" not in cards and "<table" not in cards   # 状態は見出し行の文
    assert "PoXReason.approvalHtml" in inbox
    my = TPL("mypage.html")
    assert "doApprove" not in my and "承認待ち ${waiting.length}件" in my and 'href="/inbox"' in my
    assert "PoXReason" not in my                                                # 申し出の文・根拠はここに出さない
    prof = TPL("profile.html")
    assert "受信箱で応える" in prof and "承認する" not in prof and "approvalContext" not in prof
    assert "取り下げる" in my and "取り下げる" in prof                            # 取り下げは残す
