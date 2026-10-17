"""指示書65-B 追補 §1（65B-7）— 必須の優先度の変更（テスト 342〜351。追補 §1-4 の 1〜10）。

本人がマイページで、目的ごとに必要像の文を「欠かせない要素／あれば嬉しい」に切り替える。変更は既存の
necessity.published の新しい版として記録する（profile.structured は書かない・正準形も型も変えない）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import ledger_events as le  # noqa: E402
import necessities as N  # noqa: E402
import v5  # noqa: E402
from embedding_config import MODEL_TAG  # noqa: E402
from test_t61_resonance_gate import _cli, db  # noqa: E402,F401
from test_t65b2_two_layer import _match, world2  # noqa: E402,F401


def _count(type_, owner="me"):
    return sum(1 for e in le.get_events(type_=type_, db_path=appmod.DB)
               if (e["payload"].get("owner_ref") or e["payload"].get("subject_id") or e["actor"]) == owner)


def _row(nid):
    with N._connect(appmod.DB) as con:
        return con.execute("SELECT body_json, gate_s, gate_u, evidence_commit, offer_hash, content_hash, canon_version, "
                           "purpose_id FROM necessities WHERE necessity_id=%s", (nid,)).fetchone()


def _save(sid, purposes):
    return _cli(sid).post("/api/my/musts", json={"purposes": purposes})


def _live(sid="me"):
    return {n["purpose_id"]: n for n in v5.live_necessities_v5(sid, db_path=appmod.DB)}


# ── 342（1）優先度だけを変えると necessity.published が 1 件増え、profile.structured は増えない ─────────────
def test_t342_only_necessity_published(world2):
    pids = list(_live())
    nec, prof = _count("necessity.published"), _count("profile.structured")
    r = _save("me", [{"purpose_id": pids[1], "musts": [True]}])
    assert r.status_code == 200 and r.get_json()["changed"] == [pids[1]]
    assert _count("necessity.published") == nec + 1 and _count("profile.structured") == prof


# ── 343（2・3）新しい版は文・型・数値・evidence_commit・offer_hash が同じで、必須だけが違い、ハッシュが変わる ──────────
def test_t343_same_body_except_must_and_hash_changes(world2):
    pids = list(_live())
    old = _live()[pids[0]]["necessity_id"]
    assert _save("me", [{"purpose_id": pids[0], "musts": [False, True]}]).status_code == 200
    new = _live()[pids[0]]["necessity_id"]
    a, b = _row(old), _row(new)
    import json
    sa, sb = json.loads(a[0]), json.loads(b[0])
    assert [(x["文"], x["型"]) for x in sa] == [(x["文"], x["型"]) for x in sb]
    assert [x["必須"] for x in sa] == [True, False] and [x["必須"] for x in sb] == [False, True]
    assert a[1:5] == b[1:5] and a[6:] == b[6:]                       # 数値・evidence_commit・offer_hash・正準形・目的
    assert a[5] != b[5]                                            # 必須の反転だけでハッシュが変わる
    assert N.verify_content_hash(new, db_path=appmod.DB)["ok"] is True
    assert N.is_priority_change(new, db_path=appmod.DB) and not N.is_priority_change(old, db_path=appmod.DB)


# ── 344（4）目的ごとに 3 つ目の必須は選べない（サーバーでも拒否する）─────────────────────────────
def test_t344_third_must_refused(world2, monkeypatch):
    pids = list(_live())
    monkeypatch.setattr(N, "MAX_MUSTS_PER_PURPOSE", 1)            # 文が 2 つの目的で「上限を越える」を作る
    r = _save("me", [{"purpose_id": pids[0], "musts": [True, True]}])
    assert r.status_code == 400 and "つまでです" in r.get_json()["error"]
    html = open(os.path.join(ROOT, "templates", "mypage.html"), encoding="utf-8").read()
    assert "目的ごとに欠かせない要素は 2 つまでです" in html


# ── 345（5）0 にできる。0 のとき上層が無く、文言が出る ──────────────────────────────────────
def test_t345_zero_musts_allowed(world2):
    pids = list(_live())
    r = _save("me", [{"purpose_id": pids[0], "musts": [False, False]}]).get_json()
    assert r["no_must"] == [pids[0]]
    d, g, _ = _match()
    assert g[pids[0]]["no_must"] is True and g[pids[0]]["results"] == []
    html = open(os.path.join(ROOT, "templates", "mypage.html"), encoding="utf-8").read()
    assert "この目的には欠かせない要素がありません。補完の照合（目的を満たす相手）は行われません" in html


# ── 346（6）ベクトルを作り直さない（埋め込みを呼ばない）。新しい版の id に紐づけ直す ──────────────────
def test_t346_vectors_relinked_not_reembedded(world2, monkeypatch):
    pids = list(_live())
    old = _live()[pids[1]]["necessity_id"]
    def boom(*a, **k):
        raise AssertionError("埋め込みを呼んではならない")
    import necessities as _nec
    monkeypatch.setattr(_nec, "_default_embed", boom)
    monkeypatch.setattr(v5, "save_sentence_vectors", boom)
    assert _save("me", [{"purpose_id": pids[1], "musts": [True]}]).status_code == 200
    new = _live()[pids[1]]["necessity_id"]
    assert v5.get_sentence_vectors("necessity", new, MODEL_TAG, db_path=appmod.DB) == \
        v5.get_sentence_vectors("necessity", old, MODEL_TAG, db_path=appmod.DB)


# ── 347（7）保存した次の照合から新しい印で判定する。申し出中の組の根拠は申し出の時点のまま ─────────────────
def test_t347_next_match_uses_new_marks(world2):
    pids = list(_live())
    _, g, _ = _match()
    assert g[pids[1]]["no_must"] is True and g[pids[1]]["results"] == []
    assert _save("me", [{"purpose_id": pids[1], "musts": [True]}]).status_code == 200
    d, g, _ = _match()
    assert [x["purpose_id"] for x in d["groups"]][:2] == pids             # 版が新しくなっても目的の並びは登録の順
    assert g[pids[1]]["no_must"] is False
    assert [r["candidate_id"] for r in g[pids[1]]["results"]] == ["c_z"]   # 目的2 をすべて覆う人として出る
    # 申し出の後に優先度を変えた側の根拠は「申し出た時点のプロフィール」
    assert _cli("me").post("/approve", json={"to_id": "c_z", "purpose_id": pids[1]}).status_code == 200
    assert _save("me", [{"purpose_id": pids[1], "musts": [False]}]).status_code == 200
    r = _cli("c_z").get("/api/connections/reason?with=me").get_json()["reason"]
    assert r and r["basis"] == "offer_time"


# ── 348（8）本人以外は変えられない（401／403）────────────────────────────────────────────
def test_t348_only_owner(world2):
    pids = list(_live())
    c = appmod.app.test_client()
    assert c.post("/api/my/musts", json={"purposes": []}).status_code == 401
    assert c.get("/api/my/musts").status_code == 401
    r = _save("c_z", [{"purpose_id": pids[1], "musts": [True]}])            # 他人の目的
    assert r.status_code == 403
    assert _cli("c_z").post("/api/my/musts", json={"id": "me", "purposes": []}).status_code == 403
    assert _live()[pids[1]]["sentences"][0]["必須"] is False


# ── 349（9）v4 の人には出ない ─────────────────────────────────────────────────────────
def test_t349_not_for_v4(db):
    assert _cli("v4_only").get("/api/my/musts").get_json()["purposes"] == []
    assert _save("v4_only", []).status_code == 400
    html = open(os.path.join(ROOT, "templates", "mypage.html"), encoding="utf-8").read()
    assert 'if (!mustData || !(mustData.purposes || []).length) { box.style.display = "none"; return; }' in html


# ── 350（10）何も変えずに保存したら版を書かない ─────────────────────────────────────────────
def test_t350_no_change_no_version(world2):
    pids = list(_live())
    nec = _count("necessity.published")
    r = _save("me", [{"purpose_id": pids[0], "musts": [True, False]}, {"purpose_id": pids[1], "musts": [False]}])
    assert r.status_code == 200 and r.get_json()["changed"] == []
    assert _count("necessity.published") == nec


# ── 351 軌跡: 「欠かせない要素を変えた」を日付と目的の名前だけで出す。第三者は必要像と同じ閾値 ───────────────
def test_t351_trajectory_shows_change(world2, monkeypatch):
    pids = list(_live())
    assert _save("me", [{"purpose_id": pids[1], "musts": [True]}]).status_code == 200
    import trajectory as T
    out = T._must_changes("me", lambda at: True, appmod.DB)
    assert [(b["kind"], b["purpose"]) for b in out] == [("must_changed", "照合の論理を説明できる形にしたい。")]
    assert set(out[0]) == {"kind", "at", "purpose"}                          # どの文かは出さない
    assert T._must_changes("me", lambda at: False, appmod.DB) == []          # 閾値より前は第三者に出ない
    html = open(os.path.join(ROOT, "templates", "_trajectory.html"), encoding="utf-8").read()
    assert "欠かせない要素を変えました（目的: ${esc(b.purpose)}）" in html
    # 作り直し（登録）の版は優先度の変更に当たらない
    assert not any(N.is_priority_change(n["necessity_id"], db_path=appmod.DB)
                   for n in v5.live_necessities_v5("c_z", db_path=appmod.DB))
