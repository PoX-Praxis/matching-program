"""指示書63 段階1 PR-C — プロフィールの v5 表示（テスト 287〜293）。

v5 の人のプロフィールは 背景 → 意志 → 現状 → 目的ごと → 力になれること → 関心 → 本人より → 軌跡。
目的ごとの必要像は必要像と同じ公開の日時の閾値に従う。与え像（力になれること）は、プライバシーポリシー §2・§4 に
公開の対象として読める記述が無いので本人の画面だけに出す（第三者の応答に含めない）。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import subject_ledger as SL  # noqa: E402
import trajectory as T  # noqa: E402
import v5  # noqa: E402
from test_t63a_v5_edit_guard import STORY, _cli, _doc, _v4_person, db  # noqa: E402,F401

PV = {"schema_version": "v4", "headline": "仕組みを作る人。", "background": "背景の文。", "will_why": "根の捉え方。",
      "will_origin": "届かなかった経験。", "state_have": "試作", "state_can_type": "形にする", "state_bound": "時間",
      "state_unsorted": "", "free_text": ""}


def _doc_v5():
    d = _doc()
    d["関心"] = [{"文": "構造がつながりを阻むことに関心がある。"}]
    return d


def _person(sid="u_v5"):
    doc = _doc_v5()
    assigned = v5.assign_purposes(sid, doc, None, db_path=appmod.DB)
    doc2 = {**doc, "purposes": [{**p, "purpose_id": pid} for pid, p in assigned]}
    prof = SL.publish_profile_structured(sid, {"will_text": "w", "v5": doc2,
                                               "supporting_raw": doc["supporting_material"]}, actor=sid, db_path=appmod.DB)
    appmod._confirm_v5_ledger(sid, doc2, assigned, prof, {"payload": doc, "attempt_n": 1})
    return sid


@pytest.fixture
def pv(db, monkeypatch):
    monkeypatch.setattr(appmod, "get_profile_view", lambda sid, db_path=None: dict(PV))
    return db


def _public(sid="u_v5", viewer=None):
    c = _cli(viewer) if viewer else appmod.app.test_client()
    return c.get(f"/api/profile/{sid}").get_json()


def _owner(sid="u_v5"):
    return _cli(sid).get(f"/api/my/profile_v5?id={sid}").get_json()


TPL = lambda name: open(os.path.join(ROOT, "templates", name), encoding="utf-8").read()  # noqa: E731


# ── 287 目的2 の必要像・関心・手段が本人と第三者に出る。力になれることは本人だけ ──────────────
def test_t287_purposes_interest_means_for_both_offers_owner_only(pv):
    _person()
    for v in (_public()["v5"], _public(viewer="u_other")["v5"], _owner()):
        assert v["one_liner"] == "つなぐ"
        assert [p["dest"] for p in v["purposes"]] == ["自然に辿り着ける状態を実現したい。", "照合の論理を説明できる形にしたい。"]
        assert [p["means"] for p in v["purposes"]] == ["照合の仕組みを根づかせる。", "文単位で検証する。"]
        assert v["purposes"][1]["needs"] == [{"text": "論理の穴を指摘してきた人。", "must": False}]   # 目的2 の必要像
        assert v["interests"] == ["構造がつながりを阻むことに関心がある。"]
    assert _owner()["offers"] == ["仕組みを試作として形にする立場で関われる。"]
    assert "offers" not in _public()["v5"] and "offers" not in _public(viewer="u_other")["v5"]
    assert "仕組みを試作として形にする" not in json.dumps(_public(), ensure_ascii=False)


# ── 288 欠かせない要素の印（「必須」「歓迎」は画面に出さない） ─────────────────────────────
def test_t288_must_mark(pv):
    _person()
    assert _public()["v5"]["purposes"][0]["needs"] == [{"text": "事業を立ち上げる局面で関わってきた人。", "must": True}]
    tpl = TPL("_profile_view.html")
    body = tpl[tpl.index("function needsList("):tpl.index("function purposeBlock(")]
    assert 'n.must ? \'<span class="pv-must">欠かせない要素</span>\' : \'\'' in body
    v5part = tpl[tpl.index("function needsList("):tpl.index("/* v3 後方互換")]
    assert "必須" not in v5part and "歓迎" not in v5part
    reg = TPL("register.html")
    prev = reg[reg.index("function renderV5Preview("):reg.index("function", reg.index("function renderV5Preview(") + 10)]
    assert "欠かせない要素" in prev and '"歓迎"' not in prev and "<b>必須</b>" not in prev
    assert "<b>力になれること</b>" in prev
    traj = TPL("_trajectory.html")
    assert "欠かせない要素" in traj and '"必須" : "歓迎"' not in traj and ">力になれること<" in traj


# ── 289 閾値より前の登録では、目的ごとの必要像を第三者に出さない（本人には出す） ───────────────
def test_t289_threshold_hides_needs_from_third_parties(pv, monkeypatch):
    _person()
    monkeypatch.setenv("POX_NECESSITY_PUBLIC_SINCE", "2999-01-01T00:00:00+00:00")
    pub = _public(viewer="u_other")["v5"]
    assert [p["needs"] for p in pub["purposes"]] == [None, None]
    assert [p["dest"] for p in pub["purposes"]][1] == "照合の論理を説明できる形にしたい。"   # 向かう先・手段は意志の側
    own = _owner()
    assert [len(p["needs"]) for p in own["purposes"]] == [1, 1]
    assert [p["needs_public"] for p in own["purposes"]] == [False, False]
    monkeypatch.setenv("POX_NECESSITY_PUBLIC_SINCE", "2000-01-01T00:00:00+00:00")
    assert all(p["needs"] for p in _public()["v5"]["purposes"])
    assert all(p["needs_public"] for p in _owner()["purposes"])


# ── 290 v4 の人の表示は変わらない ────────────────────────────────────────────────
def test_t290_v4_profile_unchanged(pv):
    _v4_person(pv["store"])
    assert "v5" not in _public("u_v4")
    assert _cli("u_v4").get("/api/my/profile_v5?id=u_v4").get_json() == {}
    tpl = TPL("_profile_view.html")
    v4 = tpl[tpl.index("function bodyV4("):tpl.index("/* ══ v5（指示書63")]
    assert "足りないもの" in v4 and "求めている" in v4 and "pvNecessity" in v4
    v5part = tpl[tpl.index("function bodyV5("):tpl.index("/* v3 後方互換")]
    assert "足りないもの" not in v5part and "求めている" not in v5part
    assert "pv.v5 ? bodyV5(pv, mode) : (pv.schema_version === \"v4\") ? bodyV4(pv, mode) : bodyV3(pv, mode)" in tpl
    assert "「本人より」と「求めている」は本人の言葉です。" in tpl                    # v4 の読み方はそのまま
    assert "以下は、話した内容を AI が構造化したものです。「本人より」は本人の言葉です。" in tpl


# ── 291 第三者の応答に根拠・数値・生テキスト・generator が無い。本人には根拠が出る ───────────────
def test_t291_no_evidence_numbers_raw_for_third_parties(pv):
    _person()
    blob = json.dumps(_public(viewer="u_other"), ensure_ascii=False)
    for k in ("根拠", "evidence", "gate_s", "gate_u", "数値", "生テキスト", "generator", "GPT-6", "needs_public",
              STORY[:10], "事業を一緒に立ち上げる人がほしい", "論理の穴を指摘してくれる人も"):
        assert k not in blob, k
    own = _owner()
    assert [p["evidence"] for p in own["purposes"]] == ["事業を一緒に立ち上げる人がほしい。", "論理の穴を指摘してくれる人も。"]
    oblob = json.dumps(own, ensure_ascii=False)
    for k in ("gate_s", "gate_u", "生テキスト", "generator", STORY[:10]):
        assert k not in oblob, k
    # 本人用の API は本人だけ
    assert _cli("u_other").get("/api/my/profile_v5?id=u_v5").status_code == 403
    assert appmod.app.test_client().get("/api/my/profile_v5?id=u_v5").status_code == 401


# ── 292 軌跡でも、与え像（力になれること）は第三者に出さない ─────────────────────────────
def test_t292_trajectory_offers_not_for_third_parties():
    snap = {"snapshot_id": "s1", "necessity": {"necessity_text": "x", "purposes": [
        {"purpose_id": "pur_1", "向かう先": "A", "必要像": [{"文": "n1", "必須": True}]}],
        "与え像": [{"文": "o1"}]}}
    third = T._content(snap, "third", True)
    assert "offers" not in third and third["purposes"][0]["needs"] == [{"文": "n1", "必須": True}]
    assert T._content(snap, "partner", True)["offers"] == ["o1"]                 # 接続の相手には従来どおり
    assert T._content(snap, "owner", True)["offers"] == ["o1"]


# ── 293 並びと本人の画面（マイページは本人用の本文に差し替える） ──────────────────────────
def test_t293_order_and_owner_page():
    tpl = TPL("_profile_view.html")
    body = tpl[tpl.index("function bodyV5("):tpl.index("/* v3 後方互換")]
    order = ["背景", "意志<span", "stateBlock(pv)", "purposeBlock(p, i", "力になれること<span", "関心<span", "freeBlock("]
    pos = [body.index(x) for x in order]
    assert pos == sorted(pos)
    assert "AI が本人の語りから読み取り、本人が提供の意思を確かめたもの" in body
    assert "total > 1 ? '目的 ' + (i + 1) : '目的'" in tpl                          # 目的が1つなら番号なし
    assert "この推定の根拠として引用した、あなた自身の発言" in tpl[tpl.index("function purposeBlock("):]
    my = TPL("mypage.html")
    assert "/api/my/profile_v5" in my and "pv.v5 = own" in my
    assert my.index("/api/my/profile_v5") < my.index('PoXProfileView.render(document.getElementById("pvRoot"), pv, { mode: "owner" })')
    assert "「力になれること」が未設定のため、現状で照合しています。" in my
