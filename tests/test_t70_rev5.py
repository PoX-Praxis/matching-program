"""指示書70 順2（確認 2026-11-11）— rev5 A の受け付け・c5・p4・§5・69 の表の運用（テスト 438〜443）。

438 §6-1: rev5 A の JSON を受け付け、種類・説明への使用を検証する（値の外は弾く）
439 §6-2: rev4 以前の JSON（B を含む）も今までどおり受け付け、種類は null（仮の規則）・説明への使用は null（使わない）
440 §6-8: プロンプトのファイルの sha256 が報告の値と一致し、登録画面がそのファイルから読み込む（A は rev5・B は rev4）
441 §6-9: 説明の生成が、構造化の 種類 と 説明への使用 を使う（69 の別の表は読まない）
442 §3: c5・p4 の内容ハッシュ（選んだ値と null を区別・前の版はその版の規則で確かめる・ベクトルの使い回し）
443 確認 2・4: 本番の件数だけを数える（v5r4-B の登録・69 の表の行数）・69 の表の行を消す（台帳は触れない）
（§6-3 B の確認の画面は、確認 2 で作らないことになった）
"""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import explanation_gen as G  # noqa: E402
import explanation_store as S  # noqa: E402
import ledger_events as le  # noqa: E402
import necessities as N  # noqa: E402
import ops70  # noqa: E402
import subject_ledger as SL  # noqa: E402
import v5  # noqa: E402
from embedding_config import MODEL_TAG  # noqa: E402
from test_t61_resonance_gate import _cli, _doc, _put, db  # noqa: E402,F401

PROMPT_A_SHA = "468c07c1d3715d9b02c62846725d6bf432402249fb37e5e023a48f642f37a9ef"   # rev5 A（確認 1 で 1 か所直した後）
PROMPT_B_SHA = "de080f0b0514b1b0e4f7f124177bfcfd6ba68e53b85fe6d4195a85093a2c6809"   # rev4 B のまま（確認 2）


def _r5(kinds=("補完", "共鳴"), bound=False):
    d = _doc(_meta={"source": "v5r5-A"})
    for p, k in zip(d["purposes"], kinds):
        p["種類"] = k
    d["説明への使用"] = {"縛られているもの": bound}
    d["現状"]["縛られているもの"] = "家業の店を離れられない"
    return d


def _draft(sid, doc):
    return _cli(sid).post("/v4/drafts", json={"raw_text": json.dumps(doc, ensure_ascii=False)})


def _register(sid, doc, handle):
    r = _draft(sid, doc)
    assert r.status_code == 201, r.get_json()
    c = _cli(sid).post(f"/v4/drafts/{r.get_json()['draft_id']}/confirm", json={"handle": handle, "privacy_policy_agreed": True})
    assert c.status_code in (200, 202) and c.get_json()["confirmed"] is True, c.get_json()
    return r.get_json()


def _rows(sid):
    with N._connect(appmod.DB) as con:
        return con.execute("SELECT necessity_id, canon_version, purpose_kind FROM necessities WHERE owner_ref=%s ORDER BY n",
                           (sid,)).fetchall()


# ── 438（§6-1）rev5 A を受け付け、種類・説明への使用を検証する ─────────────────────────────────────────
def test_t438_rev5a_accepted_and_validated(db):
    d = _register("u_a", _r5(bound=True), "kaoru_2026")
    assert [p["種類"] for p in d["payload"]["purposes"]] == ["補完", "共鳴"]
    assert d["payload"]["説明への使用"] == {"縛られているもの": True}
    doc = v5.get_doc("u_a", db_path=appmod.DB)
    assert [p["種類"] for p in doc["purposes"]] == ["補完", "共鳴"] and v5.bound_use_of(doc) is True
    assert [(c, k) for _, c, k in _rows("u_a")] == [("c5", "補完"), ("c5", "共鳴")]
    # 値の外は弾く（理由を 1 行で）
    bad = []
    d1 = _r5(); d1["purposes"][0]["種類"] = "その他"; bad.append(d1)
    d2 = _r5(); d2["purposes"][1].pop("種類"); bad.append(d2)
    d3 = _r5(); d3["purposes"][0]["種類"] = None; bad.append(d3)
    d4 = _r5(); d4.pop("説明への使用"); bad.append(d4)
    d5 = _r5(); d5["説明への使用"] = {"縛られているもの": "false"}; bad.append(d5)
    d6 = _r5(); d6["説明への使用"] = False; bad.append(d6)
    d7 = _r5(); d7["説明への使用"] = {"縛られているもの": None}; bad.append(d7)
    for b in bad:
        r = _draft("u_b", b)
        msg = r.get_json()["error"]
        assert r.status_code == 400 and ("種類は" in msg or "説明への使用は" in msg) and "\n" not in msg, msg
    # 必須は 0 でもよい（rev5: 0 も可）。3 つ以上は今までどおり弾く
    d8 = _r5(); d8["purposes"][1]["必要像"][0]["必須"] = False
    assert _draft("u_b", d8).status_code == 201
    d9 = _r5(); d9["purposes"][0]["必要像"] = [dict(x, 必須=True) for x in d9["purposes"][0]["必要像"] * 2][:3]
    assert _draft("u_b", d9).status_code == 400


# ── 439（§6-2）rev4 以前（B を含む）は今までどおり。種類は null（仮の規則）・説明への使用は null（使わない）────────────
@pytest.mark.parametrize("source", ["v5r4-B", "v5r4-A", "v5r3-A", None])
def test_t439_older_json_accepted_with_nulls(db, source):
    d = _doc() if source else {k: v for k, v in _doc().items() if k != "_meta"}
    if source:
        d["_meta"] = {"source": source}
    d["purposes"][0]["種類"] = "共鳴"                       # rev4 B には無い項目。入っていても使わない（null で書く）
    d["説明への使用"] = {"縛られているもの": True}
    d["現状"]["縛られているもの"] = "家業の店を離れられない"
    _register("u_a", d, "kaoru_2026")
    doc = v5.get_doc("u_a", db_path=appmod.DB)
    assert [p["種類"] for p in doc["purposes"]] == [None, None]
    assert doc["説明への使用"] == {"縛られているもの": None} and v5.bound_use_of(doc) is False
    assert [(c, k) for _, c, k in _rows("u_a")] == [("c5", None), ("c5", None)]
    ev = [e["payload"] for e in le.get_events(type_="profile.structured", db_path=appmod.DB)][-1]
    assert ev["canon_version"] == "p4"
    # 説明の入力: 種類は仮の規則で補い、縛られているものは使わない
    appmod_person = appmod._explanation_person
    appmod._public_since_passed, keep = (lambda at: True), appmod._public_since_passed
    try:
        person = appmod_person("u_a")
    finally:
        appmod._public_since_passed = keep
    assert [p["kind"] for p in person["purposes"]] == [None, None]
    ns = G.need_side(person, appmod._explanation_bound_allowed("u_a"))
    assert [p["種類"] for p in ns["block"]["purposes"]] == ["補完", "補完"] and ns["bound_used"] is False


# ── 440（§6-8）プロンプトの sha256 と、登録画面がそのファイルから読むこと ────────────────────────────────
def test_t440_prompt_files_and_register_page():
    import html
    import re
    a = open(os.path.join(ROOT, "docs", "prompts", "v5_A_dialogue.txt"), encoding="utf-8").read()
    b = open(os.path.join(ROOT, "docs", "prompts", "v5_B_selfwrite.txt"), encoding="utf-8").read()
    assert hashlib.sha256(a.encode("utf-8")).hexdigest() == PROMPT_A_SHA
    assert hashlib.sha256(b.encode("utf-8")).hexdigest() == PROMPT_B_SHA
    assert '"source": "v5r5-A"' in a and '"source": "v5r4-B"' in b
    assert '`{ "縛られているもの": true }` か `{ "縛られているもの": false }`' in a          # 確認 1 で直した 1 か所
    page = appmod.app.test_client().get("/register").get_data(as_text=True)
    for pid, text in (("promptA", a), ("promptB", b)):
        m = re.search(rf'<pre id="{pid}">(.*?)</pre>', page, re.S)
        assert html.unescape(m.group(1)) == text
    assert "v5r5-A" in v5.SOURCES and "v5r5-B" not in v5.SOURCES and v5.CHOICE_SOURCES == ("v5r5-A",)


# ── 441（§6-9）説明の生成が構造化の値を使う（69 の別の表は読まない）──────────────────────────────────────
def test_t441_generation_uses_structured_choices(db, monkeypatch):
    monkeypatch.setattr(appmod, "_public_since_passed", lambda at: True)
    # 公開プロフィールの現状の欄（このテストの世界では v4 の受付を省いているので、欄の文だけを置く）
    monkeypatch.setattr(appmod, "get_profile_view", lambda sid, db_path=None: {"state_bound": "家業の店を離れられない"})
    _register("u_a", _r5(kinds=("補完", "共鳴"), bound=True), "kaoru_2026")
    person = appmod._explanation_person("u_a")
    assert [p["kind"] for p in person["purposes"]] == ["補完", "共鳴"]
    assert appmod._explanation_bound_allowed("u_a") is True
    ns = G.need_side(person, appmod._explanation_bound_allowed("u_a"))
    assert [p["種類"] for p in ns["block"]["purposes"]] == ["補完", "共鳴"] and ns["bound_used"] is True
    assert "家業の店を離れられない" in json.dumps(ns["block"], ensure_ascii=False)
    # 答えが false なら使わない（構造化し直すと変わる）
    _register("u_a", _r5(kinds=("共鳴", "共鳴"), bound=False), "kaoru_2026")
    assert appmod._explanation_bound_allowed("u_a") is False
    assert [p["kind"] for p in appmod._explanation_person("u_a")["purposes"]] == ["共鳴", "共鳴"]
    # 69 の別の表を読む・書く道は無い（行があっても使わない）
    for name in ("bound_setting", "set_bound", "purpose_kinds", "set_purpose_kind"):
        assert not hasattr(S, name), name
    with S._connect(appmod.DB) as con:
        con.execute("INSERT INTO explanation_settings (subject_id, bound_allowed, bound_version, updated_at) "
                    "VALUES ('u_a', 1, 1, 'x')")
    assert appmod._explanation_bound_allowed("u_a") is False
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "_xs.purpose_kinds(" not in src and "_xs.bound_setting(" not in src


# ── 442（§3）c5・p4 の内容ハッシュ・前の版の確かめ・ベクトルの使い回し ─────────────────────────────────
def test_t442_c5_p4_hashes(db, monkeypatch):
    s = [{"文": "a", "必須": True, "型": "資源"}]
    h = lambda k, v="c5": N.compute_content_hash("", {"gate_s": 0.5, "gate_u": 0.3}, "ev", canon_version=v,  # noqa: E731
                                                  sentences=s, offer_hash="o", purpose_kind=k)
    assert len({h("補完"), h("共鳴"), h(None)}) == 3                         # 選んだ値と null を区別する
    assert h("補完", "c4") == h(None, "c4") == h("共鳴", "c4")                # c4 は種類を含まない（前の版はその規則のまま）
    base = {"will_text": "w", "v5": _r5(), "supporting_raw": _r5()["supporting_material"]}
    p4 = {b: SL.profile_content_hash({**base, "v5": {**base["v5"], "説明への使用": {"縛られているもの": b}}})
          for b in (True, False, None)}
    assert len(set(p4.values())) == 3
    assert SL.profile_content_hash(base, "p3") == SL.profile_content_hash(
        {**base, "v5": {**base["v5"], "説明への使用": {"縛られているもの": True}}}, "p3")   # p3 は含まない
    # 前の版（c4・p3）で書いた記録は、その版の規則のまま確かめられる
    monkeypatch.setattr(N, "V5_CANON", "c4")
    monkeypatch.setattr(SL, "PROFILE_CANON_V5", "p3")
    d4 = _doc(_meta={"source": "v5r4-A"})
    _register("u_a", d4, "kaoru_2026")
    old = _rows("u_a")
    assert [c for _, c, _ in old] == ["c4", "c4"]
    ev3 = [e["payload"] for e in le.get_events(type_="profile.structured", db_path=appmod.DB)][-1]
    assert ev3["canon_version"] == "p3"
    monkeypatch.setattr(N, "V5_CANON", "c5")
    monkeypatch.setattr(SL, "PROFILE_CANON_V5", "p4")
    assert all(N.verify_content_hash(i, db_path=appmod.DB)["ok"] for i, _, _ in old)
    # 同じ文の目的で、本人が種類を選んだ（rev5 A）→ c5 の新しい版。ベクトルは前の版のものを紐づけ直す（埋め込みを呼ばない）
    for i, _, _ in old:
        _put("necessity", i, [[1.0, 0.0, 0.0]] * len(v5.necessity_body(i, db_path=appmod.DB)["sentences"]))
    monkeypatch.setattr(N, "_default_embed", lambda *a, **k: (_ for _ in ()).throw(AssertionError("埋め込みを呼ばない")))
    d5 = _r5()
    d5["現状"]["縛られているもの"] = ""
    _register("u_a", d5, "kaoru_2026")
    new = _rows("u_a")[2:]
    assert [(c, k) for _, c, k in new] == [("c5", "補完"), ("c5", "共鳴")]
    assert all(N.verify_content_hash(i, db_path=appmod.DB)["ok"] for i, _, _ in new)
    for (o, _, _), (n, _, _) in zip(old, new):
        assert v5.get_sentence_vectors("necessity", n, MODEL_TAG, db_path=appmod.DB) == \
            v5.get_sentence_vectors("necessity", o, MODEL_TAG, db_path=appmod.DB)
    # 同じ内容を rev4 で登録し直しても（種類は null）、c5 の前の版と同じなら版を足さない
    _register("u_b", _doc(_meta={"source": "v5r4-B"}), "pa_2026")
    n_b = len(_rows("u_b"))
    _register("u_b", _doc(_meta={"source": "v5r4-B"}), "pa_2026")
    assert len(_rows("u_b")) == n_b


# ── 443（確認 2・4）件数だけを数える・69 の表の行を消す（台帳は触れない）────────────────────────────────────
def test_t443_ops_counts_and_clear(db):
    _register("u_a", _doc(_meta={"source": "v5r4-B"}), "kaoru_2026")
    _register("u_a", _doc(_meta={"source": "v5r4-B"}), "kaoru_2026")
    _register("u_b", _r5(), "pa_2026")
    _draft("u_c", _doc(_meta={"source": "v5r4-B"}))                            # 確定していない下書きは数えない
    assert ops70.source_counts("v5r4-B", db_path=appmod.DB) == {"registrations": 2, "people": 1, "current": 1}
    assert ops70.source_counts("v5r5-A", db_path=appmod.DB) == {"registrations": 1, "people": 1, "current": 1}
    with S._connect(appmod.DB) as con:
        con.execute("INSERT INTO purpose_kinds (subject_id, purpose_id, kind, updated_at) VALUES ('u_a','p','共鳴','x')")
        con.execute("INSERT INTO explanation_settings (subject_id, bound_allowed, bound_version, updated_at) VALUES ('u_a',1,1,'x')")
    events = len(le.get_events(db_path=appmod.DB))
    lines = ops70.startup_lines({}, db_path=appmod.DB)                         # 既定は数えるだけ
    assert lines == ["[ops-70] v5r4-B の登録=2回・1人（いまの登録が v5r4-B=1人）",
                     "[ops-70] 69 の表の行数 purpose_kinds=1・explanation_settings=1"]
    assert ops70.legacy_69_counts(appmod.DB) == {"purpose_kinds": 1, "explanation_settings": 1}
    lines = ops70.startup_lines({ops70.CLEAR_ENV: "1"}, db_path=appmod.DB)
    assert lines[-1].startswith("[ops-70] 69 の表の行を消しました purpose_kinds=1・explanation_settings=1")
    assert ops70.legacy_69_counts(appmod.DB) == {"purpose_kinds": 0, "explanation_settings": 0}
    assert len(le.get_events(db_path=appmod.DB)) == events and le.verify_chain(db_path=appmod.DB)["ok"] is True
    blob = "\n".join(lines)
    for secret in ("u_a", "u_b", "kaoru", "共鳴", "家業"):                       # 件数だけ（中身は出さない）
        assert secret not in blob
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "ops70.startup_lines(os.environ" in src
