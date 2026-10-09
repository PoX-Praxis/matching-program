"""指示書63 段階1 PR-E — つながるのカード・登録者一覧のラベル（テスト 304〜309）。

カード（照合の結果・登録者一覧とも）: 名前（@ハンドル）／一行紹介（v5）・要約文（v4）／目指していること（目的ごと。
v4 は意志）／必要としている相手（欠かせない要素の文を先に、最大 2 文・残りは全文で）。力になれること（与え像）は
プライバシーポリシーの確認（PR-C）により第三者に出さない。必要像はプロフィールと同じ公開の日時の閾値に従う。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import v5  # noqa: E402
from test_t61_resonance_gate import STORY, _cli, _groups, db, world  # noqa: E402,F401

TPL = lambda name: open(os.path.join(ROOT, "templates", name), encoding="utf-8").read()  # noqa: E731
DESTS = ["自然に辿り着ける状態を実現したい。", "照合の論理を説明できる形にしたい。"]


def _card(d, cid):
    return next(r for g in d["groups"] for r in g["results"] if r["candidate_id"] == cid)


def _seekers(monkeypatch, ids):
    monkeypatch.setattr(appmod, "list_directory_ids", lambda db_path=None: list(ids))
    monkeypatch.setattr(appmod, "get_profile_view",
                        lambda i, db_path=None: {"headline": f"{i}の要約文", "pursuing": f"{i}の意志"})
    return {r["id"]: r for r in appmod.app.test_client().get("/seekers").get_json()}


# ── 304 照合の結果のカード: 目的ごとの向かう先・必要像（欠かせない要素）・「あなたの目的 N に」 ─────────
def test_t304_match_card_rows_and_for_purpose(world):
    d, _ = _groups()
    me_pids = [n["purpose_id"] for n in v5.live_necessities_v5("me", db_path=appmod.DB)]
    g1 = next(g for g in d["groups"] if g["purpose_id"] == me_pids[0])
    assert g1["purpose_label"] == "あなたの目的 1"
    c = _card(d, "c_ok")
    assert c["for_purpose"] == "あなたの目的 1に" and c["one_liner"] == "つなぐ人"          # v5 は一行紹介
    assert [p["dest"] for p in c["purposes"]] == DESTS
    assert c["purposes"][0]["needs"] == [
        {"text": "事業を立ち上げる局面で、構想を実行に移す側として関わってきた人。", "must": True},
        {"text": "事業構造をロジカルに組み立てる立場で関わる人。", "must": False}]


# ── 305 カードに力になれること（与え像）・根拠・数値・生テキストを出さない ───────────────────────
def test_t305_no_offers_evidence_numbers_on_cards(world, monkeypatch):
    d, _ = _groups()
    c = _card(d, "c_ok")
    prof = json.dumps({"one_liner": c["one_liner"], "purposes": c["purposes"]}, ensure_ascii=False)
    for k in ("与え像", "offers", "試作として形にする立場で関われる", "根拠", "gate", "生テキスト", STORY[:10]):
        assert k not in prof, k
    rows = json.dumps(_seekers(monkeypatch, ["c_ok"]), ensure_ascii=False)
    for k in ("与え像", "offers", "試作として形にする立場で関われる", "根拠", "evidence", "gate_s", "生テキスト",
              "generator", STORY[:10]):
        assert k not in rows, k
    html = TPL("connect.html")
    body = html[html.index("function profileRows("):html.index("/* ── 照合の結果（v4 照合）")]
    assert "offers" not in body and "力になれること" not in body


# ── 306 必要像はプロフィールと同じ公開の日時の閾値に従う（一覧・カードとも） ───────────────────────
def test_t306_threshold_on_cards_and_directory(world, monkeypatch):
    monkeypatch.setenv("POX_NECESSITY_PUBLIC_SINCE", "2999-01-01T00:00:00+00:00")
    r = _seekers(monkeypatch, ["c_ok"])["c_ok"]
    assert [p["dest"] for p in r["purposes"]] == DESTS and all(p["needs"] == [] for p in r["purposes"])
    monkeypatch.setenv("POX_NECESSITY_PUBLIC_SINCE", "2000-01-01T00:00:00+00:00")
    r = _seekers(monkeypatch, ["c_ok"])["c_ok"]
    assert all(p["needs"] for p in r["purposes"])


# ── 307 一覧（/seekers）: v5 は一行紹介と目的ごと、v4 は要約文と意志・公開の必要像 ──────────────────
def test_t307_directory_rows(world, monkeypatch):
    monkeypatch.setattr(appmod, "get_public_necessity",
                        lambda i: {"necessity_text": "実装を一緒に進める人。"} if i == "c_v4ok" else None)
    rows = _seekers(monkeypatch, ["c_ok", "c_v4ok"])
    assert rows["c_ok"]["one_liner"] == "つなぐ人" and len(rows["c_ok"]["purposes"]) == 2
    assert rows["c_v4ok"]["one_liner"] == "c_v4okの要約文"
    assert rows["c_v4ok"]["purposes"] == [{"dest": "c_v4okの意志",
                                           "needs": [{"text": "実装を一緒に進める人。", "must": False}]}]


# ── 308 すべての段にラベル・欠かせない要素を先に最大 2 文・常設の注記 ─────────────────────────────
def test_t308_labels_order_and_note():
    html = TPL("connect.html")
    body = html[html.index("const MUST_TAG"):html.index("/* ── 照合の結果（v4 照合）")]
    assert '<span class="cf-k">目指していること</span>' in body
    assert '<span class="cf-k">必要としている相手</span>' in body
    assert "(b.must ? 1 : 0) - (a.must ? 1 : 0)" in body and "xs.slice(0, 2)" in body and "全文を見る" in body
    assert "欠かせない要素" in body
    assert "dir-nec" not in html and "rec-nec" not in html                     # 色だけで区別しない
    assert '<span class="cf-k">なぜこの人か</span>' in html and "r.for_purpose" in html
    assert "申し出が届いている相手は、ここではなく" in html
    rec = html[html.index("async function loadRecommendations("):html.index("if (!sid) {", html.index("async function loadRecommendations("))]
    assert 'getElementById("inboxNote").style.display = sid ? "" : "none"' in rec   # 0 件の画面だけでなく常に
    assert "${profileRows(r)}" in html[html.index("function renderDirectory("):]


# ── 309 v4 の相手のカード: 意志・公開の必要像（力になれることの節は無い） ──────────────────────────
def test_t309_v4_card(world, monkeypatch):
    monkeypatch.setattr(appmod, "get_profile_view",
                        lambda sid, db_path=None: {"headline": "要約文の一行。", "pursuing": "意志の文。"})
    monkeypatch.setattr(appmod, "get_public_necessity", lambda i: {"necessity_text": "必要像の全文。二文目。"})
    d, _ = _groups()
    c = _card(d, "c_v4ok")
    assert c["one_liner"] == "要約文の一行。"
    assert c["purposes"] == [{"dest": "意志の文。", "needs": [{"text": "必要像の全文。二文目。", "must": False}]}]
