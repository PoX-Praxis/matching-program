"""指示書45B — 削除の台帳記録 redaction.recorded と discussion_hash v1 の検証（項目 93〜98・93-a）。

本文を伏せる操作は作らない（45B §4）。テストでは通常DBの本文を直接伏せて「削除後」を作る。
"""
import json, os, sys, tempfile
from datetime import datetime, timezone
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import ledger_events as le
import redaction
import talks

REDACTED = redaction.REDACTED_BODY   # 伏せ字の定数（45C §2）
SECRET = "山田さんの持病は○○です"        # 伏せられる本文（他人の属性への言及）


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


def _agreed_proposal(bodies=("方針に賛成です", SECRET, "では進めましょう")):
    """発言つきの提議を合意させ、(talk_id, 合意イベントの event_hash, post_ids) を返す。"""
    os.environ.pop("POX_DEBUG", None)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    cid = _cli("u_alice").post("/api/communities", json={"name": "n", "founder_id": "u_alice"}).get_json()["id"]
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "P", "target": {}}).get_json()["talk_id"]
    pids = [_cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": b}).get_json()["post_id"] for b in bodies]
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    ref = _cli().get(f"/api/talks/{tk}").get_json()["result"]["purpose_event_hash"]
    return tk, ref, pids


def _redact_in_db(post_id):
    """運用者が通常DBで本文を伏せた状態を作る（行・順序・投稿者は残す）。"""
    with talks._connect(appmod.DB) as con:
        con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", (REDACTED, post_id))


def _record(tk, ref, pids, **kw):
    return redaction.record_redaction(tk, ref, redacted_post_ids=pids,
                                      reason_class=kw.get("reason_class", "subject_request"),
                                      decided_by=kw.get("decided_by", "u_operator"), db_path=appmod.DB)


# ── 93: 削除後の再計算が redaction.recorded.result_hash と一致（同時刻の投稿でも安定）──
def test_t093_recompute_matches_result_hash():
    tk, ref, pids = _agreed_proposal()
    assert redaction.verify_discussion(tk, ref, db_path=appmod.DB)["status"] == "intact"
    _redact_in_db(pids[1])
    ev = _record(tk, ref, [pids[1]])
    assert ev["type"] == "redaction.recorded"
    assert redaction.current_discussion_hash(tk, db_path=appmod.DB) == ev["payload"]["result_hash"]
    assert redaction.verify_discussion(tk, ref, db_path=appmod.DB)["status"] == "redacted"


def test_t093_same_timestamp_posts_use_insertion_order(monkeypatch):
    fixed = datetime(2026, 9, 26, tzinfo=timezone.utc)
    monkeypatch.setattr(talks, "_clock", lambda: fixed)      # 全発言が同じ created_at になる
    bodies = [f"発言{i}" for i in range(12)]
    tk, ref, pids = _agreed_proposal(bodies)
    text = talks._discussion_text(tk, appmod.DB)
    assert text == "\n".join(f"u_alice: {b}" for b in bodies)   # タイムスタンプではなく挿入順
    assert redaction.verify_discussion(tk, ref, db_path=appmod.DB)["status"] == "intact"
    # 時計のずれで先の発言の created_at が後になっても、並びは挿入順のまま（ハッシュ不変）
    with talks._connect(appmod.DB) as con:
        con.execute("UPDATE talk_posts SET created_at=%s WHERE post_id=%s", ("2099-01-01T00:00:00+00:00", pids[0]))
    assert talks._discussion_text(tk, appmod.DB).startswith("u_alice: 発言0\n")
    assert redaction.verify_discussion(tk, ref, db_path=appmod.DB)["status"] == "intact"
    _redact_in_db(pids[5])
    ev = _record(tk, ref, [pids[5]])
    for _ in range(3):                                          # 何度計算しても同じ
        assert redaction.current_discussion_hash(tk, db_path=appmod.DB) == ev["payload"]["result_hash"]


# ── 94: 一致しない場合は改ざんとして検出（境界以降の合意）───────────────────────
def test_t094_mismatch_is_detected_as_tampering():
    tk, ref, pids = _agreed_proposal()
    assert not redaction.is_legacy(le.get_events(type_="purpose.agreed", db_path=appmod.DB)[-1])
    with talks._connect(appmod.DB) as con:                      # 削除記録なしで本文を書き換える
        con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", ("書き換えた", pids[0]))
    r = redaction.verify_discussion(tk, ref, db_path=appmod.DB)
    assert r["status"] == "tampered" and r["reason"] == "hash_mismatch"
    # 削除記録の後にさらに書き換えても検出される
    tk, ref, pids = _agreed_proposal()
    _redact_in_db(pids[1]); _record(tk, ref, [pids[1]])
    with talks._connect(appmod.DB) as con:
        con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", ("後から改変", pids[2]))
    assert redaction.verify_discussion(tk, ref, db_path=appmod.DB)["status"] == "tampered"


# ── 95: redaction.recorded が連鎖する（prev_hash → result_hash）──────────────────
def test_t095_redactions_chain():
    tk, ref, pids = _agreed_proposal()
    stored = le.get_events(type_="purpose.agreed", db_path=appmod.DB)[-1]["payload"]["discussion_hash"]
    _redact_in_db(pids[1]); e1 = _record(tk, ref, [pids[1]])
    _redact_in_db(pids[2]); e2 = _record(tk, ref, [pids[2]], reason_class="legal")
    assert e1["payload"]["prev_hash"] == stored
    assert e2["payload"]["prev_hash"] == e1["payload"]["result_hash"]
    assert e1["payload"]["result_hash"] != e2["payload"]["result_hash"]
    r = redaction.verify_discussion(tk, ref, db_path=appmod.DB)
    assert r["status"] == "redacted" and r["redactions"] == 2
    assert le.verify_chain(db_path=appmod.DB)["ok"] is True


# ── 96: 台帳に削除された本文が入っていない ────────────────────────────────────
def test_t096_redacted_text_not_in_ledger():
    tk, ref, pids = _agreed_proposal()
    _redact_in_db(pids[1]); _record(tk, ref, [pids[1]])
    dump = json.dumps(le.get_events(db_path=appmod.DB), ensure_ascii=False)
    assert SECRET not in dump


# ── 97: 台帳に申立てた人物が入っていない（reason_class と scope_digest のみ）──────────
def test_t097_complainant_not_in_ledger():
    tk, ref, pids = _agreed_proposal()
    complainant = "u_yamada_complainant"                       # 申立て人（台帳に渡す経路が無い）
    _redact_in_db(pids[1])
    ev = _record(tk, ref, [pids[1]])
    assert set(ev["payload"]) == {"talk_id", "target_ref", "hash_kind", "canon_version", "prev_hash",
                                  "result_hash", "scope_digest", "reason_class", "decided_by", "recorded_at"}
    assert ev["payload"]["reason_class"] == "subject_request"
    assert ev["payload"]["hash_kind"] == "discussion" and ev["payload"]["canon_version"] == "v1"
    assert complainant not in json.dumps(le.get_events(db_path=appmod.DB), ensure_ascii=False)
    import inspect
    assert "complainant" not in inspect.signature(redaction.record_redaction).parameters
    with pytest.raises(ValueError):
        _record(tk, ref, [pids[1]], reason_class="because")      # reason_class は legal / subject_request のみ


# ── 98: 削除記録を消すと検証が壊れる（記録の剥奪が改ざんとして検出される）─────────────
def test_t098_removing_redaction_record_breaks_verification():
    tk, ref, pids = _agreed_proposal()
    _redact_in_db(pids[1])
    ev = _record(tk, ref, [pids[1]])
    assert redaction.verify_discussion(tk, ref, db_path=appmod.DB)["status"] == "redacted"
    with le._connect(appmod.DB) as con:                          # 台帳から削除記録を剥ぎ取る
        con.execute("DELETE FROM ledger_events WHERE event_hash=%s", (ev["event_hash"],))
    assert redaction.verify_discussion(tk, ref, db_path=appmod.DB)["status"] == "tampered"
    # 末尾以外の記録の剥奪は、連鎖の切れとしても検出される
    tk, ref, pids = _agreed_proposal()
    _redact_in_db(pids[0]); e1 = _record(tk, ref, [pids[0]])
    _redact_in_db(pids[1]); _record(tk, ref, [pids[1]])
    with le._connect(appmod.DB) as con:
        con.execute("DELETE FROM ledger_events WHERE event_hash=%s", (e1["event_hash"],))
    r = redaction.verify_discussion(tk, ref, db_path=appmod.DB)
    assert r["status"] == "tampered" and r["reason"] == "chain_broken"
    assert le.verify_chain(db_path=appmod.DB)["ok"] is False       # 台帳の連結も壊れる


# ── 93-a: legacy の合意は、再計算が一致しなくても改ざんと判定しない（境界 seq 以下）────────
def test_t093a_legacy_agreement_not_flagged(monkeypatch):
    tk, ref, pids = _agreed_proposal()
    with talks._connect(appmod.DB) as con:                      # 合意後に追記できた時代の状態を模す
        con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", ("合意後の追記", pids[2]))
    agreed = [e for e in le.get_events(type_="purpose.agreed", db_path=appmod.DB)][-1]
    monkeypatch.setenv("POX_LEGACY_BOUNDARY_SEQ", str(agreed["seq"]))       # この合意までが legacy
    assert redaction.verify_discussion(tk, ref, db_path=appmod.DB)["status"] == "legacy_unverified"
    # legacy の連鎖は台帳の保存値から始まる
    _redact_in_db(pids[1]); ev = _record(tk, ref, [pids[1]])
    assert ev["payload"]["prev_hash"] == agreed["payload"]["discussion_hash"]
    # 境界より後の合意は legacy にならない（同じ改変は改ざんとして検出される）
    tk2, ref2, pids2 = _agreed_proposal()
    with talks._connect(appmod.DB) as con:
        con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", ("合意後の追記", pids2[2]))
    agreed2 = [e for e in le.get_events(type_="purpose.agreed", db_path=appmod.DB)][-1]
    monkeypatch.setenv("POX_LEGACY_BOUNDARY_SEQ", str(agreed2["seq"]))
    assert redaction.verify_discussion(tk2, ref2, db_path=appmod.DB)["status"] == "legacy_unverified"
    monkeypatch.setenv("POX_LEGACY_BOUNDARY_SEQ", str(agreed2["seq"] - 1))
    assert redaction.verify_discussion(tk2, ref2, db_path=appmod.DB)["status"] == "tampered"
    # 継承の印は台帳に書かない
    assert not [e for e in le.get_events(db_path=appmod.DB) if "legacy" in e["type"]]


# ── 93-b: 伏せ字（定数）に置き換えた本文から再計算し、result_hash と一致する ─────────────
def test_t093b_constant_placeholder_reproduces_result_hash():
    tk, ref, pids = _agreed_proposal()
    with talks._connect(appmod.DB) as con:
        con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", ("【非表示】", pids[1]))
    ev = _record(tk, ref, [pids[1]])
    # 同じ手順（定数そのものに置換）を別の DB で独立にやり直しても同じ result_hash になる
    tk2, ref2, pids2 = _agreed_proposal()
    with talks._connect(appmod.DB) as con:
        con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", ("【非表示】", pids2[1]))
    assert redaction.current_discussion_hash(tk2, db_path=appmod.DB) == ev["payload"]["result_hash"]
    # 伏せ字が定数と違う（前後の空白・改行、別の文字列）と記録を拒否する
    for bad in (" 【非表示】", "【非表示】\n", "［削除済み］", ""):
        tk3, ref3, pids3 = _agreed_proposal()
        with talks._connect(appmod.DB) as con:
            con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", (bad, pids3[1]))
        with pytest.raises(ValueError):
            _record(tk3, ref3, [pids3[1]])
    # 行を消した場合も記録を拒否する（並びと投稿者が失われ再計算できない）
    tk4, ref4, pids4 = _agreed_proposal()
    with talks._connect(appmod.DB) as con:
        con.execute("DELETE FROM talk_posts WHERE post_id=%s", (pids4[1],))
    with pytest.raises(ValueError):
        _record(tk4, ref4, [pids4[1]])


# ── 93-c: POX_LEGACY_BOUNDARY_SEQ 未設定では起動しない（検証が静かに無効化されない）─────────
def test_t093c_boundary_unset_fails_fast(monkeypatch):
    import subprocess
    env = {k: v for k, v in os.environ.items() if k not in ("POX_LEGACY_BOUNDARY_SEQ", "POX_DEBUG")}
    env["POX_EMAIL_SALT"] = "x"
    r = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    assert "POX_LEGACY_BOUNDARY_SEQ" in (r.stderr + r.stdout)
    assert "docs/ledger_limits.md" in (r.stderr + r.stdout)
    # 不正値も同じ（フォールバックしない）
    env["POX_LEGACY_BOUNDARY_SEQ"] = "abc"
    assert subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120).returncode != 0
    # 設定すれば起動する
    env["POX_LEGACY_BOUNDARY_SEQ"] = "0"
    assert subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120).returncode == 0
    # 起動後に未設定になっても、検証は legacy なし／全件 legacy に倒れず例外になる
    tk, ref, pids = _agreed_proposal()
    monkeypatch.delenv("POX_LEGACY_BOUNDARY_SEQ", raising=False)
    with pytest.raises(redaction.BoundaryNotConfigured):
        redaction.verify_discussion(tk, ref, db_path=appmod.DB)


# ── 45C §1-2: 監査（境界以降の合意を総当たりで再計算し、不一致を一覧する）────────────
def test_t045c_audit_lists_mismatches_after_boundary():
    tk, ref, pids = _agreed_proposal()
    # 加入・参加・達成の合意もトークへ辿れる
    cid = _cli("u_alice").get(f"/api/talks/{tk}").get_json()["ctx"]
    lr = _cli("u_alice").post(f"/api/community/{cid}/projects/launch",
                              json={"title": "PJ", "purpose_ref": ref}).get_json()
    jtk = _cli("u_ext").post(f"/api/community/{cid}/talks",
                             json={"kind": "project_join", "title": "j",
                                   "target": {"intent_id": lr["intent_id"], "participant": "u_ext"}}).get_json()["talk_id"]
    _cli("u_ext").post(f"/api/talks/{jtk}/posts", json={"body": "参加したい"})
    _cli("u_alice").post(f"/api/talks/{jtk}/vote", json={"stance": "approve"})
    r = redaction.audit_after_boundary(0, db_path=appmod.DB)
    assert r["mismatches"] == [] and r["unresolved"] == [] and r["checked"] >= 2
    # 境界より後の合意の本文を改変すると不一致として出る（talk_id・保存値・再計算値・投稿数）
    with talks._connect(appmod.DB) as con:
        con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", ("改変", pids[0]))
    r = redaction.audit_after_boundary(0, db_path=appmod.DB)
    assert [m["talk_id"] for m in r["mismatches"]] == [tk]
    assert set(r["mismatches"][0]) >= {"talk_id", "stored", "recomputed", "posts"}
    # 境界をその合意より後にすると監査の対象外になる
    agreed_seq = [e for e in le.get_events(type_="purpose.agreed", db_path=appmod.DB)][-1]["seq"]
    assert redaction.audit_after_boundary(agreed_seq, db_path=appmod.DB)["mismatches"] == []
    # 監査は台帳に書かない
    n = len(le.get_events(db_path=appmod.DB)); redaction.audit_after_boundary(0, db_path=appmod.DB)
    assert len(le.get_events(db_path=appmod.DB)) == n


# ── 合意済みトークへの追記は現行 main で不可（45B §3 の再確認）──────────────────────
def test_t045b_agreed_talk_rejects_append():
    tk, ref, pids = _agreed_proposal()
    assert _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "追記"}).status_code == 409


# ── すり合わせ §3-6: 伏せ字の定数そのものは発言として投稿できない ──────────────────
def test_t093d_placeholder_cannot_be_posted():
    tk, ref, pids = _agreed_proposal()
    cid = _cli("u_alice").get(f"/api/talks/{tk}").get_json()["ctx"]
    open_tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                                   json={"kind": "proposal", "title": "Q", "target": {}}).get_json()["talk_id"]
    for body in ("【非表示】", "  【非表示】\n"):
        assert _cli("u_alice").post(f"/api/talks/{open_tk}/posts", json={"body": body}).status_code == 400
    assert _cli("u_alice").post(f"/api/talks/{open_tk}/posts", json={"body": "【非表示】について"}).status_code == 201
