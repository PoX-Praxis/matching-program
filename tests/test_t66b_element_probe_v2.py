"""指示書66（PR-66B）— 要素単位の接続の計測・第2段のスクリプト（テスト 373〜375）。

計測は Code のセッションで nomic-emb-v2 とクロスエンコーダを読み込んで実行する。ここでは埋め込みとクロスエンコーダを
決定論的な擬似の値に差し替え、registration_v2 の規則（上限の計算・作動点の格子と選び方・人物の作り方・b-1〜b-3 の定義）と
取り扱いの約束（生テキスト・根拠・門の申告値を書かない・本番に触れない）を確かめる。KAORU・PA の実際の文は置かない。
"""
import functools
import json
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

import embedding_service as es  # noqa: E402
import element_probe_v2 as E2  # noqa: E402

RAW_SECRET = "これは生テキストの合成の文で、結果に出てはならない。"
EVIDENCE = "根拠として引いた合成の言葉。"


def _doc():
    need = lambda t, m=False: {"文": t, "必須": m, "型": "関わり方"}  # noqa: E731
    return {"id": "k", "schema_version": "v5", "generator": "X", "_meta": {"source": "v5r4-A"},
            "purposes": [
                {"purpose_id": "p1", "向かう先": "", "手段": "合成の手段。",
                 "必要像": [need("合成の必要像その一をしてきた人。", True), need("合成の必要像その二を持つ人。", True),
                          need("合成の必要像その三に関心がある人。"), need("合成の必要像その四を広げてきた人。")],
                 "数値": {"gate_s": 0.6, "gate_u": 0.3}, "根拠": EVIDENCE},
                {"purpose_id": "p2", "向かう先": "", "手段": "合成の手段二。",
                 "必要像": [need("合成の必要像その五を探究してきた人。"), need("合成の必要像その六に関心がある人。")],
                 "数値": {"gate_s": 0.3, "gate_u": 0.3}, "根拠": EVIDENCE}],
            "与え像": [{"文": "合成の与え像その一の立場で関われる。", "型": "関わり方"}],
            "supporting_material": {"一行紹介": "x", "要約文": "y", "生テキスト": [RAW_SECRET, EVIDENCE]}}


def _small_sets():
    bg, el, el1 = E2.load_sets()
    sec = bg["sections"]
    small = {"offer": [x for x in sec["offer"] if x["split"] == "est"][:60] + [x for x in sec["offer"] if x["split"] == "val"][:202],
             "need": sec["need"][:50],
             "interest": [x for x in sec["interest"] if x["split"] == "est"][:30] + [x for x in sec["interest"] if x["split"] == "val"][:60]}
    return dict(bg, sections=small), el, el1


@functools.lru_cache(maxsize=None)
def _vec(text):
    return tuple(es._stub_encode(text)[:32])


def _rerank(pairs):
    return [sum(x * y for x, y in zip(_vec(a), _vec(b))) * 10 for a, b in pairs]


# ── 373 規則の計算（上限・作動点の格子）──────────────────────────────────────────────
def test_t373_rule_arithmetic_and_grids():
    assert abs(E2.FP_MAX - 0.002405) < 1e-6                        # π＝3%・Se 70%・precision 90%
    assert abs(E2.fp_max(0.01) - 0.000786) < 1e-6 and abs(E2.fp_max(0.10) - 0.008642) < 1e-6
    for x, want in ((0, 0.00125), (1, 0.00198), (2, 0.00262), (3, 0.00323), (4, 0.00381)):   # 約 2400 での読み方の目安
        assert abs(E2.cp_upper(x, 2400) - want) < 0.00001, x
    for x, want in ((0, 0.00249), (1, 0.00395)):                   # Q5: 半分の 1200
        assert abs(E2.cp_upper(x, 1200) - want) < 0.00001, x
    lo, hi = E2.cp_interval(17, 24)
    assert 0.48 < lo < 0.50 and 0.86 < hi < 0.88
    g, r, ce = E2.GRIDS["g"], E2.GRIDS["R"], E2.GRIDS["ce"]
    assert (len(g), g[0], g[-1]) == (41, -0.05, 0.15) and 0.0 in g
    assert (len(r), r[0], r[-1]) == (61, 0.6, 0.9) and 0.7 in r
    assert (len(ce), ce[0], ce[-1]) == (101, -2.0, 8.0) and 0.0 in ce
    assert E2.SCHEMES == ("R", "C2", "C2+b1", "C2+b2h", "C2+b2s", "C2+b3", "C2+b123", "C6m", "C6m+b1")
    assert E2.exp_precision(0.03, 0.7, 0.002405) > 0.899


# ── 374 人物の作り方と b-1〜b-3 の定義 ──────────────────────────────────────────────
def test_t374_persons_and_scheme_definitions():
    neg, pos_bg, left = E2.make_persons(2997, 36)
    assert len(neg) == 599 and left == 2 and all(len(p) == 5 for p in neg)
    flat = [j for p in neg for j in p]
    assert len(set(flat)) == len(flat) == 2995                      # 検証用の文を重ねずに 5 文ずつ
    assert len(pos_bg) == 36 and all(len(b) == 4 and len(set(b)) == 4 for b in pos_bg)
    assert E2.make_persons(2997, 36) == (neg, pos_bg, left)          # seed 6601・6602 で決まる
    # 3 要素（0・1 が役割、2 が関心型）× 4 文
    g = np.array([[0.80, 0.70, 0.66, 0.90],
                  [0.78, 0.72, 0.60, 0.50],
                  [0.70, 0.71, 0.90, 0.50]])
    q99n = np.array([0.70, 0.70, 0.70])
    q99s = np.array([0.75, 0.60, 0.60, 0.95])
    G = E2.Group(g, q99n, q99s)
    S = E2.comp_sets(["1-1", "1-2", "1-3"], [0, 1])
    assert S == [[0, 1], [0, 1], [0, 1, 2]]                          # 関心型の要素（参考値）は自分を足す
    assert E2.passes("C2", G, 0.0, S)[:, 0].tolist() == [True, True, False]
    assert E2.passes("C2+b1", G, 0.0, S)[:, 3].tolist() == [False, False, False]    # 相手の側の 99% 点を超えない
    assert E2.passes("C2+b2h", G, 0.0, S)[:, 0].tolist() == [True, False, False]    # 最も近い 1 要素にだけ
    k2 = E2.passes("C2+b2s", G, 0.075, S)[:, 0].tolist()                             # 2 要素が通る → 0.01 上げる
    assert k2 == [True, False, False]
    assert E2.passes("C2+b3", G, 0.0, S)[:, 1].tolist() == [False, True, False]      # 自分の他の役割の要素より近い
    assert E2.passes("R", G, 0.70, S)[:, 1].tolist() == [True, True, True]


# ── 375 流れ・出力・取り扱いの約束 ─────────────────────────────────────────────────
def test_t375_runs_outputs_and_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(E2, "DRAWS", 8)
    monkeypatch.setattr(E2, "LIST_N", (10,))
    kpath = tmp_path / "kaoru.json"
    kpath.write_text("```json\n" + json.dumps(_doc(), ensure_ascii=False) + "\n```", encoding="utf-8")
    spot = tmp_path / "spot.json"
    spot.write_text(json.dumps({"HN": [{"index": 48, "label": "判断できない"}]}), encoding="utf-8")
    out = tmp_path / "out"
    res = E2.main(["--kaoru", str(kpath), "--audit", str(tmp_path / "none.json"), "--labels", str(tmp_path / "none.json"),
                   "--spot", str(spot), "--out", str(out)], encode=es._stub_encode, scorer=_rerank, sets=_small_sets())
    md = (out / "element_probe_v2_result.md").read_text(encoding="utf-8")
    for h in ("## 1. 規則に照らした判定", "### 1-1. 足した C 群", "### 1-2. 文単位", "### 1-3. 要素ごとの偽陽性",
              "### 1-4. 期待 precision", "## 2. 曲線", "## 3. hard negative", "## 5. 較正曲線", "## 6. 要素の層の「該当なし」",
              "## 7. 上層", "## 8. 関心の欄", "## 9. 母集団非依存性・決定性"):
        assert h in md, h
    assert res["role_keys"] == ["1-1", "1-2", "1-4", "2-1"] and res["interest_keys"] == ["1-3", "2-2"]
    assert res["schemes"] == list(E2.SCHEMES) and res["sizes"]["neg_persons"] == 40
    for s in res["schemes"]:                                        # 報告する作動点の選び方
        c, curve = res["chosen"][s], res["curves"][s]
        ok = [r for r in curve if r["fp_upper"] <= E2.FP_MAX and r["se"] >= E2.SE_MIN]
        if ok:
            assert c["ok"] and c["delta"] == max(ok, key=lambda r: (r["se"], r["delta"]))["delta"]
        else:
            assert not c["ok"]
        assert [r["delta"] for r in curve] == E2.grid_of(s)
        assert all(r["trials"] == 40 * 4 for r in curve)             # 役割の 4 要素 × 負例の人物
    assert all(res["detail"][s]["hn"]["undecided"] == 1 for s in res["schemes"])   # 「判断できない」は数えない
    assert all(x["flips"] == 0 for k, x in res["indep"].items() if "対照" not in k)  # 固定の文集だけ → 母集団で変わらない
    assert {u for u in res["upper"]} == {"R 0.70", "C2 δ=0"}
    blob = md + (out / "element_probe_v2_result.json").read_text(encoding="utf-8")
    for k in (RAW_SECRET, EVIDENCE, "gate_s\": 0.6", "合成の必要像その一"):
        assert k not in blob, k                                    # 生テキスト・根拠・門の申告値・本人の文を書かない
    assert (out / "passing_background.md").exists()


def test_t375b_pinned_and_no_production():
    src = open(os.path.join(ROOT, "scripts", "element_probe_v2.py"), encoding="utf-8").read()
    for k in ("background_v2.json", "element_sets_v2.json", "registration_v2", "SEED_NEG, SEED_POS, SEED_DET, SEED_SPOT = 6601, 6602, 6603, 6604",
              "EP.RERANKERS[CE_NAME]", "transformers は 4.x に固定"):
        assert k in src, k
    assert E2.CE[1] == "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e" and E2.NOMIC[1] == "1066b6599d099fbb93dfcb64f9c37a7c9e503e85"
    for k in ("DATABASE_URL", "X-Anchor-Token", "pox-web.onrender.com", "get_connection", "ledger"):
        assert k not in src, k                                       # DB・台帳・トークン・本番を使わない
    tracked = subprocess.run(["git", "ls-files", "local"], cwd=ROOT, capture_output=True, text=True).stdout
    assert tracked.strip() == ""
