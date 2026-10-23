"""指示書65-A — 要素単位の接続の計測スクリプト（テスト 330〜332）。

計測は Code のセッションで nomic-emb-v2 とクロスエンコーダを読み込んで実行する。ここでは埋め込みとクロスエンコーダを
決定論的な擬似の値に差し替え、流れ・出力・取り扱いの約束（数値をラベル一覧に載せない・生テキストを書かない・
最終ラベルを引き継ぐ・背景は固定の文集だけ）を確かめる。KAORU・PA の実際の文はテストにも置かない（合成の文で代える）。
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

import embedding_service as es  # noqa: E402
import element_probe as EP  # noqa: E402

RAW_SECRET = "これは生テキストの合成の文で、結果に出てはならない。"
EVIDENCE = "根拠として引いた合成の言葉。"


def _doc():
    return {"id": "k", "schema_version": "v5", "generator": "X", "_meta": {"source": "v5r4-A"},
            "purposes": [
                {"purpose_id": "p1", "向かう先": "", "手段": "合成の手段。",
                 "必要像": [{"文": "合成の必要像その一をしてきた人。", "必須": True, "型": "関わり方"},
                          {"文": "合成の必要像その二を持つ人。", "必須": True, "型": "資源"},
                          {"文": "合成の必要像その三に関心がある人。", "必須": False, "型": "関心"}],
                 "数値": {"gate_s": 0.6, "gate_u": 0.3}, "根拠": EVIDENCE},
                {"purpose_id": "p2", "向かう先": "", "手段": "合成の手段二。",
                 "必要像": [{"文": "合成の必要像その四を探究してきた人。", "必須": False, "型": "関わり方"},
                          {"文": "合成の必要像その五に関心がある人。", "必須": False, "型": "関心"}],
                 "数値": {"gate_s": 0.3, "gate_u": 0.3}, "根拠": EVIDENCE}],
            "与え像": [{"文": "合成の与え像その一の立場で関われる。", "型": "関わり方"},
                    {"文": "合成の与え像その二を手伝える。", "型": "資源"}],
            "supporting_material": {"一行紹介": "x", "要約文": "y", "生テキスト": [RAW_SECRET, EVIDENCE]}}


def _audit(emb):
    slots = {"持っているもの": "合成の持っているもの。", "できること_型": "合成の型。", "縛られているもの": "合成の制約。"}
    from embedding_service import state_concat
    text = state_concat({"state_have": slots["持っているもの"], "state_can_type": slots["できること_型"],
                         "state_bound": slots["縛られているもの"]})
    needs = [n["文"] for p in _doc()["purposes"] for n in p["必要像"]]
    return {"v5": {"sentences": [
        {"direction": "a_to_b", "sentences": [
            {"need": n, "best": " / ".join(slots.values()), "best_source": "現状（全文）",
             "display_field": {"field": "できること（型）", "text": "合成の型。"},
             "g": round(EP.DP.g_of(emb(n, "query"), emb(text, "passage")), 2)} for n in needs]},
        {"direction": "b_to_a", "sentences": [{"need": "合成の相手の必要像を検証できる人。", "g": 0.5,
                                               "best": "合成の与え像その一の立場で関われる。", "best_source": "与え像"}]}]}}


def _rerank(pairs):
    return [sum(es._stub_encode(a)[i] * es._stub_encode(b)[i] for i in range(32)) for a, b in pairs]


def _run(tmp_path, monkeypatch, labels=None):
    monkeypatch.setattr(EP, "DRAWS", 8)
    monkeypatch.setattr(EP, "LIST_N", (10,))
    kpath = tmp_path / "kaoru.json"
    kpath.write_text("```json\n" + json.dumps(_doc(), ensure_ascii=False) + "\n```", encoding="utf-8")
    emb, _, _ = EP.DP.make_embedder(encode=es._stub_encode, strict=False)
    apath = tmp_path / "audit.json"
    apath.write_text(json.dumps(_audit(emb), ensure_ascii=False), encoding="utf-8")
    lpath = tmp_path / "labels.json"
    if labels:
        lpath.write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    res = EP.main(["--kaoru", str(kpath), "--audit", str(apath), "--labels", str(lpath), "--out", str(out)],
                  encode=es._stub_encode, rerankers={"C6": _rerank})
    return res, out, lpath


# ── 330 流れと出力（擬似の値で最後まで動く・規則の判定・ラベル一覧に数値を載せない・最終ラベルを引き継ぐ）────────
@pytest.mark.slow           # テストの中では約 7 分かかる（単独の実行では約 15 秒。原因は未特定）。`pytest --run-slow` で実行する
def test_t330_probe_runs_rules_and_labels(tmp_path, monkeypatch):
    res, out, lpath = _run(tmp_path, monkeypatch)
    md = (out / "element_probe_result.md").read_text(encoding="utf-8")
    assert "仮ラベルでの結果" in md
    for h in ("## 1. 背景の分布", "## 2. 要素 1 組あたりの偽陽性・取りこぼし", "### 較正曲線", "## 3. 必須の数ごとの目的の成立",
              "## 4. 母集団非依存性", "## 5. 「該当なし」", "## 6. 下層の一覧の長さ", "## 7. 方向B（暫定）・v5 × v5",
              "## 関心の欄（(i) 対称・(ii) query × document）", "## 規則に照らした判定", "## 8. 要素の対の一覧"):
        assert h in md, h
    assert res["schemes"] == ["R", "Rg", "C2", "C5", "C6"]
    assert [n["key"] for n in res["needs"] if n["interest"]] == ["1-3", "2-2"]
    assert res["indep"]["C2"]["flips"] == 0 and res["indep"]["C6"]["flips"] == 0     # 固定の文集だけ → 母集団で変わらない
    assert {u["musts"] for u in res["upper"]["R"] if u["purpose"] == 1} == {0, 1, 2}
    v = EP.verdicts(res)
    assert set(v["lower"]) >= {"C2/all", "C2/role", "C5/all", "C6/all"} and set(v["interest"]) == {"i", "ii"}
    blob = md + (out / "element_probe_result.json").read_text(encoding="utf-8") + lpath.read_text(encoding="utf-8")
    for k in (RAW_SECRET, EVIDENCE, "gate_s\": 0.6"):
        assert k not in blob, k                                    # 生テキスト・根拠・門の申告値を書かない
    pairs = json.loads(lpath.read_text(encoding="utf-8"))["pairs"]
    assert 20 <= len(pairs) and all(set(p) == {"id", "need_key", "need", "other_kind", "other", "provisional", "note", "final"}
                                    for p in pairs)                # 一覧に数値の欄は無い
    assert {p["provisional"] for p in pairs} <= {"応える", "応えない", "判断できない", "未定"}
    # 最終ラベルを書くと次の実行で引き継ぎ、見出しが変わる
    pairs[0]["final"] = "応えない"
    # 一覧の対は最終ラベルで数える（判断できないは数えない・未定だった対も「応えない」なら応えない側に入る）
    pa = {p["need_key"]: p for p in pairs if p["other_kind"].startswith("PA の現状")}
    pa["1-3"]["final"] = "応えない"                                   # 仮ラベルは「未定」
    pa["2-2"]["final"] = "判断できない"                               # 仮ラベルは「応える」
    res2, out2, _ = _run(tmp_path, monkeypatch, labels={"pairs": pairs})
    assert res2["has_final"] and res2["labels"][0]["final"] == "応えない"
    md2 = (out2 / "element_probe_result.md").read_text(encoding="utf-8")
    assert "最終ラベルでの結果" in md2 and f"判断できない（数えない）: {res2['undecided']} 対" in md2
    e1, e2 = res["elem"]["R"]["all"], res2["elem"]["R"]["all"]
    assert (e1["D_neg"], e1["D_resp"]) == (2, 2) and (e2["D_neg"], e2["D_resp"]) == (3, 1) and e2["undecided"] == 1


# ── 331 背景の文集と群（版・件数・出どころ・ライセンス）───────────────────────────────────
def test_t331_background_and_sets_versioned_with_provenance():
    bg, el, code, people = EP.load_sets()
    assert bg["version"] == "bg-v1" and "偶数＝推定用" in bg["split"]
    off, it = bg["sections"]["offer"], bg["sections"]["interest"]
    assert len(off) == 300 and len({x["domain"] for x in off}) == 30 and len({x["text"] for x in off}) == 300
    assert len(it) == 150 and len({x["domain"] for x in it}) == 30 and all(x["text"].endswith("関心がある。") for x in it)
    for end in ("してきた。", "できる。", "の立場で関われる。", "を手伝える。"):
        assert sum(x["text"].endswith(end) for x in off) >= 40, end
    pv = bg["provenance"]
    assert "新しい会話" in pv["how"] and "第三者の著作物を含まない" in pv["license"]
    assert "300 文" in pv["offer"]["prompt"] and "150 文" in pv["interest"]["prompt"]
    assert el["version"] == "el-v1" and len(el["A"]) == 20 and len(el["B"]) == 10 and len(el["C"]) == 12
    assert {c["target"] for c in el["C"]} == {"1-1", "1-2", "1-3", "1-4", "2-1", "2-2"}
    assert len(el["E"]["match"]) == 10 and len(el["E"]["nonmatch"]) == 10
    assert {k: len(v) for k, v in el["E"]["for_need"].items()} == {"1-3": 3, "2-2": 3}
    for k in ("C", "E"):                                            # 渡した本人の文は記録に残さない
        assert "リポジトリに入れない" in el["provenance"][k]["prompt"]
    assert all(EP.plain_style(c["text"]) != c["text"] for c in el["C"])   # 常体の版はすべての文末を直せる
    assert EP.split_halves(list(range(6))) == ([0, 2, 4], [1, 3, 5])


# ── 332 本番と同じ経路・版の固定・本番に触れない・手元のファイルをコミットしない ─────────────────────────
def test_t332_same_paths_pinned_no_production():
    src = open(os.path.join(ROOT, "scripts", "element_probe.py"), encoding="utf-8").read()
    for k in ("DP.make_embedder(", "from necessities import _default_embed", '"1066b6599d099fbb93dfcb64f9c37a7c9e503e85"',
              '"d7462bce0b065e0624028b412693dec055d719a0"', '"953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"',
              "transformers は 4.x に固定", "background_v1.json", "np.quantile"):
        assert k in src, k
    for k in ("DATABASE_URL", "X-Anchor-Token", "pox-web.onrender.com", "get_connection", "ledger"):
        assert k not in src, k                                       # DB・台帳・トークン・本番を使わない
    assert EP.Q == 0.99 and EP.R_TH == 0.70 and EP.POOL_SIZES == (10, 100)
    req = open(os.path.join(ROOT, "scripts", "requirements-probe.txt"), encoding="utf-8").read()
    assert "transformers>=4.49,<5" in req
    tracked = subprocess.run(["git", "ls-files", "local"], cwd=ROOT, capture_output=True, text=True).stdout
    assert tracked.strip() == ""
