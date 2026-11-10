"""指示書69 順3 の確認（2026-11-10）— 取り込み分を本人の必要像の版に結びつける・subject の実在・local は明示・66C の道具のアーカイブ
（テスト 431〜433）。合成データは架空の人（world2）だけ。"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import explanation_gen as G  # noqa: E402
import explanation_store as S  # noqa: E402
import v5  # noqa: E402
from test_t61_resonance_gate import _cli, db  # noqa: E402,F401
from test_t65b2_two_layer import world2  # noqa: E402,F401
from test_t68_screens import _card, _state, xs  # noqa: E402,F401

TOK = "t" * 40


def _imported_doc():
    """取り込み分と同じ形（目的の id はファイルのもの p1）で、me の実際の必要像の文を引用した向き。"""
    live = v5.live_necessities_v5("me", db_path=appmod.DB)[0]
    needs = [x["文"] for x in live["sentences"]]
    offer = v5.latest_offer("c_full", db_path=appmod.DB)["sentences"][0]["文"]
    d = {"purposes": [{"ref": "N.purpose.p1", "order": 0, "number": 1, "title": "事業の立ち上げ", "kind": "補完",
                       "status": "explained",
                       "meaning": {"text": "{O}は、試作として形にする立場で関われる人で、{N}の事業の立ち上げに関われる可能性があります。",
                                   "cites": [0]},
                       "elements": [{"need_ref": "N.need.p1.1-1", "must": True, "need_quote": needs[0][:12],
                                     "offer_ref": "O.offer.1", "offer_quote": "試作として形にする立場で関われる", "relation": "R2"}],
                       "unknown": [], "missing_must": []}],
         "citations": [{"ref": f"N.need.p1.1-{i + 1}", "text": t} for i, t in enumerate(needs)] + [{"ref": "O.offer.1", "text": offer}],
         "as_of": {"need": {"profile": "kaoru-local", "necessity": "kaoru-local"}, "offer": {"profile": "x", "necessity": None}},
         "generation": {"prompt1_sha": None, "prompt2_sha": None, "model": "fixed", "revision": None, "decoding": {"temperature": 0}}}
    return {"format": S.FILE_FORMAT, "people": {"a": {"subject": "me"}, "b": {"subject": "c_full"}},
            "directions": [{"need": "a", "offer": "b", **d}]}, live


def _post(doc):
    return appmod.app.test_client().post("/ops/explanations/import", data=json.dumps(doc, ensure_ascii=False).encode(),
                                         headers={"X-Ops-Import-Token": TOK, "Content-Type": "application/json"})


# ── 431 取り込み分を、本人の必要像の版（c4）に結びつける・実在しない subject は保存しない ─────────────────────────
def test_t431_imported_binds_to_live_necessity(xs, monkeypatch):
    monkeypatch.setenv("POX_OPS_IMPORT_TOKEN", TOK)
    with S._connect(appmod.DB) as con:
        con.execute("DELETE FROM explanation_directions")
    doc, live = _imported_doc()
    d = _post(doc).get_json()
    assert d["saved"] == 1 and d["directions"][0]["bound"] == "1/1"            # 取り込みの応答で結びつきが分かる
    # 申請 → 承認 → explanation.agreed の a1 の必要の側が、本人の目的の id と c4 になる（null にならない）
    c = _card("me", "c_full")
    assert _cli("me").post("/approve", json={"to_id": "c_full", "explanation_digest": c["card_digest"]}).status_code == 200
    assert _cli("c_full").post("/approve", json={"to_id": "me", "explanation_confirmed": True}).status_code == 200
    assert _state("me", "c_full") == "connected"
    row = S.render_by_id(S.approved_render("me", "c_full", db_path=appmod.DB)["id"], db_path=appmod.DB)
    a1 = json.loads(row["a1_json"])
    assert a1["purposes"][0]["purpose_id"] == live["purpose_id"] != "p1"
    assert a1["purposes"][0]["necessity_c4"] == live["content_hash"] and live["content_hash"]
    import explanation_agreement as XA
    assert XA.pair_digest(a1)[0] == row["pair_digest"]                          # 残した a1 で確かめ直せる
    assert json.loads(row["card_json"])["generation"]["model"] == "fixed"       # 手作りであることは残る
    # 実在しない subject の向きは保存しない（dropped の理由に出す）
    bad = json.loads(json.dumps(doc))
    bad["people"]["b"]["subject"] = "u_does_not_exist"
    d = _post(bad).get_json()
    assert d["saved"] == 0 and d["directions"] == [{"index": 0, "ok": False, "why": "subject が存在しない"}]
    # 文が合わなければ結びつけない（null のまま。応答で 0/1）
    off = json.loads(json.dumps(doc))
    off["directions"][0]["citations"][0]["text"] = "別の文。"
    off["directions"][0]["purposes"][0]["elements"][0]["need_quote"] = "別の文"
    assert _post(off).get_json()["directions"][0]["bound"] == "0/1"


def test_t431b_v4_person_binds_by_necessity_text(monkeypatch):
    """v4 の人（目的が無い）は、生きている必要像の本文に引用元の文が含まれる版に結びつける。"""
    import necessities as N
    monkeypatch.setattr(v5, "live_necessities_v5", lambda sid, db_path=None: [])
    monkeypatch.setattr(N, "get_live_necessities", lambda sid, db_path=None: [
        {"necessity_id": "n_old", "necessity_text": "古い版の本文。", "content_hash": "h_old"},
        {"necessity_id": "n_new", "necessity_text": "前提と論理で検証できる人。構造を読み解く人。", "content_hash": "h_new"}])
    b = appmod._bind_necessities("pa_like", {"p1": ["前提と論理で検証できる人。"]})
    assert b == {"p1": {"purpose_id": "p1", "necessity_c4": "h_new"}}
    assert appmod._bind_necessities("pa_like", {"p1": ["どこにも無い文。"]}) == {}


# ── 432 local のモデルは、明示して指定しないと動かない（既定のモデルを持たない）─────────────────────────────────
def test_t432_local_requires_explicit_model(monkeypatch):
    monkeypatch.delenv("POX_EXPLANATION_LOCAL_MODEL", raising=False)
    monkeypatch.delenv("POX_EXPLANATION_LOCAL_REVISION", raising=False)
    with pytest.raises(ValueError):
        G.LocalBackend()
    monkeypatch.setenv("POX_EXPLANATION_BACKEND", "local")
    monkeypatch.delenv("RENDER", raising=False)
    with pytest.raises(SystemExit):
        G.startup_check()
    monkeypatch.setenv("POX_EXPLANATION_LOCAL_MODEL", "some/model")
    with pytest.raises(SystemExit):
        G.startup_check()                                                       # revision も要る
    monkeypatch.setenv("POX_EXPLANATION_LOCAL_REVISION", "abc123")
    assert G.startup_check() == {"backend": "local", "generates": True}
    b = G.LocalBackend()
    assert (b.model, b.revision) == ("some/model", "abc123") and b._lm is None  # 指定しただけでは読み込まない
    src = open(os.path.join(ROOT, "src", "explanation_gen.py"), encoding="utf-8").read()
    assert "LOCAL_DEFAULT" not in src and "llm-jp" not in src


# ── 433 66C の道具はアーカイブに移し、CI のテストから外す ───────────────────────────────────────
def test_t433_66c_archived():
    for f in ("scripts/element_probe_l.py", "scripts/l_token_check.py", "tests/test_t66c_element_probe_l.py"):
        assert not os.path.exists(os.path.join(ROOT, f)), f
    for f in ("archive/66c/scripts/element_probe_l.py", "archive/66c/scripts/l_token_check.py",
              "archive/66c/tests/test_t66c_element_probe_l.py", "archive/66c/README.md"):
        assert os.path.exists(os.path.join(ROOT, f)), f
    conf = open(os.path.join(ROOT, "conftest.py"), encoding="utf-8").read()
    assert 'collect_ignore_glob = ["archive/*"]' in conf
