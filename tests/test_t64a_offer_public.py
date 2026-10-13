"""指示書64 PR-64A — 力になれること（与え像）の公開（テスト 310〜314）。

プライバシーポリシーの 3 か所に書き足し、与え像専用の公開の日時の閾値より後に作られた版だけを第三者に出す
（さかのぼらない）。受信箱では、申し出た本人が相手に向けて開示したものとして閾値によらず出す。判定は変えない。
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import trajectory as T  # noqa: E402
import v5  # noqa: E402
from test_t61_resonance_gate import STORY, _cli, _groups, db, world  # noqa: E402,F401
from test_t63c_profile_v5 import PV, _owner, _person, _public  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def pv(world, monkeypatch):
    """同じ照合の世界（MemoryStore）に、v5 の本人 u_v5 のプロフィールの表示（PV）を足す。"""
    monkeypatch.setattr(appmod, "get_profile_view", lambda sid, db_path=None: dict(PV))
    return world

TPL = lambda name: open(os.path.join(ROOT, "templates", name), encoding="utf-8").read()  # noqa: E731
FUTURE, PAST = "2999-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"
C_OK_OFFER = "まだ形になっていない仕組みを試作として形にする立場で関われる。"
U_V5_OFFER = "仕組みを試作として形にする立場で関われる。"

# 改定前の privacy.html（main c435d8f）の sha256。§1-1 の 3 か所と最終更新の日付以外を変えていないことの確かめに使う。
PRIVACY_BEFORE_SHA256 = "5205815cb9052e2259f13b1b0f4c11d5a5ea17468c67c590b7e0a2e6ed6a42ce"
POLICY_EDITS = [
    ("最終更新：2026年9月", "最終更新：2026年10月"),
    ("あなたが選んだ AI が生成する情報</strong>：必要像。",
     "あなたが選んだ AI が生成する情報</strong>：必要像、力になれること（AI が読み取り、あなたが提供の意思を確認したもの）。"),
    ("  <tr><td>必要像の本文</td><td>公開</td><td>表示</td></tr>\n",
     "  <tr><td>必要像の本文</td><td>公開</td><td>表示</td></tr>\n  <tr><td>力になれること</td><td>公開</td><td>表示</td></tr>\n"),
    ("あなたの意志・現状・必要像・経緯といった、", "あなたの意志・現状・必要像・力になれること・経緯といった、"),
]


# ── 310 ポリシーの 3 か所が改定後の文言（他は変わらない） ─────────────────────────────────
def test_t310_policy_three_places_only():
    now = TPL("privacy.html")
    for _, after in POLICY_EDITS:
        assert now.count(after) == 1, after
    before = now
    for old, new in POLICY_EDITS:
        before = before.replace(new, old)
    assert hashlib.sha256(before.encode("utf-8")).hexdigest() == PRIVACY_BEFORE_SHA256   # ほかは 1 字も変わらない


# ── 311 閾値より後の版: 第三者のプロフィール・カード・つながるの根拠・軌跡に出る ─────────────────────
def test_t311_after_threshold_shown_to_third_parties(pv, monkeypatch):
    monkeypatch.setenv("POX_OFFER_PUBLIC_SINCE", PAST)
    _person()
    assert _public(viewer="u_other")["v5"]["offers"] == [U_V5_OFFER]                   # プロフィール
    assert _owner()["offers_public"] is True
    snap = {"snapshot_id": "s1", "created_at": "2026-12-01T00:00:00+00:00",
            "necessity": {"necessity_text": "x", "purposes": [{"purpose_id": "p", "向かう先": "A", "必要像": []}],
                          "与え像": [{"文": "o1"}]}}
    assert T._content(snap, "third", True, offer_public=True)["offers"] == ["o1"]      # 軌跡
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert 'offer_public_for=(_offer_public if viewer_role == "third"' in src


def test_t311b_after_threshold_on_cards_and_reasons(world, monkeypatch):
    monkeypatch.setenv("POX_OFFER_PUBLIC_SINCE", PAST)
    d, _ = _groups()
    c = next(r for g in d["groups"] for r in g["results"] if r["candidate_id"] == "c_ok")
    assert C_OK_OFFER in c["offers"]                                                     # カード
    x = next(r for r in c["reasons"] if r["kind"] == "fill_mine")
    assert x["offer"] == C_OK_OFFER and "offer_hidden" not in x                         # つながるの根拠
    html = TPL("connect.html")
    assert '<span class="cf-k">力になれること</span>' in html and "offers.slice(0, 2)" in html


# ── 312 閾値より前の版: 第三者には出ず、つながるの根拠は断り書き ──────────────────────────────
def test_t312_before_threshold_hidden(pv, monkeypatch):
    monkeypatch.setenv("POX_OFFER_PUBLIC_SINCE", FUTURE)
    _person()
    assert "offers" not in _public(viewer="u_other")["v5"]
    own = _owner()
    assert own["offers"] == [U_V5_OFFER] and own["offers_public"] is False              # 本人には出す
    tpl = TPL("_profile_view.html")
    assert "この版の力になれることは、他の人には表示されません。作り直すと公開されます。" in tpl
    assert "いまは公開プロフィールには表示していません" not in tpl
    snap = {"snapshot_id": "s1", "necessity": {"necessity_text": "x", "与え像": [{"文": "o1"}],
                                               "purposes": [{"purpose_id": "p", "向かう先": "A", "必要像": []}]}}
    assert "offers" not in T._content(snap, "third", True, offer_public=False)          # 軌跡（現状どおり）


def test_t312b_before_threshold_cards_and_reasons(world, monkeypatch):
    monkeypatch.setenv("POX_OFFER_PUBLIC_SINCE", FUTURE)
    d, _ = _groups()
    c = next(r for g in d["groups"] for r in g["results"] if r["candidate_id"] == "c_ok")
    assert c["offers"] == []
    x = next(r for r in c["reasons"] if r["kind"] == "fill_mine")
    assert x["offer"] == "（公開前の登録のため表示しません）" and x["offer_hidden"] is True
    assert C_OK_OFFER not in json.dumps(d, ensure_ascii=False)
    part = TPL("_match_reason.html")
    assert "x.offer_hidden ? esc(x.offer) : quote(x.offer)" in part                    # 引用符で囲まない


# ── 313 受信箱では閾値によらず、申し出た側の与え像が出る ─────────────────────────────────────
def test_t313_inbox_shows_offerer_offer_regardless(world, monkeypatch):
    monkeypatch.setenv("POX_OFFER_PUBLIC_SINCE", FUTURE)
    pid = v5.live_necessities_v5("c_ok", db_path=appmod.DB)[0]["purpose_id"]
    assert _cli("c_ok").post("/approve", json={"to_id": "me", "purpose_id": pid}).status_code == 200
    r = _cli("me").get("/api/connections/reason?with=c_ok").get_json()["reason"]
    x = next(i for i in r["reasons"] if i["kind"] == "fill_mine")
    assert x["offer"] == C_OK_OFFER and "offer_hidden" not in x


# ── 314 第三者の応答に根拠・数値・生テキスト・evidence_span が無い（既存の担保） ─────────────────────
def test_t314_third_party_no_evidence_numbers_raw(pv, monkeypatch):
    monkeypatch.setenv("POX_OFFER_PUBLIC_SINCE", PAST)
    _person()
    blob = json.dumps(_public(viewer="u_other"), ensure_ascii=False)
    for k in ("根拠", "evidence", "gate_s", "gate_u", "数値", "生テキスト", "generator", "offers_public",
              STORY[:10], "事業を一緒に立ち上げる人がほしい"):
        assert k not in blob, k
