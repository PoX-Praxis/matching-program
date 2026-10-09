"""指示書63 段階1 PR-A — v5 の人の編集・再試行を止める（テスト 277〜281）。

段階0 §0-1: v4 の編集・再試行の経路は v5 の本文を持たない入力で宣言（c1）と目的の無い必要像を書き、最後の目的の
必要像を置き換えて照合から消していた。v5 のプロフィールは「作り直した JSON を貼る」でだけ変える。
"""
import json
import os
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

STORY = "私は仕組みを作るのが好きです。事業を一緒に立ち上げる人がほしい。論理の穴を指摘してくれる人も。"


def _doc():
    return {
        "id": "kaoru_2026", "schema_version": "v5", "generator": "GPT-6", "_meta": {"source": "v5r4-A"},
        "purposes": [
            {"purpose_id": "p1", "向かう先": "自然に辿り着ける状態を実現したい。", "手段": "照合の仕組みを根づかせる。",
             "必要像": [{"文": "事業を立ち上げる局面で関わってきた人。", "必須": True, "型": "関わり方"}],
             "数値": {"gate_s": 0.3, "gate_u": 0.3}, "根拠": "事業を一緒に立ち上げる人がほしい。"},
            {"purpose_id": "p2", "向かう先": "照合の論理を説明できる形にしたい。", "手段": "文単位で検証する。",
             "必要像": [{"文": "論理の穴を指摘してきた人。", "必須": False, "型": "資源"}],
             "数値": {"gate_s": 0.0, "gate_u": 0.3}, "根拠": "論理の穴を指摘してくれる人も。"}],
        "与え像": [{"文": "仕組みを試作として形にする立場で関われる。", "型": "関わり方"}],
        "現状": {"持っているもの": "試作", "できること_型": "形にする", "縛られているもの": "時間", "未分類": ""},
        "supporting_material": {"一行紹介": "つなぐ", "要約文": "仕組みを作る人。", "生テキスト": [STORY]},
    }


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.setattr(appmod, "_v5_sentence_job", lambda *a, **k: None)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)
    store = MemoryStore()
    monkeypatch.setattr(appmod, "_v4_store", lambda: store)
    spawned = []
    monkeypatch.setattr(appmod, "_spawn_v4_job", lambda *a, **k: spawned.append((a, k)))
    return {"db": appmod.DB, "store": store, "spawned": spawned}


def _cli(sid):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["subject_id"] = sid
    return c


def _v5_person(sid="u_v5"):
    doc = _doc()
    assigned = v5.assign_purposes(sid, doc, None, db_path=appmod.DB)
    doc2 = {**doc, "purposes": [{**p, "purpose_id": pid} for pid, p in assigned]}
    prof = SL.publish_profile_structured(sid, {"will_text": "w", "v5": doc2,
                                               "supporting_raw": doc["supporting_material"]}, actor=sid, db_path=appmod.DB)
    appmod._confirm_v5_ledger(sid, doc2, assigned, prof, {"payload": doc, "attempt_n": 1})
    return sid


def _v4_person(store, sid="u_v4"):
    fields = {"will_text": "つながりをつくる", "state_have": "試作", "state_can_type": "", "state_bound": "",
              "state_unsorted": ""}
    store.save_profile(sid, fields=fields, supporting_raw={}, supporting_redacted={}, pii_redaction_status="none",
                       migrated_from=None, generation_status="error")
    store.save_necessity(sid, MODEL_TAG, {"necessity_text": "実装できる人", "gate_s": 0.3, "gate_u": 0.3,
                                          "p_sharpness": 0, "alpha": 1, "beta": 1, "evidence_span": ""})
    return sid


def _state(db):
    return (len(le.get_events(db_path=db)), [n["purpose_id"] for n in v5.live_necessities_v5("u_v5", db_path=db)])


# ── 277 v5 の人の編集・再試行は 409 ────────────────────────────────────────────────
def test_t277_v5_edit_and_retry_blocked(db):
    _v5_person()
    before = _state(db["db"])
    c = _cli("u_v5")
    r = c.post("/api/profile/u_v5/core", json={"state_have": "編集した"})
    assert r.status_code == 409 and r.get_json() == appmod.V5_EDIT_BLOCKED
    r = c.post("/v4/seekers/u_v5/retry")
    assert r.status_code == 409 and r.get_json()["error"] == "v5_profile"
    assert _state(db["db"]) == before and len(before[1]) == 2         # 台帳も生きている目的も変わらない
    assert db["spawned"] == []


# ── 278 裏の処理（v4 の経路）を直接呼んでも書かない ──────────────────────────────────────
def test_t278_v4_job_writes_nothing_for_v5(db):
    _v5_person()
    before = _state(db["db"])
    nec = {"necessity_text": "x", "gate_s": 0.9, "gate_u": 0.3, "p_sharpness": 0, "alpha": 1, "beta": 1,
           "evidence_span": "e"}
    appmod._v4_async_job("u_v5", {"will_text": "w", "state_have": "編集した"}, nec, is_fallback=False)
    assert _state(db["db"]) == before


# ── 279 目的つきの必要像を持つ人に、目的なしの necessity.published は書けない ────────────────
def test_t279_purposeless_necessity_refused_for_v5(db):
    _v5_person()
    before = _state(db["db"])
    with pytest.raises(ValueError):
        N.publish_necessity("u_v5", "subject", {"necessity_text": "x", "gate_s": 0.3, "gate_u": 0.3,
                                                "evidence_span": "e"}, db_path=db["db"])
    assert _state(db["db"]) == before
    # 目的の無い人（v4）には従来どおり書ける
    assert not N.publish_necessity("u_v4x", "subject", {"necessity_text": "x", "gate_s": 0.3, "gate_u": 0.3,
                                                        "evidence_span": "e"}, db_path=db["db"])["skipped"]


# ── 280 v4 の人の編集・再試行は従来どおり ───────────────────────────────────────────
def test_t280_v4_edit_and_retry_unchanged(db):
    _v4_person(db["store"])
    c = _cli("u_v4")
    r = c.post("/api/profile/u_v4/core", json={"state_have": "編集した持っているもの"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    r = c.post("/v4/seekers/u_v4/retry")
    assert r.status_code != 409
    assert db["spawned"]                                             # 裏の処理（v4 の経路）が起動する
    assert c.get("/api/my/purposes").get_json()["is_v5"] is False


# ── 281 v5 の人のマイページは「作り直す」 ──────────────────────────────────────────
def test_t281_mypage_shows_rebuild_for_v5(db):
    _v5_person()
    assert _cli("u_v5").get("/api/my/purposes").get_json()["is_v5"] is True
    tpl = open(os.path.join(ROOT, "templates", "mypage.html"), encoding="utf-8").read()
    body = tpl[tpl.index("function applyV5Owner()"):tpl.index("/* ══ 照合の準備状態")]
    assert 'getElementById("editLink")' in body and "outerHTML = REBUILD_HTML" in body
    assert ">作り直す</a>" in tpl and "AI で作り直した JSON を貼ると、新しい版として記録されます。" in tpl
    status = tpl[tpl.index('if (s === "error")'):tpl.index("async function retryGeneration")]
    assert "mp.is_v5" in status and 'href="/register">作り直す' in status   # v5 には再試行を出さない
    edit = open(os.path.join(ROOT, "templates", "edit.html"), encoding="utf-8").read()
    assert ".is_v5" in edit and 'href="/register">作り直す' in edit           # 見せ方の編集も v5 には出さない


def test_t63a_v4_json_paste_refused_for_v5(db, monkeypatch):
    """v5 の人が v4 の JSON を貼って確定すると、目的の無い必要像で置き換わるので 409（番号なし）。"""
    _v5_person()
    monkeypatch.setattr(appmod, "_ingest_v4_from_flat", lambda *a, **k: ("u_v5", {"necessity_text": "x"}, False))
    c = _cli("u_v5")
    v4 = {"id": "x", "schema_version": "v4", "seeker": {"意志": "w", "現状": {"持っているもの": "h"}},
          "necessity": {"necessity_text": "n", "gate_s": 0.3, "gate_u": 0.3, "p_sharpness": 0.0, "alpha": 1.0,
                        "beta": 1.0, "evidence_span": ""}}
    did = c.post("/v4/drafts", json={"raw_text": json.dumps(v4, ensure_ascii=False)}).get_json()["draft_id"]
    before = _state(db["db"])
    r = c.post(f"/v4/drafts/{did}/confirm", json={})
    assert r.status_code == 409 and r.get_json()["error"] == "v5_profile"
    assert _state(db["db"]) == before
