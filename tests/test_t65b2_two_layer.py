"""指示書65B-2 — 2層の判定と目的ごとの表（テスト 333〜341）。

上層（目的を満たす相手）＝欠かせない要素の連言。下層（この要素に応える相手）＝1 組ごとの判定。下層は計算して
保持し、表示だけをフラグで閉じる。関わっている組の根拠は、上層が不成立でも要素の対を出す（65-B §1-3）。
関心の欄は作らない（65-B 追補 §2）。関心のベクトルは登録時に保存する（表示には使わない）。
"""
import copy
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import match_config  # noqa: E402
import necessities as N  # noqa: E402
import v5  # noqa: E402
from db_v4 import MemoryStore  # noqa: E402
from embedding_config import MODEL_TAG  # noqa: E402
from matcher_v5 import direction, elements, judge, judge_direction, match_pair  # noqa: E402
from test_t61_resonance_gate import X, Y, Z, _cli, _confirm, _doc, _put, db  # noqa: E402,F401

_REAL_SENTENCE_JOB = appmod._v5_sentence_job


def _person(store, sid, doc, needs, offers, dest):
    """v5 の人。needs は目的ごとの必要像の文のベクトル、offers は与え像の文のベクトル。"""
    store.save_profile(sid, fields={"will_text": "w", "state_have": "s"}, supporting_raw={},
                       supporting_redacted={}, pii_redaction_status="none", migrated_from=None,
                       generation_status="ready")
    store.save_necessity(sid, MODEL_TAG, {"necessity_text": "x", "gate_s": 0, "gate_u": 0, "p_sharpness": 0,
                                          "alpha": 1, "beta": 1, "evidence_span": ""})
    store.save_vectors(sid, MODEL_TAG, {"will_symmetric": dest, "will_passage": dest, "state_passage": Z,
                                        "necessity_query": needs[0][0]})
    pids = _confirm(sid, doc)
    for n, vecs in zip(v5.live_necessities_v5(sid, db_path=appmod.DB), needs):
        _put("necessity", n["necessity_id"], vecs, texts=[x["文"] for x in n["sentences"]])
    off = v5.latest_offer(sid, db_path=appmod.DB)
    _put("offer", off["offer_id"], offers, texts=[x["文"] for x in off["sentences"]])
    for pid in pids:
        _put("dest", pid, [dest], texts=["向かう先"])
    return pids


def _no_must_doc():
    d = copy.deepcopy(_doc())
    d["purposes"][1]["必要像"][0]["必須"] = False                     # 目的2 は欠かせない要素なし
    return d


@pytest.fixture
def world2(db, monkeypatch):
    """me: 目的1＝欠かせない X・歓迎 Y（門あり・向かう先 X）／目的2＝欠かせない要素なし・Z。与え像 Z。
    c_full: 与え像 X・Y（目的1 をすべて覆う）／c_part: 与え像 Y だけ（欠かせない X に届かない）／
    c_z: 与え像 Z（目的2 の要素にだけ応える）。どれも向かう先 X（門を通る）。"""
    store = MemoryStore()
    _person(store, "me", _no_must_doc(), needs=[[X, Y], [Z]], offers=[Z, Z], dest=X)
    other = [[[0.0, 0.0, -1.0]] * 2, [[0.0, 0.0, -1.0]]]
    _person(store, "c_full", _doc(), needs=other, offers=[X, Y], dest=X)
    _person(store, "c_part", _doc(), needs=other, offers=[Y, Y], dest=X)
    _person(store, "c_z", _doc(), needs=other, offers=[Z, Z], dest=X)
    monkeypatch.setattr(appmod, "_v4_store", lambda: store)
    monkeypatch.setattr(appmod, "_matching_available", lambda: True)
    monkeypatch.setattr(appmod, "_linked_ids", lambda ids: set(ids))
    import migrate_v4
    monkeypatch.setattr(migrate_v4, "ensure_migrated", lambda *a, **k: None)
    return store


def _match(sid="me"):
    d = _cli(sid).post("/v4/match", json={}).get_json()
    pids = [n["purpose_id"] for n in v5.live_necessities_v5(sid, db_path=appmod.DB)]
    return d, {g["purpose_id"]: g for g in d["groups"]}, pids


# ── 333 c4 の内容ハッシュ: 必須の印を反転するとハッシュが変わる（65-B §9-1）──────────────────────
def test_t333_c4_hash_changes_when_must_flips():
    nums = {"gate_s": 0.6, "gate_u": 0.3}
    base = [{"文": "文その一。", "必須": True, "型": "関わり方"}, {"文": "文その二。", "必須": False, "型": "資源"}]
    flip = [dict(base[0], 必須=False), base[1]]
    h = lambda s: N.compute_content_hash("", nums, "commit", canon_version=N.C4, sentences=s, offer_hash="o")  # noqa: E731
    assert h(base) != h(flip)
    assert h(base) == h(copy.deepcopy(base))                      # 同じ印なら同じハッシュ


# ── 334 2層: 届かない必須があっても要素の対が残る。上層は不成立 ─────────────────────────────
def test_t334_elements_survive_failed_must():
    needs = [{"text": "必須", "vec": X, "required": True}, {"text": "歓迎", "vec": Y, "required": False}]
    offers = [{"text": "o_y", "vec": Y}]
    assert direction(needs, offers) == (False, [])                # 上層は不成立（以前は対も全部捨てた）
    rows = elements({"purpose_id": "p", "needs": needs}, {"offers": offers})
    assert rows == [{"text": "必須", "must": True, "offers": []}, {"text": "歓迎", "must": False, "offers": ["o_y"]}]
    r = match_pair({"purposes": [{"purpose_id": "p", "needs": needs, "dest_vec": X, "gate_s": 0, "gate_u": 0}],
                    "offers": [], "dests": [X]}, {"purposes": [], "offers": offers, "dests": [X]})
    assert r["by_purpose"] == {} and r["elements"]["p"][1]["offers"] == ["o_y"]


# ── 335 欠かせない要素が無い目的には上層が無い（v4 の全文は欠かせない要素とみなす）──────────────────
def test_t335_no_must_purpose_has_no_upper(world2):
    v5_p = {"purpose_id": "p", "needs": [{"text": "歓迎", "vec": Z, "required": False}], "gate_s": 0, "gate_u": 0}
    v4_p = {"purpose_id": None, "needs": [{"text": "全文", "vec": Z, "required": False}], "gate_s": 0, "gate_u": 0}
    other = {"offers": [{"text": "o", "vec": Z}], "dests": []}
    assert judge_direction(v5_p, other)[0] is False and judge_direction(v4_p, other)[0] is True
    d, g, pids = _match()
    assert g[pids[1]]["no_must"] is True and g[pids[1]]["results"] == []   # c_z は目的2 の要素に応えるが上層は無い
    assert g[pids[0]]["no_must"] is False
    html = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert "この目的には欠かせない要素がありません" in html


# ── 336 目的ごとの表: 結果の無い目的も出る・上層の印・目的全体の文言・並びは中立（id 順）───────────────
def test_t336_table_per_purpose_neutral_order(world2):
    d, g, pids = _match()
    assert [x["purpose_id"] for x in d["groups"]][:2] == pids         # 結果の無い目的も表に出る
    assert [r["candidate_id"] for r in g[pids[0]]["results"]] == ["c_full"]   # c_part は欠かせない X に届かない
    html = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    for t in ("欠かせない要素をすべて覆う人", "いまは、この目的をすべて満たす相手はいません", "この要素に応える人が見つかりませんでした"):
        assert t in html, t
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "# id 順（中立。g の順にしない）" in src and "ids = sorted(" in src


# ── 337 下層の表示のフラグ: 既定で閉じる。閉じている間は要素の行を返さない（表は上層と目的全体の文言だけ）──────
def test_t337_lower_layer_flag_closed_by_default(world2, monkeypatch):
    monkeypatch.delenv("POX_LOWER_LAYER_OPEN", raising=False)
    assert match_config.lower_layer_open() is False
    d, g, pids = _match()
    assert all("elements" not in x for x in d["groups"])
    html = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert "if (!Array.isArray(g.elements)) return \"\";" in html          # サーバーが返さなければ行を出さない


# ── 338 フラグを開けると、要素ごとに応える人（id 順）と「見つかりませんでした」の行が出る ──────────────────
def test_t338_lower_layer_rows_when_open(world2, monkeypatch):
    monkeypatch.setenv("POX_LOWER_LAYER_OPEN", "1")
    d, g, pids = _match()
    rows = g[pids[0]]["elements"]
    assert [r["must"] for r in rows] == [True, False]
    assert [p["candidate_id"] for p in rows[0]["people"]] == ["c_full"]
    assert [p["candidate_id"] for p in rows[1]["people"]] == ["c_full", "c_part"]   # 上層が不成立でも残る・id 順
    assert [p["candidate_id"] for p in g[pids[1]]["elements"][0]["people"]] == ["c_z"]
    for x in rows:
        for p in x["people"]:
            assert set(p) == {"candidate_id", "name", "handle"}            # 数値を出さない


# ── 339 関わっている組は、上層が不成立でも要素の対が根拠に出る。関わっていない組・見送りには出ない ─────────────
def test_t339_engaged_pairs_get_element_reasons(world2, monkeypatch):
    monkeypatch.delenv("POX_LOWER_LAYER_OPEN", raising=False)          # 下層の表示が閉じていても出す
    assert _cli("me").get("/api/connections/reason?with=c_part").status_code == 403      # 関わっていない
    assert _cli("c_part").post("/approve", json={"to_id": "me"}).status_code == 200      # 相手から申し出（承認待ち）
    r = _cli("me").get("/api/connections/reason?with=c_part").get_json()["reason"]
    assert r["axis"] == "element" and r["reasons"]
    mine = [x for x in r["reasons"] if x["kind"] == "fill_mine"]
    assert [x["need"] for x in mine] == ["事業構造をロジカルに組み立てる立場で関わる人。"]   # 歓迎の要素の対
    assert all("g" not in x and "score" not in x for x in r["reasons"])
    assert _cli("me").post("/api/connections/decline", json={"to_id": "c_part"}).status_code == 200
    assert _cli("me").get("/api/connections/reason?with=c_part").status_code == 403      # 見送りには出ない
    html = open(os.path.join(ROOT, "templates", "_match_reason.html"), encoding="utf-8").read()
    assert "要素で応える" in html


# ── 340 上層の作動点は設定値（後から差し替えられる）─────────────────────────────────────────
def test_t340_upper_operating_point_is_a_setting(monkeypatch):
    need, offer = [1.0, 0.0, 0.0], [0.7, 0.714142842854285, 0.0]      # cos 0.7 → g 0.85
    assert judge(need, offer) is True
    monkeypatch.setitem(match_config.UPPER_OPERATING_POINT, "threshold", 0.9)
    assert judge(need, offer) is False
    monkeypatch.setitem(match_config.UPPER_OPERATING_POINT, "form", "per_need_q99")
    with pytest.raises(ValueError):
        judge(need, offer)
    assert match_config.LOWER_OPERATING_POINT == {"form": "fixed", "threshold": 0.70}


# ── 341 関心のベクトルを登録時に保存する（照合にも表示にも使わない）─────────────────────────────
def test_t341_interest_vectors_saved_at_registration(db):
    _confirm("u1", _doc())
    _REAL_SENTENCE_JOB("u1", v5.latest_offer("u1", db_path=appmod.DB))
    want = ["生まれた環境の構造が自由なつながりを阻むことに関心がある。"]
    for kind in ("interest", "interest_doc"):
        assert [t for t, _ in v5.get_sentence_vectors(kind, "u1", MODEL_TAG, db_path=appmod.DB)] == want
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "interest" not in src.split("def _side_of")[1].split("def _reason_items")[0]   # 照合では使わない
