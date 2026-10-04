"""指示書55-2 PR-B — 表示と母集団・方向 B・入口の閾値・埋め込みのガード。

166 方向 B／167 必要像の無い人を出さない／168 一覧と照合の母集団が v4／169 generation_status を出さない
170 件数を出さない／171 本文を切り詰めない／172 生 id を画面に出さない／183 本番で stub は起動しない
184・192 stub の間は照合の結果を出さない＋起動ログ／186 接続済み・申し出中を出さない
187 軸は 2 つに要約／190 新規登録者が同じタグで母集団に入る／191 stub のタグは実タグと分離
（193・194 は tests/test_match_auth.py）
"""
import json, math, os, subprocess, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
from db_v4 import MemoryStore, ingest_profile_v4
from embedding_config import MODEL_TAG
from matcher_v4 import rank_candidates, public_axis, passes_entry
from match_config import MATCH_ENTRY_THRESHOLD
from necessity_gen import compute_gamma

TPL = os.path.join(ROOT, "templates")


def _tpl(name):
    return open(os.path.join(TPL, name), encoding="utf-8").read()


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


def _u(x):
    return [x, math.sqrt(max(0.0, 1 - x * x))]


def _vecs(will, state, need):
    return {"will_symmetric": will, "will_passage": will, "state_passage": state, "necessity_query": need}


@pytest.fixture
def v4(monkeypatch):
    """MemoryStore で /v4/match を通す。me・c1 は噛み合う／nonec は必要像なし／far は噛み合わない。"""
    monkeypatch.delenv("POX_DEBUG", raising=False)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    store = MemoryStore()
    gamma = compute_gamma(0.6, 0.3)
    same = _u(1.0)
    for pid, nec, vecs in (("me", "翻訳できる開発者", _vecs(same, same, same)),
                           ("c1", "設計できる人", _vecs(same, same, same)),
                           ("nonec", "", _vecs(same, same, same)),
                           ("far", "遠い人", _vecs([-1.0, 0.0], [-1.0, 0.0], [-1.0, 0.0]))):
        store.save_profile(pid, fields={"will_text": "w", "state_have": "s"}, supporting_raw={},
                           supporting_redacted={}, pii_redaction_status="none", migrated_from=None,
                           generation_status="ready")
        store.save_necessity(pid, MODEL_TAG, {"necessity_text": nec, "gate_s": 0.6, "gate_u": 0.3,
                                              "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0,
                                              "gamma": gamma, "evidence_span": ""})
        store.save_vectors(pid, MODEL_TAG, vecs)
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)
    monkeypatch.setattr(appmod, "_v4_store", lambda: store)
    monkeypatch.setattr(appmod, "_matching_available", lambda: True)
    monkeypatch.setattr(appmod, "_linked_ids", lambda ids: set(ids))
    import migrate_v4
    monkeypatch.setattr(migrate_v4, "ensure_migrated", lambda *a, **k: None)
    return store


def _match(sid="me"):
    return _cli(sid).post("/v4/match", json={"seeker_id": sid}).get_json()


# ── 166 方向 B ──────────────────────────────────────────────────────────────
def test_t166_direction_b_is_computed():
    # 自分の必要像は相手の現状と噛み合わない（方向 A は弱い）が、相手の必要像は自分の現状と噛み合う
    seeker = _vecs([0.0, 1.0], _u(1.0), [-1.0, 0.0])
    cand = _vecs([1.0, 0.0], [0.0, -1.0], _u(1.0))
    r = rank_candidates(seeker, [("c", cand)], gamma=0.0)[0]
    assert r["attribution"]["d_sim"] == pytest.approx(1.0)
    assert r["score_b"] > r["score"]
    assert public_axis(r["attribution"]) == "fill_theirs"
    # 方向 B で入口を通る（方向 A だけなら通らない）
    assert r["score"] < MATCH_ENTRY_THRESHOLD <= r["score_b"] and passes_entry(r)


def test_t166_necessity_path_passes_owner_state():
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "owner_state_passage=owner_sp" in src


# ── 167 必要像の無い人を出さない ／ 入口の閾値 ─────────────────────────────────
def test_t167_candidates_without_necessity_are_excluded(v4):
    ids = [r["candidate_id"] for r in _match()["results"]]
    assert "nonec" not in ids and "far" not in ids and ids == ["c1"]


def test_t167_seeker_without_necessity_gets_no_necessity_state(v4):
    d = _match("nonec")
    assert d["status"] == "no_necessity" and d["results"] == []


# ── 168 母集団は v4 ───────────────────────────────────────────────────────────
def test_t168_directory_reads_v4_not_v3(monkeypatch):
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    monkeypatch.setattr(appmod, "list_directory_ids", lambda db_path=None: ["u_a", "u_b"])
    monkeypatch.setattr(appmod, "load_all_seekers", lambda **k: pytest.fail("旧 v3 を読んだ"))
    monkeypatch.setattr(appmod, "get_profile_view", lambda i, db_path=None: {"headline": "h", "pursuing": "w"})
    monkeypatch.setattr(appmod, "get_public_necessity", lambda i: None)
    rows = _cli().get("/seekers").get_json()
    assert [r["id"] for r in rows] == ["u_a", "u_b"]
    import db
    src = open(db.__file__, encoding="utf-8").read()
    assert "FROM profiles_v4 p JOIN auth_identities" in src      # 紐づきのある v4 登録者だけ


def test_t168_connect_flow_does_not_use_v3_match():
    assert 'fetch("/match"' not in _tpl("profile.html") and 'fetch("/match"' not in _tpl("connect.html")


# ── 169 generation_status ／ 170 件数 ／ 171 切り詰め ──────────────────────────
def test_t169_t171_directory_no_status_no_truncation(monkeypatch):
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    long_will = "あ" * 300
    monkeypatch.setattr(appmod, "list_directory_ids", lambda db_path=None: ["u_a"])
    monkeypatch.setattr(appmod, "get_profile_view",
                        lambda i, db_path=None: {"headline": "h", "pursuing": long_will})
    monkeypatch.setattr(appmod, "get_public_necessity", lambda i: {"necessity_text": "い" * 300})
    r = _cli().get("/seekers").get_json()[0]
    assert set(r) == {"id", "handle", "name", "one_liner", "will", "necessity"}
    assert r["will"] == long_will and r["necessity"] == "い" * 300          # 171
    blob = json.dumps(r, ensure_ascii=False)
    assert "generation_status" not in blob and "preparing" not in blob and "error" not in blob   # 169
    html = _tpl("connect.html")
    assert "necessity_state" not in html and "必要としている相手: 準備中です" not in html


def test_t170_no_counts(v4):
    assert "pool_size" not in _match()
    html = _tpl("connect.html")
    assert "プール" not in html and "pool_size" not in html
    assert "いま噛み合う相手は見つかっていません" in html


# ── 172 生 id を画面に出さない ────────────────────────────────────────────────
def test_t172_templates_do_not_render_raw_ids():
    conn = _tpl("connect.html")
    assert ">${escHtml(cid)}<" not in conn and ">${escHtml(r.id)}<" not in conn     # 見える文字に id を出さない
    assert "subject-id\">${" not in _tpl("inbox.html")
    assert "c.other_name || c.other_id" not in _tpl("inbox.html")
    conv = _tpl("conversation.html")
    assert 'id="myName"' not in conv and "m.from_name || m.from_id" not in conv
    mp = _tpl("mypage.html")
    assert "${escHtml(other)}</a>" not in mp and "escHtml(other)} の承認待ち" not in mp
    pvh = _tpl("_profile_view.html")
    assert "esc(dn || sid)" not in pvh and "esc(dn ? sid" not in pvh


def test_t172_api_names_fall_back_to_label_not_id(monkeypatch):
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    monkeypatch.setattr(appmod, "get_profile_view", lambda i, db_path=None: {"headline": "h"})
    d = _cli().get("/api/profile/u_nameless").get_json()
    assert d["display_name"] == appmod.UNNAMED_LABEL and d["display_name_set"] is False


# ── 183・191・192 埋め込みのガード（別プロセスで起動を確かめる）────────────────────
def _boot(env_extra, code="import app"):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("POX_EMBED_") and k not in ("POX_DEBUG", "POX_TEST_ALLOW_STUB")}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def test_t183_prod_refuses_stub():
    r = _boot({})                                   # 本番（POX_DEBUG なし）・backend 未設定＝stub
    assert r.returncode != 0 and "POX_EMBED_BACKEND が stub" in (r.stderr + r.stdout)
    ok = _boot({"POX_DEBUG": "1"})                  # 開発時だけ許す
    assert ok.returncode == 0, ok.stderr


def test_t192_startup_log_reports_backend_and_tag():
    r = _boot({"POX_DEBUG": "1", "POX_EMBED_MODEL_TAG": "nomic-emb-v2"})
    out = r.stdout
    assert "[embedding] backend=stub backend_env_set=False" in out
    assert "model_tag=stub-d768 model_tag_env_set=True dim=768" in out
    assert "照合の結果は出しません" in out


def test_t191_stub_tag_is_separated_from_real_tags():
    code = "import sys; sys.path.insert(0,'src'); import embedding_config as e; print(e.MODEL_TAG)"
    assert _boot({"POX_EMBED_MODEL_TAG": "nomic-emb-v2"}, code).stdout.strip() == "stub-d768"
    assert _boot({"POX_EMBED_BACKEND": "nomic", "POX_EMBED_MODEL_TAG": "nomic-emb-v2"},
                 code).stdout.strip() == "nomic-emb-v2"            # 既存の実タグは凍結（改名しない）
    bad = _boot({"POX_EMBED_BACKEND": "nomic", "POX_EMBED_MODEL_TAG": "qwen3-embedding-0.6b-d1024"}, code)
    assert bad.returncode != 0 and "食い違っています" in bad.stderr


def test_t184_stub_shows_no_results_but_other_screens_work(v4, monkeypatch):
    monkeypatch.setattr(appmod, "_matching_available", lambda: False)
    d = _match()
    assert d["status"] == "unavailable" and d["results"] == []
    monkeypatch.setattr(appmod, "list_directory_ids", lambda db_path=None: [])
    assert _cli("me").get("/seekers").status_code == 200
    assert _cli("me").get("/connect").status_code == 200


def test_t184_matching_available_follows_backend(monkeypatch):
    import embedding_config
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.setattr(embedding_config, "BACKEND", "stub")
    assert appmod._matching_available() is False
    monkeypatch.setenv("POX_DEBUG", "1")
    assert appmod._matching_available() is True
    monkeypatch.delenv("POX_DEBUG")
    monkeypatch.setattr(embedding_config, "BACKEND", "nomic")
    assert appmod._matching_available() is True


# ── 186 接続済み・申し出中を出さない（一覧には出る）──────────────────────────────
def test_t186_engaged_are_excluded_from_results_not_directory(v4, monkeypatch):
    import ledger
    with ledger._connect(appmod.DB) as con:
        con.execute("INSERT INTO connection_requests (id, from_subject, to_subject, status, created_at) "
                    "VALUES (%s,%s,%s,'pending',%s)", ("r1", "me", "c1", "2026-09-30T00:00:00Z"))
    assert [r["candidate_id"] for r in _match()["results"]] == []
    assert ledger.engaged_counterparts("c1", db_path=appmod.DB) == {"me"}     # 向きを問わない
    monkeypatch.setattr(appmod, "list_directory_ids", lambda db_path=None: ["c1"])
    monkeypatch.setattr(appmod, "get_public_necessity", lambda i: None)
    assert [r["id"] for r in _cli("me").get("/seekers").get_json()] == ["c1"]


# ── 187 軸は 2 つに要約 ───────────────────────────────────────────────────────
def test_t187_axes_are_summarized(v4):
    r = _match()["results"][0]
    assert r["axis"] in {"will", "fill", "mutual"}
    assert r["axis"] == "mutual"                          # me と c1 は双方向に噛み合う
    assert [x["kind"] for x in r["reasons"]] == ["fill_mine", "fill_theirs"]
    html = _cli().get("/connect").get_data(as_text=True)      # 共通部品（_match_reason.html）を含む
    for label in ("意志が近い", "足りないところを埋める", "足りないところを互いに埋める"):
        assert label in html
    for old in ("共鳴が効いています", "補完が効いています", "意志の補完が効いています", "相互に噛み合っている"):
        assert old not in html


def test_t187_public_axis_values():
    base = {"ga": 0.9, "gb": 0.6, "gc": 0.5, "c_log_contrib": 0.0}
    assert public_axis(base) == "will"
    assert public_axis({**base, "gb": 0.95}) == "fill_mine"
    assert public_axis({**base, "gb": 0.95, "gd": 0.9}) == "mutual"
    assert public_axis({**base, "gd": 0.95}) == "fill_theirs"


# ── 190 新規登録者が同じタグで母集団に入る ──────────────────────────────────────
def test_t190_new_registrant_enters_pool_with_same_tag():
    store = MemoryStore()
    ingest_profile_v4(store, "me", {"will_text": "つなぐ", "state_have": "設計"})
    ingest_profile_v4(store, "newcomer", {"will_text": "作る", "state_have": "実装"})
    assert "newcomer" in store.candidate_ids("me", MODEL_TAG)
    assert ("newcomer", MODEL_TAG) in store.vectors
    import inspect, db_v4
    for fn in (db_v4.ingest_profile_v4, db_v4.receive_profile_v4, db_v4.vectorize_profile_v4):
        assert inspect.signature(fn).parameters["model_tag"].default == MODEL_TAG
