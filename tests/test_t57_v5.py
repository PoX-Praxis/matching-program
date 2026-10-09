"""指示書57 — 照合の段2〜3: 目的ごとの必要像・与え像・文単位の照合（205〜220）。"""
import json, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import ledger
import ledger_events as le
import necessities as N
import v5
from db_v4 import MemoryStore
from embedding_config import MODEL_TAG
from matcher_v5 import direction, match_pair, judge

TPL = lambda n: open(os.path.join(ROOT, "templates", n), encoding="utf-8").read()
STORY = "私は仕組みを作るのが好きです。事業を一緒に立ち上げる人がほしい。論理の穴を指摘してくれる人も。"


def _doc(**over):
    d = {
        "id": "kaoru_2026",
        "schema_version": "v5",
        "generator": "Claude Opus 4.8",
        "purposes": [
            {"purpose_id": "p1", "向かう先": "自然に辿り着ける状態を実現したい。", "手段": "照合の仕組みを根づかせる。",
             "必要像": [{"文": "事業を立ち上げる局面で、構想を実行に移す側として関わってきた人。", "必須": True, "型": "関わり方"},
                      {"文": "事業構造をロジカルに組み立てる立場で関わる人。", "必須": False, "型": "資源"}],
             "数値": {"gate_s": 0.9, "gate_u": 0.3, "alpha": 1.2, "beta": 1.0, "p_sharpness": 0.5},
             "根拠": "事業を一緒に立ち上げる人がほしい。"},
            {"purpose_id": "p2", "向かう先": "照合の論理を説明できる形にしたい。", "手段": "文単位で検証できるようにする。",
             "必要像": [{"文": "論理の穴を指摘し、構造として組み直す関わり方をしてきた人。", "必須": False, "型": "資源"}],
             "数値": {"gate_s": 0.0, "gate_u": 0.3, "alpha": 0.5, "beta": 1.5}, "根拠": "論理の穴を指摘してくれる人も。"}],
        "与え像": [{"文": "まだ形になっていない仕組みを試作として形にする立場で関われる。", "型": "関わり方"},
                 {"文": "照合の仕組みと、そこに集まる人と機会という場を提供できる。", "型": "資源"}],
        "関心": [{"文": "生まれた環境の構造が自由なつながりを阻むことに関心がある。"}],
        "現状": {"持っているもの": "試作", "できること_型": "形にする", "縛られているもの": "", "未分類": ""},
        "supporting_material": {"一行紹介": "つなぐ人", "要約文": "仕組みを作る人。", "生テキスト": [STORY]},
    }
    d.update(over)
    return d


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


_REAL_SENTENCE_JOB = appmod._v5_sentence_job


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    # 文単位ベクトルの非同期ジョブはテストでは止める（必要なテストは同期で呼ぶ。競合を避ける）
    monkeypatch.setattr(appmod, "_v5_sentence_job", lambda *a, **k: None)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "_conn_ref_resolver",
                        lambda s, purpose_id=None: {"profile_snapshot_hash": f"h_{s}",
                                                    "necessity_hash": v5.latest_event_hash_for_purpose(s, purpose_id, db_path=appmod.DB) if purpose_id else None})
    return appmod.DB


def _confirm(sid, doc, mapping=None, sync_vectors=True, monkeypatch=None):
    """確定の v5 部分（台帳・与え像・必要像・本文）。Postgres の受付（profiles_v4）は介さない。"""
    assigned = v5.assign_purposes(sid, doc, mapping, db_path=appmod.DB)
    doc2 = {**doc, "purposes": [{**p, "purpose_id": pid} for pid, p in assigned]}
    from subject_ledger import publish_profile_structured
    prof = publish_profile_structured(sid, {"will_text": "w", "v5": doc2, "supporting_raw": doc.get("supporting_material")},
                                      actor=sid, db_path=appmod.DB)
    appmod._confirm_v5_ledger(sid, doc2, assigned, prof, {"payload": {}, "attempt_n": 1})
    return [pid for pid, _ in assigned], prof


# ── 205 受信の検証 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("mut,why", [
    (lambda d: d.update(purposes=[]), "1〜3"),
    (lambda d: d["purposes"][0].update(手段=""), "別々"),
    (lambda d: d["purposes"][0].update(向かう先="そのために実現したい。"), "手段が混ざって"),
    (lambda d: d["purposes"][0]["必要像"].extend([{"文": "x", "必須": True, "型": "資源"}] * 2), "必須"),
    (lambda d: d.update(与え像=[{"文": f"関われる{i}。", "型": "関わり方"} for i in range(6)]), "与え像は 0〜5"),
    (lambda d: d["与え像"][0].update(型="能力"), "型"),
    (lambda d: d["purposes"][1]["必要像"][0].update(文="分析ができる方。"), "求人票"),
    (lambda d: d["purposes"][0].update(根拠="語りに無い文章。"), "根拠"),
])
def test_t205_validation_rules(db, mut, why):
    d = _doc()
    mut(d)
    r = _cli("u_a").post("/v4/drafts", json={"raw_text": json.dumps(d, ensure_ascii=False)})
    assert r.status_code == 400 and why in r.get_json()["error"]


def test_t205_rule1_schema_version():
    assert v5.validate(_doc(schema_version="v4")) == (False, "schema_version が v5 ではありません")


def test_t205_valid_v5_draft_saved_and_extra_numbers_dropped(db):
    r = _cli("u_a").post("/v4/drafts", json={"raw_text": json.dumps(_doc(), ensure_ascii=False)})
    assert r.status_code == 201
    d = r.get_json()
    assert d["v5"] is True and "p_sharpness" not in d["payload"]["purposes"][0]["数値"]      # 規則 7


def test_t205_evidence_checked_against_raw_text(db):
    """根拠の検査は supporting_material.生テキストで行う（「本人の語り」の別欄は廃止。指示書60 §2-2）。"""
    d = _doc(supporting_material={"一行紹介": "x", "要約文": "y", "生テキスト": ["別の話。"]})
    r = _cli("u_a").post("/v4/drafts", json={"raw_text": json.dumps(d, ensure_ascii=False), "narrative": STORY})
    assert r.status_code == 400 and "が生テキストにありません" in r.get_json()["error"]


# ── 206 目的の id はサーバーが振る・不変 ───────────────────────────────────────────
def test_t206_purpose_ids_server_side_and_stable(db):
    ids1, _ = _confirm("u_a", _doc())
    assert all(i.startswith("pur_") for i in ids1) and len(set(ids1)) == 2
    sugg = v5.suggest_mapping("u_a", _doc(), db_path=db)
    assert sugg == {"p1": ids1[0], "p2": ids1[1]}                  # 本人が確認する提案
    swapped = _doc()
    swapped["purposes"] = list(reversed(swapped["purposes"]))      # ①が順番を入れ替えても
    ids2, _ = _confirm("u_a", swapped, mapping={"p2": ids1[1], "p1": ids1[0]})
    assert ids2 == [ids1[1], ids1[0]]                              # 本人の対応どおり不変
    ids3, _ = _confirm("u_a", _doc(), mapping={"p1": "new", "p2": ids1[1]})
    assert ids3[0] not in ids1 and ids3[1] == ids1[1]             # 対応が取れない目的は新しい目的
    live = {n["purpose_id"] for n in v5.live_necessities_v5("u_a", db_path=db)}
    assert ids1[0] not in live                                     # 消えた目的の必要像は取り下げ（記録は残る）
    assert any(e["type"] == "necessity.retired" for e in le.get_events(db_path=db))


# ── 207・208 目的ごとの necessity.published（c3）とピン留め ─────────────────────────
def test_t207_one_event_per_purpose_c3(db):
    ids, _ = _confirm("u_a", _doc())
    evs = [e for e in le.get_events(type_="necessity.published", db_path=db)]
    assert [e["payload"]["purpose_id"] for e in evs] == ids
    assert all(e["payload"]["offer_hash"] == v5.offer_hash(_doc()["与え像"]) for e in evs)
    with N._connect(db) as con:
        # 新しく書く版は c4（指示書61 で c3 から上げた。c3 の行の検証は test_t61 で確かめる）
        assert {r[0] for r in con.execute("SELECT canon_version FROM necessities").fetchall()} == {"c4"}


def test_t208_offer_change_changes_each_purpose_hash(db):
    _confirm("u_a", _doc())
    h1 = [e["payload"]["content_hash"] for e in le.get_events(type_="necessity.published", db_path=db)]
    d2 = _doc()
    d2["与え像"][0]["文"] = "別の関わり方で関われる。"
    _confirm("u_a", d2, mapping=v5.suggest_mapping("u_a", d2, db_path=db))
    evs = le.get_events(type_="necessity.published", db_path=db)
    assert len(evs) == 4                                           # 必要像は同じでも、与え像が変われば両目的とも新しい版
    h2 = [e["payload"]["content_hash"] for e in evs[2:]]
    assert set(h1).isdisjoint(h2)
    _confirm("u_a", d2, mapping=v5.suggest_mapping("u_a", d2, db_path=db))
    assert len(le.get_events(type_="necessity.published", db_path=db)) == 4   # 同じ内容は churn で書かない


# ── 209 宣言の canon_version ─────────────────────────────────────────────────────
def test_t209_profile_structured_canon_version(db):
    from subject_ledger import publish_profile_structured, profile_content_hash
    publish_profile_structured("u_v4", {"will_text": "w"}, actor="u_v4", db_path=db)
    _confirm("u_a", _doc())
    p4 = [e["payload"] for e in le.get_events(type_="profile.structured", db_path=db) if e["payload"]["subject_id"] == "u_v4"][0]
    p5 = [e["payload"] for e in le.get_events(type_="profile.structured", db_path=db) if e["payload"]["subject_id"] == "u_a"][0]
    assert "canon_version" not in p4 and p5["canon_version"] == "p3"      # 無いもの＝c1（v5 は 61 で p2→p3）
    assert p4["content_hash"] == profile_content_hash({"will_text": "w"})   # c1 のまま読める
    assert le.verify_chain(db_path=db)["ok"] is True


def test_t209_c1_c2_rows_still_recompute():
    nums = {"gate_s": 0.5, "gate_u": 0.3, "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0}
    c1 = N.compute_content_hash("t", nums, "ev", canon_version="c1")
    c2 = N.compute_content_hash("t", nums, "ev", "seek", canon_version="c2")
    assert c1 != c2 and c1 == N.compute_content_hash("t", nums, "ev", "other", canon_version="c1")


# ── 210・219 文単位ベクトルと、型をまたぐ比較 ────────────────────────────────────────
def test_t210_sentence_vectors_saved(db):
    _confirm("u_a", _doc())
    _REAL_SENTENCE_JOB("u_a", v5.latest_offer("u_a", db_path=db))
    nec = v5.live_necessities_v5("u_a", db_path=db)
    assert [len(v5.get_sentence_vectors("necessity", n["necessity_id"], MODEL_TAG, db_path=db)) for n in nec] == [2, 1]
    off = v5.latest_offer("u_a", db_path=db)
    assert len(v5.get_sentence_vectors("offer", off["offer_id"], MODEL_TAG, db_path=db)) == 2


def test_t219_types_do_not_partition_comparison():
    needs = [{"text": "関わり方の必要像", "vec": [1.0, 0.0], "required": True}]
    offers = [{"text": "資源の与え像", "vec": [1.0, 0.0]}]       # 型が違っても比べられる
    ok, pairs = direction(needs, offers)
    assert ok and pairs == [{"need": "関わり方の必要像", "offer": "資源の与え像"}]
    import inspect, matcher_v5
    assert "型" not in inspect.getsource(matcher_v5.direction)


def test_t210_required_sentence_must_be_met():
    needs = [{"text": "必須", "vec": [1.0, 0.0], "required": True},
             {"text": "歓迎", "vec": [0.0, 1.0], "required": False}]
    assert direction(needs, [{"text": "o", "vec": [0.0, 1.0]}])[0] is False   # 必須が満たされない
    assert direction(needs, [{"text": "o", "vec": [1.0, 0.0]}])[0] is True    # 歓迎は満たされなくてよい
    assert judge([1.0, 0.0], [1.0, 0.0]) and not judge([1.0, 0.0], [-1.0, 0.0])


# ── 211・212・216・218 照合の結果（目的ごと・引用の対・v4 互換）──────────────────────────
@pytest.fixture
def world(db, monkeypatch):
    """me（v5・2 目的）と c1（v4: 与え像なし）・c2（v5）の照合。文ベクトルは合成。"""
    store = MemoryStore()
    for pid, v in (("me", [1.0, 0.0]), ("c1", [1.0, 0.0]), ("c2", [0.0, 1.0])):
        store.save_profile(pid, fields={"will_text": "w", "state_have": "s"}, supporting_raw={},
                           supporting_redacted={}, pii_redaction_status="none", migrated_from=None,
                           generation_status="ready")
        store.save_necessity(pid, MODEL_TAG, {"necessity_text": f"{pid}の必要像", "gate_s": 0, "gate_u": 0,
                                              "p_sharpness": 0, "alpha": 1, "beta": 1, "evidence_span": ""})
        store.save_vectors(pid, MODEL_TAG, {k: list(v) for k in
                                            ("will_symmetric", "will_passage", "state_passage", "necessity_query")})
    for sid in ("me", "c2"):
        _confirm(sid, _doc())
    # 文ベクトルを合成で上書き: me の p1 は [1,0] を、p2 は [0,1] を必要とする。c2 は [0,1] を与える
    def put(kind, ref, vecs):
        texts = [t for t, _ in v5.get_sentence_vectors(kind, ref, MODEL_TAG, db_path=db)] or [f"{ref}{i}" for i in range(len(vecs))]
        v5.save_sentence_vectors(kind, ref, texts[:len(vecs)], MODEL_TAG, lambda t, r, it=iter(vecs): next(it), db_path=db)
    for sid in ("me", "c2"):
        nec = v5.live_necessities_v5(sid, db_path=db)
        put("necessity", nec[0]["necessity_id"], [[1.0, 0.0], [1.0, 0.0]])
        put("necessity", nec[1]["necessity_id"], [[0.0, 1.0]])
        put("offer", v5.latest_offer(sid, db_path=db)["offer_id"], [[0.0, 1.0], [0.0, 1.0]] if sid == "c2" else [[1.0, 0.0], [1.0, 0.0]])
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)
    monkeypatch.setattr(appmod, "_v4_store", lambda: store)
    monkeypatch.setattr(appmod, "_matching_available", lambda: True)
    monkeypatch.setattr(appmod, "_linked_ids", lambda ids: set(ids))
    import migrate_v4
    monkeypatch.setattr(migrate_v4, "ensure_migrated", lambda *a, **k: None)
    return store


def test_t211_t212_results_grouped_by_purpose_with_quote_pairs(world):
    d = _cli("me").post("/v4/match", json={}).get_json()
    by = {g["purpose_id"]: [r["candidate_id"] for r in g["results"]] for g in d["groups"]}
    me_purposes = [n["purpose_id"] for n in v5.live_necessities_v5("me", db_path=appmod.DB)]
    assert by[me_purposes[0]] == ["c1"]                    # p1（[1,0] が必要）には c1（現状 [1,0]）
    assert by[me_purposes[1]] == ["c2"]                    # p2（[0,1] が必要）には c2（与え像 [0,1]）
    card = d["groups"][1]["results"][0]
    assert 1 <= len(card["reasons"]) <= 2
    assert set(card["reasons"][0]) == {"kind", "need_label", "need", "offer_label", "offer"}
    blob = json.dumps(d, ensure_ascii=False)
    for k in ("score", "cos", "gate_s", "0.7"):
        assert k not in blob, k


def test_t216_v4_counterpart_matched_by_state(world):
    d = _cli("me").post("/v4/match", json={}).get_json()
    assert "groups" in d, d
    c1 = next(r for g in d["groups"] for r in g["results"] if r["candidate_id"] == "c1")
    assert c1["reasons"][0]["offer_label"] == "相手の現状"            # 与え像が無い人は現状で照合
    assert _cli("c1").get("/api/my/purposes").get_json() == {"purposes": [], "has_offer": False}
    assert "与え像が未設定のため、現状で照合しています" in TPL("mypage.html")


def test_t218_wording(world):
    part = TPL("_match_reason.html")
    assert "足りないところを互いに埋める" in part and "相互に噛み合っています" not in part
    # 引用の対の見出し（指示書61 §3 で「…が応えています」から置き換え）
    assert "あなたが必要としていること" in open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "mr-arrow" in part


# ── 213・214・215 接続（目的・版の参照・承認の根拠・申し出の文）────────────────────────────
def test_t214_offer_stores_purpose_and_refs_idempotent_per_purpose(world):
    pids = [n["purpose_id"] for n in v5.live_necessities_v5("me", db_path=appmod.DB)]
    for _ in range(2):
        assert _cli("me").post("/approve", json={"to_id": "c2", "purpose_id": pids[0]}).status_code == 200
    _cli("me").post("/approve", json={"to_id": "c2", "purpose_id": pids[1]})
    with ledger._connect(appmod.DB) as con:
        rows = con.execute("SELECT purpose_id, necessity_ref, offer_ref FROM connection_requests "
                           "WHERE from_subject='me'").fetchall()
    assert sorted(r[0] for r in rows) == sorted(pids)             # 冪等は「相手 × 目的」
    assert all(r[1] and r[2] for r in rows)
    assert _cli("me").post("/approve", json={"to_id": "c2", "purpose_id": "pur_not_mine"}).status_code == 400


def test_t213_approval_reason_recomputed_from_refs(world):
    pids = [n["purpose_id"] for n in v5.live_necessities_v5("c2", db_path=appmod.DB)]
    _cli("c2").post("/approve", json={"to_id": "me", "purpose_id": pids[1]})
    r = _cli("me").get("/api/connections/reason?with=c2").get_json()["reason"]
    assert r and r["reasons"] and "score" not in json.dumps(r)
    with ledger._connect(appmod.DB) as con:                        # 根拠のコピーは保存しない
        cols = [c[1] for c in con.execute("PRAGMA table_info(connection_requests)").fetchall()]
    assert "reason" not in " ".join(cols)
    for t in ("inbox.html", "mypage.html", "profile.html"):
        assert "PoXReason" in TPL(t)


def test_t214_establish_uses_purpose_event_hash(world):
    pids = [n["purpose_id"] for n in v5.live_necessities_v5("me", db_path=appmod.DB)]
    _cli("me").post("/approve", json={"to_id": "c2", "purpose_id": pids[1]})
    _cli("c2").post("/approve", json={"to_id": "me"})
    ev = le.get_events(type_="connection.established", db_path=appmod.DB)[-1]["payload"]
    mine = ev["a_ref"] if ev["a"] == "me" else ev["b_ref"]
    assert mine["necessity_hash"] == v5.latest_event_hash_for_purpose("me", pids[1], db_path=appmod.DB)


def test_t215_offer_message_box_only_for_offerer():
    """申し出の文の入力欄は申し出る側だけ（57 受理時の訂正 #5）。承認欄は文を「表示する」場所。"""
    assert 'id="offerMsg"' in TPL("profile.html")
    assert "PoXReason.composeHtml" in TPL("connect.html")
    for t in ("inbox.html", "mypage.html"):
        assert "apvMsg_" not in TPL(t) and "composeHtml" not in TPL(t)
        assert "PoXReason.offerHtml" in TPL(t)                         # 受けた文は表示する
    assert 'show("offerBox", state === "none")' in TPL("profile.html")


def test_t57f_approval_does_not_carry_message(world):
    """承認は状態の遷移だけ。承認の側から文を送っても保存しない（DM は成立後だけ・177 と整合）。"""
    pids = [n["purpose_id"] for n in v5.live_necessities_v5("c2", db_path=appmod.DB)]
    _cli("c2").post("/approve", json={"to_id": "me", "purpose_id": pids[1], "message": "申し出の文"})
    _cli("me").post("/approve", json={"to_id": "c2", "message": "承認の側の文"})
    with ledger._connect(appmod.DB) as con:
        rows = dict(con.execute("SELECT from_subject, offer_message FROM connection_requests").fetchall())
    assert rows == {"c2": "申し出の文", "me": None}


def test_t57f_exclusion_is_per_purpose(world):
    """申し出中の相手は「その目的」のグループからだけ外す（57 受理時の推奨 #4）。"""
    pids = [n["purpose_id"] for n in v5.live_necessities_v5("me", db_path=appmod.DB)]
    _cli("me").post("/approve", json={"to_id": "c1", "purpose_id": pids[1]})     # 別の目的で申し出中
    _cli("me").post("/approve", json={"to_id": "c2", "purpose_id": pids[1]})     # この目的で申し出中
    d = _cli("me").post("/v4/match", json={}).get_json()
    by = {g["purpose_id"]: [r["candidate_id"] for r in g["results"]] for g in d["groups"]}
    assert by.get(pids[0]) == ["c1"] and pids[1] not in by
    _cli("me").post("/approve", json={"to_id": "c1"})                            # 目的を指定しない申し出は全部から外す
    d = _cli("me").post("/v4/match", json={}).get_json()
    assert all(r["candidate_id"] != "c1" for g in d["groups"] for r in g["results"])


def test_t223_register_prompts_are_v5_revision2():
    """登録画面のプロンプトは v5 改訂2（A 対話・B 自分で書く。指示書60 で書き換え）。v4 の JSON も受け付ける旨を残す。"""
    html = appmod.app.test_client().get("/register").get_data(as_text=True)
    assert 'id="promptA"' in html and 'id="promptB"' in html and "promptV5" not in html
    assert '"schema_version": "v5"' in html.replace("&#34;", '"')
    assert "v4 の JSON も今までどおり登録できます" in html


# ── 217 軌跡 ──────────────────────────────────────────────────────────────────
def test_t217_trajectory_purposes_and_branch():
    import trajectory as T
    snap = {"snapshot_id": "s1", "necessity": {"necessity_text": "x", "purposes": [
        {"purpose_id": "pur_1", "向かう先": "A", "必要像": [{"文": "n1", "必須": True}]},
        {"purpose_id": "pur_2", "向かう先": "B", "必要像": [{"文": "n2", "必須": False}]}],
        "与え像": [{"文": "o1"}]}}
    c = T._content(snap, "owner", True)
    assert [p["label"] for p in c["purposes"]] == ["A", "B"] and c["offers"] == ["o1"]
    assert "purposes" not in T._content(snap, "third", False)            # 公開閾値は必要像と同じ
    assert "self_purpose" in open(os.path.join(ROOT, "src", "trajectory.py"), encoding="utf-8").read()


# ── 220 「必要像は 1 人 1 本」の 4 箇所 ──────────────────────────────────────────────
def test_t220_one_per_person_assumptions_removed(db):
    _confirm("u_a", _doc())
    live = v5.live_necessities_v5("u_a", db_path=db)
    assert len(live) == 2                                           # ① 目的ごとに生きている（互いに置換しない）
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "v5.latest_event_hash_for_purpose(subject, purpose_id" in src   # ② 根拠は目的ごと
    assert "_side_of(" in src and "live_necessities_v5" in src     # ③ 照合は全目的（v4 ストアの 1 行は全文互換のみ）
    assert "purposes" in open(os.path.join(ROOT, "src", "trajectory.py"), encoding="utf-8").read()   # ④ 軌跡
