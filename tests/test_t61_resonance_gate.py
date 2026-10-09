"""指示書61 — 共鳴の門・根拠の表示・生成元の記録・プロンプト改訂3（テスト 243〜267）。"""
import html as htmlmod
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import ledger_events as le  # noqa: E402
import necessities as N  # noqa: E402
import subject_ledger as SL  # noqa: E402
import v5  # noqa: E402
from db_v4 import MemoryStore  # noqa: E402
from embedding_config import MODEL_TAG  # noqa: E402
from matcher_v5 import judge_direction, match_pair  # noqa: E402

STORY = "私は仕組みを作るのが好きです。事業を一緒に立ち上げる人がほしい。論理の穴を指摘してくれる人も。"
X, Y, Z = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]


def _doc(**over):
    d = {
        "id": "kaoru_2026", "schema_version": "v5", "generator": "Claude Opus 4.8",
        "_meta": {"source": "v5r3-A"},
        "purposes": [
            {"purpose_id": "p1", "向かう先": "自然に辿り着ける状態を実現したい。", "手段": "照合の仕組みを根づかせる。",
             "必要像": [{"文": "事業を立ち上げる局面で、構想を実行に移す側として関わってきた人。", "必須": True, "型": "関わり方"},
                      {"文": "事業構造をロジカルに組み立てる立場で関わる人。", "必須": False, "型": "資源"}],
             "数値": {"gate_s": 0.9, "gate_u": 0.3}, "根拠": "事業を一緒に立ち上げる人がほしい。"},
            {"purpose_id": "p2", "向かう先": "照合の論理を説明できる形にしたい。", "手段": "文単位で検証できるようにする。",
             "必要像": [{"文": "論理の穴を指摘し、構造として組み直す関わり方をしてきた人。", "必須": False, "型": "資源"}],
             "数値": {"gate_s": 0.0, "gate_u": 0.3}, "根拠": "論理の穴を指摘してくれる人も。"}],
        "与え像": [{"文": "まだ形になっていない仕組みを試作として形にする立場で関われる。", "型": "関わり方"},
                 {"文": "照合の仕組みと、そこに集まる人と機会という場を提供できる。", "型": "資源"}],
        "関心": [{"文": "生まれた環境の構造が自由なつながりを阻むことに関心がある。"}],
        "現状": {"持っているもの": "試作", "できること_型": "形にする", "縛られているもの": "", "未分類": ""},
        "supporting_material": {"一行紹介": "つなぐ人", "意志_なぜ": "構造がつながりを阻むべきではない。",
                                "要約文": "仕組みを作る人。", "経験": "辿り着けなかった。", "生テキスト": [STORY]},
    }
    d.update(over)
    return d


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.setattr(appmod, "_v5_sentence_job", lambda *a, **k: None)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)
    monkeypatch.setattr(appmod, "_ingest_v4_from_flat",
                        lambda flat, profile_id=None, publish_ledger=True:
                        (profile_id, {"necessity_text": flat.get("necessity_text")}, False))
    monkeypatch.setattr(appmod, "_conn_ref_resolver",
                        lambda s, purpose_id=None: {"profile_snapshot_hash": f"h_{s}", "necessity_hash": None})
    return appmod.DB


def _cli(sid):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["subject_id"] = sid
    return c


def _confirm(sid, doc):
    assigned = v5.assign_purposes(sid, doc, None, db_path=appmod.DB)
    doc2 = {**doc, "purposes": [{**p, "purpose_id": pid} for pid, p in assigned]}
    prof = SL.publish_profile_structured(sid, {"will_text": "w", "v5": doc2,
                                               "supporting_raw": doc.get("supporting_material")},
                                         actor=sid, db_path=appmod.DB)
    appmod._confirm_v5_ledger(sid, doc2, assigned, prof, {"payload": doc, "attempt_n": 1})
    return [pid for pid, _ in assigned]


def _put(kind, ref, vecs, texts=None):
    texts = texts or [t for t, _ in v5.get_sentence_vectors(kind, ref, MODEL_TAG, db_path=appmod.DB)] \
        or [f"{ref}{i}" for i in range(len(vecs))]
    v5.save_sentence_vectors(kind, ref, texts[:len(vecs)], MODEL_TAG, lambda t, r, it=iter(vecs): next(it),
                             db_path=appmod.DB)


def _v5_person(store, sid, *, needs1, needs2, offers, dest, will=None):
    """v5 の人（2 目的）。必要像・与え像・向かう先のベクトルを合成で置く。"""
    store.save_profile(sid, fields={"will_text": "w", "state_have": "s"}, supporting_raw={},
                       supporting_redacted={}, pii_redaction_status="none", migrated_from=None,
                       generation_status="ready")
    store.save_necessity(sid, MODEL_TAG, {"necessity_text": "x", "gate_s": 0, "gate_u": 0, "p_sharpness": 0,
                                          "alpha": 1, "beta": 1, "evidence_span": ""})
    w = will or dest
    store.save_vectors(sid, MODEL_TAG, {"will_symmetric": w, "will_passage": w, "state_passage": Z,
                                        "necessity_query": needs1[0]})
    pids = _confirm(sid, _doc())
    nec = v5.live_necessities_v5(sid, db_path=appmod.DB)
    for n, vecs in ((nec[0], needs1), (nec[1], needs2)):
        _put("necessity", n["necessity_id"], vecs, texts=[x["文"] for x in n["sentences"]])
    off = v5.latest_offer(sid, db_path=appmod.DB)
    _put("offer", off["offer_id"], offers, texts=[x["文"] for x in off["sentences"]])
    for pid in pids:
        _put("dest", pid, [dest], texts=["向かう先"])
    return pids


def _v4_person(store, sid, *, will, state, nec_vec=None, gate_s=0.0, gate_u=0.0):
    store.save_profile(sid, fields={"will_text": "w", "state_have": "s"}, supporting_raw={},
                       supporting_redacted={}, pii_redaction_status="none", migrated_from=None,
                       generation_status="ready")
    store.save_necessity(sid, MODEL_TAG, {"necessity_text": f"{sid}の必要像", "gate_s": gate_s, "gate_u": gate_u,
                                          "p_sharpness": 0, "alpha": 1, "beta": 1, "evidence_span": ""})
    store.save_vectors(sid, MODEL_TAG, {"will_symmetric": will, "will_passage": will, "state_passage": state,
                                        "necessity_query": nec_vec or Y})


@pytest.fixture
def world(db, monkeypatch):
    """me（v5。p1 は門あり＝gate_s 0.9・gate_u 0.3・必要像 X／p2 は門なし・必要像 Y／与え像 Z／向かう先 X）と、
    c_ok（与え像 X・向かう先 X）・c_gate（与え像 X・向かう先 Z＝共鳴が低い）・c_v4ok（意志 X・現状 X）・
    c_v4no（意志 Z・現状 X）・c_low（与え像 Y・向かう先 Z＝p2 は門なしで補完だけ）。"""
    store = MemoryStore()
    _v5_person(store, "me", needs1=[X, X], needs2=[Y], offers=[Z, Z], dest=X)
    _v5_person(store, "c_ok", needs1=[[0.0, 0.0, -1.0]] * 2, needs2=[[0.0, 0.0, -1.0]], offers=[X, X], dest=X)
    _v5_person(store, "c_gate", needs1=[[0.0, 0.0, -1.0]] * 2, needs2=[[0.0, 0.0, -1.0]], offers=[X, X], dest=Z)
    _v5_person(store, "c_low", needs1=[[0.0, 0.0, -1.0]] * 2, needs2=[[0.0, 0.0, -1.0]], offers=[Y, Y], dest=Z)
    _v4_person(store, "c_v4ok", will=X, state=X)
    _v4_person(store, "c_v4no", will=Z, state=X)
    monkeypatch.setattr(appmod, "_v4_store", lambda: store)
    monkeypatch.setattr(appmod, "_matching_available", lambda: True)
    monkeypatch.setattr(appmod, "_linked_ids", lambda ids: set(ids))
    import migrate_v4
    monkeypatch.setattr(migrate_v4, "ensure_migrated", lambda *a, **k: None)
    return store


def _groups(sid="me"):
    d = _cli(sid).post("/v4/match", json={}).get_json()
    return d, {g["purpose_id"]: [r["candidate_id"] for r in g["results"]] for g in d["groups"]}


def _side(needs=None, offers=None, dest=None, dests=None, gs=0.0, gu=0.0, pid="p"):
    return {"purposes": [{"purpose_id": pid, "needs": [{"text": "n", "vec": v, "required": True} for v in (needs or [])],
                          "dest_vec": dest, "gate_s": gs, "gate_u": gu}] if needs else [],
            "offers": [{"text": "o", "vec": v} for v in (offers or [])], "dests": dests or ([dest] if dest else [])}


# ── 243〜249 共鳴の門 ─────────────────────────────────────────────────────────────
def test_t243_gate_blocks_low_resonance(world):
    _, by = _groups()
    p1 = v5.live_necessities_v5("me", db_path=appmod.DB)[0]["purpose_id"]
    assert "c_ok" in by[p1] and "c_gate" not in by[p1]          # gate_s 0.9・gate_u 0.3＝門あり


def test_t244_no_gate_when_gate_s_zero(world):
    _, by = _groups()
    p2 = v5.live_necessities_v5("me", db_path=appmod.DB)[1]["purpose_id"]
    assert "c_low" in by[p2]                                      # 共鳴は低いが、門が無いので補完で出る


def test_t245_no_gate_when_uncertain():
    mine = _side(needs=[X], dest=X, gs=0.9, gu=0.6)               # 門の強さ 0.36 < 0.5
    theirs = _side(offers=[X], dests=[Z])
    ok, _, info = judge_direction(mine["purposes"][0], theirs)
    assert ok and info["gate"] is False


def test_t246_direction_b_uses_their_purpose_numbers():
    mine = _side(needs=[Z], offers=[X], dest=X, gs=0.9, gu=0.0)   # 自分の目的は門あり（B には効かない）
    theirs_gate = _side(needs=[X], dest=Z, gs=0.9, gu=0.0)        # 相手の目的が門あり・共鳴が低い
    theirs_open = _side(needs=[X], dest=Z, gs=0.0, gu=0.0)        # 相手の目的が門なし
    assert match_pair(mine, theirs_gate)["theirs_need_me"] is None
    assert match_pair(mine, theirs_open)["theirs_need_me"] is not None


def test_t247_resonance_not_mixed_into_score():
    mine = _side(needs=[X], dest=X, gs=0.9, gu=0.0)
    hi = judge_direction(mine["purposes"][0], _side(offers=[X], dests=[X]))
    mid = judge_direction(mine["purposes"][0], _side(offers=[X], dests=[[0.8, 0.6, 0.0]]))   # g=0.9 でも門は通る
    assert hi[2]["resonance"] != mid[2]["resonance"]
    assert hi[:2] == mid[:2]                                      # 判定と引用の対は同じ（点数に混ざらない）


def test_t248_gate_dropped_reason_not_shown_or_recorded(world):
    before = len(le.get_events(db_path=appmod.DB))
    d, _ = _groups()
    blob = json.dumps(d, ensure_ascii=False)
    assert "c_gate" not in blob                                    # 出さない
    for k in ("resonance", "共鳴", "gate", "門"):
        assert k not in blob, k                                    # 理由を出さない
    assert len(le.get_events(db_path=appmod.DB)) == before         # 記録しない
    assert world.ledger == [] if hasattr(world, "ledger") else True


def test_t249_v4_person_uses_will_as_destination(world):
    _, by = _groups()
    p1 = v5.live_necessities_v5("me", db_path=appmod.DB)[0]["purpose_id"]
    assert "c_v4ok" in by[p1] and "c_v4no" not in by[p1]          # 意志の全文（will_symmetric）で共鳴を測る


# ── 250 監査ルートにだけ値が出る ─────────────────────────────────────────────────────
def test_t250_audit_route_has_gate_values_public_does_not(world, monkeypatch):
    monkeypatch.setenv("POX_ANCHOR_TOKEN", "tok")
    monkeypatch.setattr(appmod, "_seeker_live_necessity_id", lambda sid, db_path=None: None)
    r = appmod.app.test_client().get("/ledger/audit/match?pair=me,c_gate", headers={"X-Anchor-Token": "tok"})
    d = r.get_json()["v5"]
    p1 = d["a_to_b"][0]
    assert set(p1) >= {"purpose_id", "resonance", "gate_strength", "gate", "gate_passed", "complement"}
    assert p1["gate"] is True and p1["gate_passed"] is False and p1["complement"] is None
    assert "事業" not in json.dumps(d, ensure_ascii=False)          # 本文は返さない
    pub = json.dumps(_cli("me").post("/v4/match", json={}).get_json(), ensure_ascii=False)
    assert "resonance" not in pub and "gate_strength" not in pub


# ── 251〜255 根拠の表示 ───────────────────────────────────────────────────────────
def test_t251_card_shows_need_to_offer_pair(world):
    d, _ = _groups()
    card = next(r for g in d["groups"] for r in g["results"] if r["candidate_id"] == "c_ok")
    x = card["reasons"][0]
    assert x["need_label"] == "あなたが必要としていること" and x["offer_label"] == "相手が力になれること"
    assert x["need"].startswith("事業を立ち上げる局面")              # 必須の文の対が先


def test_t252_card_never_pairs_needs_with_needs(world):
    d, _ = _groups()
    for g in d["groups"]:
        for r in g["results"]:
            for x in r["reasons"]:
                assert "必要" not in x["offer_label"] and "求めて" not in x["offer_label"]
    part = open(os.path.join(ROOT, "templates", "_match_reason.html"), encoding="utf-8").read()
    assert "mr-arrow" in part and "全文を見る" in part


def test_t253_approval_shows_direction_b_first(world):
    # c_mut: 与え像 X（me の p1 に応える）・p1 の必要像 Z（me の与え像 Z が応える）→ 互いに埋める
    _v5_person(world, "c_mut", needs1=[Z, Z], needs2=[[0.0, -1.0, 0.0]], offers=[X, X], dest=X)
    pid = v5.live_necessities_v5("c_mut", db_path=appmod.DB)[0]["purpose_id"]
    assert _cli("c_mut").post("/approve", json={"to_id": "me", "purpose_id": pid}).status_code == 200
    r = _cli("me").get("/api/connections/reason?with=c_mut").get_json()["reason"]
    assert r["axis"] == "mutual"
    first, second = r["reasons"]
    assert first["kind"] == "fill_theirs" and first["need_label"] == "相手があなたに求めていること"
    assert first["offer_label"] == "あなたが力になれること" and first["takes_on"] is True
    assert second["need_label"] == "あなたが求めていること"
    for t in ("inbox.html", "mypage.html", "profile.html"):      # 申し出の文が根拠の上
        src = open(os.path.join(ROOT, "templates", t), encoding="utf-8").read()
        if t == "profile.html":
            assert "PoXReason.offerHtml(offers[profileId]) + PoXReason.reasonHtml(reason)" in src
        else:
            assert src.index("offer") < src.index('class="mr-slot"') or "mr-offer-slot" in src


def test_t254_v4_counterpart_shows_matching_state_slot(world, monkeypatch):
    pvs = {"c_v4ok": {"state_have": "試作の場を持っている", "state_can_type": "人をつなぐ"}}
    monkeypatch.setattr(appmod, "get_profile_view", lambda sid, db_path=None: pvs.get(sid))
    v5.save_state_slot_vectors("c_v4ok", ["試作の場を持っている", "人をつなぐ", "", ""], MODEL_TAG,
                               lambda t, r: X if t.startswith("試作") else Y, db_path=appmod.DB)
    d, _ = _groups()
    card = next(r for g in d["groups"] for r in g["results"] if r["candidate_id"] == "c_v4ok")
    x = card["reasons"][0]
    assert x["offer_label"] == "相手の現状（持っているもの）" and x["offer"] == "試作の場を持っている"


def test_t255_no_similarity_values_on_screen(world):
    d, _ = _groups()
    blob = json.dumps(d, ensure_ascii=False)
    for k in ("score", "cos", "resonance", "gate_s", "0.7"):
        assert k not in blob, k
    for t in ("_match_reason.html", "connect.html", "inbox.html", "mypage.html", "profile.html"):
        src = open(os.path.join(ROOT, "templates", t), encoding="utf-8").read()
        assert "resonance" not in src and "gate_strength" not in src


# ── 256〜262 受信の検証・生成元 ─────────────────────────────────────────────────────
def _draft(sid, doc):
    return _cli(sid).post("/v4/drafts", json={"raw_text": json.dumps(doc, ensure_ascii=False)})


def test_t256_revision3_json_accepted(db):
    r = _draft("u_a", _doc())
    assert r.status_code == 201
    from necessity_gen import build_user_necessity                  # v4 互換の受付も alpha・beta なしで通る
    flat = v5.to_flat(_doc())
    assert build_user_necessity({**flat, "supporting_redacted": {}}, flat)["necessity_text"]


def test_t257_revision2_json_accepted_alpha_beta_dropped(db):
    d = _doc()
    d.pop("_meta")
    for p in d["purposes"]:
        p["数値"].update(alpha=1.2, beta=0.8)
    r = _draft("u_a", d)
    assert r.status_code == 201
    blob = json.dumps(r.get_json()["payload"], ensure_ascii=False)
    assert "alpha" not in blob and "beta" not in blob
    assert v5.source_of(d) == "v5r2"


def test_t258_empty_generator_rejected_one_line(db):
    r = _draft("u_a", _doc(generator=""))
    msg = r.get_json()["error"]
    assert r.status_code == 400 and msg == "generator（AI の名前）が入っていません" and "\n" not in msg


def test_t259_slash_joined_evidence_accepted(db):
    d = _doc()
    d["purposes"][0]["根拠"] = "仕組みを作るのが好きです／事業を一緒に立ち上げる人がほしい"
    assert _draft("u_a", d).status_code == 201
    d["purposes"][0]["根拠"] = "仕組みを作るのが好きです/論理の穴を指摘してくれる人も"
    assert _draft("u_a", d).status_code == 201


def test_t260_slash_part_missing_rejected(db):
    d = _doc()
    d["purposes"][0]["根拠"] = "仕組みを作るのが好きです／語りに無い一文"
    r = _draft("u_a", d)
    assert r.status_code == 400 and "が生テキストに見つかりません" in r.get_json()["error"]


def test_t261_slash_evidence_saved_verbatim(db):
    d = _doc()
    raw = "仕組みを作るのが好きです／事業を一緒に立ち上げる人がほしい"
    d["purposes"][0]["根拠"] = raw
    r = _draft("u_a", d)
    assert r.get_json()["payload"]["purposes"][0]["根拠"] == raw
    did = r.get_json()["draft_id"]
    _cli("u_a").post(f"/v4/drafts/{did}/confirm", json={"handle": "kaoru_2026"})
    assert v5.get_doc("u_a", db_path=appmod.DB)["purposes"][0]["根拠"] == raw


def test_t262_generator_recorded_as_family_and_tag(db):
    _confirm("u_a", _doc())
    ev = le.get_events(type_="necessity.published", db_path=appmod.DB)[-1]["payload"]
    assert ev["generator"] == "claude-opus" and ev["generator_tag"] == "v5r3-A/claude-opus"
    d = _doc()
    d.pop("_meta")
    d["generator"] = "ChatGPT-5"
    d["purposes"][0]["必要像"][0]["文"] = "別の必要像の文。"
    _confirm("u_b", d)
    ev = le.get_events(type_="necessity.published", db_path=appmod.DB)[-2]["payload"]
    assert ev["generator_tag"] == "v5r2/gpt"


# ── 263〜265 正準化の版 ──────────────────────────────────────────────────────────
def test_t263_profile_structured_p3_covers_why_and_experience(db):
    _confirm("u_a", _doc())
    ev = [e["payload"] for e in le.get_events(type_="profile.structured", db_path=appmod.DB)][-1]
    assert ev["canon_version"] == "p3"
    base = {"will_text": "w", "v5": _doc(), "supporting_raw": _doc()["supporting_material"]}
    h = SL.profile_content_hash(base)
    for k in ("意志_なぜ", "経験"):
        changed = {**base, "supporting_raw": {**base["supporting_raw"], k: "変えた"}}
        assert SL.profile_content_hash(changed) != h                       # p3 は変わる
        assert SL.profile_content_hash(changed, "p2") == SL.profile_content_hash(base, "p2")   # p2 は変わらない


def test_t264_necessity_published_c4_without_alpha_beta(db):
    _confirm("u_a", _doc())
    with N._connect(appmod.DB) as con:
        rows = con.execute("SELECT canon_version, alpha, beta FROM necessities").fetchall()
    assert {r[0] for r in rows} == {"c4"} and all(r[1] is None and r[2] is None for r in rows)
    s = [{"文": "a", "必須": True, "型": "資源"}]
    h1 = N.compute_content_hash("", {"gate_s": 0.5, "gate_u": 0.3, "alpha": 1.0}, "ev", sentences=s, offer_hash="o")
    h2 = N.compute_content_hash("", {"gate_s": 0.5, "gate_u": 0.3, "alpha": 2.0}, "ev", sentences=s, offer_hash="o")
    assert h1 == h2                                                       # c4 の数値は gate_s・gate_u だけ


def test_t265_p2_and_c3_events_verify_by_their_rules(db, monkeypatch):
    # 改訂前の版（p2・c3）で書かれた記録を作る
    monkeypatch.setattr(SL, "PROFILE_CANON_V5", "p2")
    monkeypatch.setattr(N, "V5_CANON", "c3")
    inp = {"will_text": "w", "v5": _doc(), "supporting_raw": _doc()["supporting_material"]}
    SL.publish_profile_structured("u_old", inp, actor="u_old", db_path=appmod.DB)
    N.publish_necessity("u_old", "subject", {"gate_s": 0.5, "gate_u": 0.3, "evidence_span": "e"},
                        purpose_id="pur_old", sentences=[{"文": "a", "必須": True, "型": "資源"}],
                        offer_hash="o", db_path=appmod.DB)
    monkeypatch.setattr(SL, "PROFILE_CANON_V5", "p3")                    # 版を戻した後に検証する
    monkeypatch.setattr(N, "V5_CANON", "c4")
    old = [e["payload"] for e in le.get_events(type_="profile.structured", db_path=appmod.DB)
           if e["payload"]["subject_id"] == "u_old"][0]
    assert old["canon_version"] == "p2"
    assert SL.profile_content_hash(inp, old["canon_version"]) == old["content_hash"]
    assert SL.profile_content_hash(inp) != old["content_hash"]            # 現行（p3）とは別の規則
    with N._connect(appmod.DB) as con:
        nid, canon = con.execute("SELECT necessity_id, canon_version FROM necessities "
                                 "WHERE owner_ref='u_old'").fetchone()
    assert canon == "c3" and N.verify_content_hash(nid, db_path=appmod.DB) == {"ok": True, "canon_version": "c3"}
    _confirm("u_new", _doc())                                             # 新しい版（c4）も同じ関数で検証できる
    with N._connect(appmod.DB) as con:
        new_ids = [r[0] for r in con.execute("SELECT necessity_id FROM necessities "
                                             "WHERE owner_ref='u_new'").fetchall()]
    res = [N.verify_content_hash(i, db_path=appmod.DB) for i in new_ids]
    assert res and all(x == {"ok": True, "canon_version": "c4"} for x in res), res
    assert le.verify_chain(db_path=appmod.DB)["ok"] is True               # 過去のイベントを書き換えていない


# ── 266・267 表示とプロンプト ───────────────────────────────────────────────────────
def test_t266_profile_shows_why():
    from db import _seeker_from_v4_row
    from profile_view import build_profile_view
    flat = v5.to_flat(_doc())
    row = (flat["will_text"], flat["state_have"], flat["state_can_type"], flat["state_bound"],
           flat["state_unsorted"], json.dumps(flat["supporting_raw"], ensure_ascii=False))
    pv = build_profile_view(_seeker_from_v4_row(row))
    assert pv["will_why"] == "構造がつながりを阻むべきではない。"
    tpl = open(os.path.join(ROOT, "templates", "_profile_view.html"), encoding="utf-8").read()
    assert 'item("その根にある捉え方", pv.will_why)' in tpl


def test_t267_register_prompts_are_revision3():
    page = appmod.app.test_client().get("/register").get_data(as_text=True)
    for pid, name, src in (("promptA", "v5_A_dialogue.txt", "v5r3-A"), ("promptB", "v5_B_selfwrite.txt", "v5r3-B")):
        m = re.search(rf'<pre id="{pid}">(.*?)</pre>', page, re.S)
        text = htmlmod.unescape(m.group(1))
        assert text == open(os.path.join(ROOT, "docs", "prompts", name), encoding="utf-8").read()
        assert f'"source": "{src}"' in text and "alpha" not in text.replace("`alpha`・`beta`", "")


def test_t61_verify_c3_rows_written_with_integer_numbers(db, monkeypatch):
    """c3 までの書き込みは gate_s=0 を int で計算していた（REAL 列からは 0.0 で戻る）。検証はそれも通す（番号なし）。"""
    monkeypatch.setattr(N, "V5_CANON", "c3")
    s = [{"文": "a", "必須": True, "型": "資源"}]
    r = N.publish_necessity("u_old", "subject", {"gate_s": 0.0, "gate_u": 0.3, "evidence_span": "e"},
                            purpose_id="pur_old", sentences=s, offer_hash="o", db_path=appmod.DB)
    with N._connect(appmod.DB) as con:
        ev = con.execute("SELECT evidence_commit FROM necessities WHERE necessity_id=%s",
                         (r["necessity_id"],)).fetchone()[0]
        old = N.compute_content_hash("", {"gate_s": 0, "gate_u": 0.3, "alpha": None, "beta": None}, ev,
                                     canon_version="c3", sentences=s, offer_hash="o")
        con.execute("UPDATE necessities SET content_hash=%s WHERE necessity_id=%s", (old, r["necessity_id"]))
    assert N.verify_content_hash(r["necessity_id"], db_path=appmod.DB) == {"ok": True, "canon_version": "c3"}
