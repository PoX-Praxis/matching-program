"""指示書64 §3 — 照合の見分ける力の調査スクリプト（テスト 323〜325）。

スクリプトは本人が手元で nomic-emb-v2 を読み込んで実行する（このセッションからはモデルを取得できない）。
ここでは埋め込みを決定論的な擬似ベクトル（stub）に差し替え、流れ・出力・取り扱いの約束だけを確かめる。
KAORU・PA の実際の文はテストにも置かない（合成の文で代える）。
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

import embedding_service as es  # noqa: E402
import discrimination_probe as P  # noqa: E402

RAW_SECRET = "これは生テキストの合成の文で、結果に出てはならない。"
EVIDENCE = "根拠として引いた合成の言葉。"


def _kaoru_doc():
    return {"id": "k", "schema_version": "v5", "generator": "X", "_meta": {"source": "v5r4-A"},
            "purposes": [
                {"purpose_id": "p1", "向かう先": "合成の向かう先その一。", "手段": "合成の手段。",
                 "必要像": [{"文": "合成の必要像その一。", "必須": True, "型": "関わり方"},
                          {"文": "合成の必要像その二。", "必須": False, "型": "資源"}],
                 "数値": {"gate_s": 0.6, "gate_u": 0.3}, "根拠": EVIDENCE},
                {"purpose_id": "p2", "向かう先": "合成の向かう先その二。", "手段": "合成の手段二。",
                 "必要像": [{"文": "合成の必要像その三。", "必須": False, "型": "関心"}],
                 "数値": {"gate_s": 0.0, "gate_u": 0.3}, "根拠": EVIDENCE}],
            "与え像": [{"文": "合成の与え像その一。", "型": "関わり方"}],
            "現状": {"持っているもの": "a", "できること_型": "b", "縛られているもの": "c", "未分類": ""},
            "supporting_material": {"一行紹介": "x", "要約文": "y", "生テキスト": [RAW_SECRET, EVIDENCE]}}


def _setup(tmp_path):
    kpath = tmp_path / "kaoru_v5.json"
    kpath.write_text("```json\n" + json.dumps(_kaoru_doc(), ensure_ascii=False) + "\n```", encoding="utf-8")
    emb, emb_state, _ = P.make_embedder(encode=es._stub_encode, strict=False)
    slots = {"持っているもの": "合成の持っているもの。", "できること_型": "合成の型。", "縛られているもの": "合成の制約。"}
    sv = emb_state(slots)
    needs = ["合成の必要像その一。", "合成の必要像その二。"]
    audit = {"v5": {"sentences": [{"direction": "a_to_b", "sentences": [
        {"need": n, "best": " / ".join(slots.values()), "best_source": "現状（全文）",
         "display_field": {"field": "できること（型）", "text": "合成の型。"},
         "g": round(P.g_of(emb(n, "query"), sv), 2)} for n in needs]}]}}
    apath = tmp_path / "audit.json"
    apath.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    return kpath, apath


# ── 323 流れと出力（擬似ベクトルで最後まで動く・監査の g を再現する欄の割り当てを選ぶ）──────────────
def test_t323_probe_runs_and_writes_results(tmp_path):
    kpath, apath = _setup(tmp_path)
    out = tmp_path / "probe"
    res = P.main(["--kaoru", str(kpath), "--audit", str(apath), "--pa-state", str(tmp_path / "none.json"),
                  "--out", str(out)], encode=es._stub_encode)
    md = (out / "probe_result.md").read_text(encoding="utf-8")
    for h in ("## (2) 群ごとの g", "### D 実際の相手", "## (3) 何人に通るか", "## (4) 文単位と全文どうし"):
        assert h in md, h
    assert res["repro"]["matched"] == res["repro"]["rows"] == 2
    assert res["repro"]["slots_used"] == ["持っているもの", "できること_型", "縛られているもの"]
    assert {n["key"] for n in res["needs"]} == {"1-1", "1-2", "2-1"}
    assert set(res["dist"]) == {"A", "B", "C"} and res["dist"]["A"]["_all"]["n"] == 20 * 3
    assert [t["row"] for t in res["people"]["table"]] == ["KAORU の目的 1 → 相手", "KAORU の目的 2 → 相手",
                                                         "相手 → KAORU", "互いに埋める"]
    blob = md + (out / "probe_result.json").read_text(encoding="utf-8")
    for k in (RAW_SECRET, EVIDENCE, "生テキスト\"", "gate_s\": 0.6"):
        assert k not in blob, k                                    # 生テキスト・根拠・門の申告値を書かない


# ── 324 架空の文と人（件数・種類の内訳）──────────────────────────────────────────────
def test_t324_fictional_data_shape():
    sents, people = P.load_data()
    assert len(sents["A"]) == 20 and len(sents["B"]) == 10 and len(sents["C"]) == 10
    assert all(c["target"] for c in sents["C"])
    assert 15 <= len(people) <= 20
    types = [p["type"] for p in people]
    assert 2 <= types.count("fit") <= 3 and 3 <= types.count("topic") <= 4
    for p in people:
        for q in p["purposes"]:
            assert 0 <= q["gate_s"] <= 1 and 0 <= q["gate_u"] <= 1 and q["必要像"]


# ── 325 本番と同じ経路・本番に触れない・手元のファイルをコミットしない ─────────────────────────────
def test_t325_same_paths_no_production_local_ignored():
    src = open(os.path.join(ROOT, "scripts", "discrimination_probe.py"), encoding="utf-8").read()
    for k in ("from necessities import _default_embed", "es.state_concat(prof)", "judge_direction(p, ps)",
              'os.environ["POX_EMBED_MODEL_TAG"] = MODEL_TAG', 'MODEL_TAG = "nomic-emb-v2"'):
        assert k in src, k
    for k in ("DATABASE_URL", "X-Anchor-Token", "pox-web.onrender.com", "get_connection"):
        assert k not in src, k                                       # DB・トークン・本番を使わない
    assert "local/" in open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read().split()
    tracked = subprocess.run(["git", "ls-files", "local"], cwd=ROOT, capture_output=True, text=True).stdout
    assert tracked.strip() == ""
