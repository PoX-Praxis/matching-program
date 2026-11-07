"""指示書66（PR-66C）— L（含意の判定）の計測のスクリプト（テスト 376〜378）。

計測は Code のセッションで llm-jp-3-1.8b-instruct（fp32）を読み込んで実行する。ここではモデルを擬似のスコアに差し替え、
registration_v2 §8 の約束（プロンプトとハッシュ・YES／NO の token id・役割の要素だけ・格子・同点の順・決定性の部分集合）と
取り扱い（本人の文・生テキスト・根拠を結果に書かない・本番に触れない）を確かめる。
"""
import functools
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import embedding_service as es  # noqa: E402
import element_probe_l as EL  # noqa: E402
import element_probe_v2 as E2  # noqa: E402
from test_t66b_element_probe_v2 import EVIDENCE, RAW_SECRET, _doc, _small_sets  # noqa: E402


@functools.lru_cache(maxsize=None)
def _v(text):
    return tuple(es._stub_encode(text)[:16])


def _fake_l(n, o):
    return sum(x * y for x, y in zip(_v(n), _v(o))) * 40


def _labels():
    return {"pairs": [{"id": "L01", "need_key": "1-1", "need": "合成の必要像その一をしてきた人。", "other": "合成の相手の文。",
                       "other_kind": "PA の現状（全文）", "provisional": "応えない", "final": None},
                      {"id": "L02", "need_key": "1-3", "need": "合成の必要像その三に関心がある人。", "other": "合成の相手の文。",
                       "other_kind": "PA の現状（全文）", "provisional": "応える", "final": None},
                      {"id": "L03", "need_key": "PA", "need": "合成の相手の必要像。", "other": "合成の与え像その一の立場で関われる。",
                       "other_kind": "KAORU の与え像", "provisional": "応える", "final": None}]}


# ── 376 プロンプト・token id・格子・同点の順 ───────────────────────────────────────────
def test_t376_prompt_tokens_and_grids():
    assert EL.prompt_sha() == EL.PROMPT_SHA256 == "d26a2c76734cb7911d01506e67f026a6c4bbaf846dab224c9a1b8c263afd4cec"
    reg = open(os.path.join(ROOT, "scripts", "probe_data", "registration_v2.md"), encoding="utf-8").read()
    block = next(b for b in re.findall(r"```\n(.*?)\n```", reg, flags=re.S) if b.startswith("次の2つの文を読んで"))
    assert block.replace("{必要像の文}", "{n}").replace("{相手の文}", "{o}") == EL.PROMPT   # 埋めたあとの文字列は登録と同じ
    assert hashlib.sha256(block.encode()).hexdigest() != EL.PROMPT_SHA256                  # 登録の値は {n}・{o} の形のハッシュ
    assert "d26a2c76734cb7911d01506e67f026a6c4bbaf846dab224c9a1b8c263afd4cec" in reg
    m, q = EL.MODELS["llm-jp"], EL.MODELS["qwen3"]
    assert (m["yes"], m["no"], m["revision"]) == (38891, 7937, "413b7929df092690b0f9449f3045baaf8deba0a5")
    assert (q["yes"], q["no"], q["chat_kwargs"]) == (14004, 8996, {"enable_thinking": False})
    assert (len(EL.GRID_L), EL.GRID_L[0], EL.GRID_L[-1]) == (81, -4.0, 4.0) and 0.0 in EL.GRID_L
    assert len(EL.GRID_D) == 41 and EL.DET_RUNS == 5
    curve = [{"fp": 0, "fp_upper": 0.001, "se": 0.75, "delta": 0.01, "delta_L": 0.5},
             {"fp": 0, "fp_upper": 0.001, "se": 0.75, "delta": 0.02, "delta_L": 0.1},
             {"fp": 0, "fp_upper": 0.001, "se": 0.75, "delta": 0.02, "delta_L": 0.3}]
    best, ok = E2.choose(curve, tie=lambda r: (r["delta"], r["delta_L"]))       # 同点は δ が大きいほう → δ_L が大きいほう
    assert ok and (best["delta"], best["delta_L"]) == (0.02, 0.3)


# ── 377 対（役割の 4 要素だけ・決定性の部分集合）──────────────────────────────────────────
def test_t377_pairs_role_only_and_det_subset(tmp_path):
    kpath = tmp_path / "kaoru.json"
    kpath.write_text("```json\n" + json.dumps(_doc(), ensure_ascii=False) + "\n```", encoding="utf-8")
    import element_probe as EP
    kaoru = EP.load_kaoru(str(kpath))
    sets = _small_sets()
    P = EL.build_pairs(kaoru, sets, _labels())
    c = P["counts"]
    assert (c["bg"], c["C"], c["HN"], c["labeled"]) == (202 * 4, 24, 60 * 4, 2)    # 関心型の要素の対は入らない
    interest = {"合成の必要像その三に関心がある人。", "合成の必要像その六に関心がある人。"}
    assert not any(n in interest for n, _ in P["full"])
    assert c["det"] == 2 + 240 + 200 and P == EL.build_pairs(kaoru, sets, _labels())   # seed 6603 で決まる
    assert set(P["det"]) <= set(P["full"])


# ── 378 流れ・出力・取り扱いの約束 ─────────────────────────────────────────────────
def test_t378_score_analyze_outputs(tmp_path):
    kpath = tmp_path / "kaoru.json"
    kpath.write_text("```json\n" + json.dumps(_doc(), ensure_ascii=False) + "\n```", encoding="utf-8")
    lpath = tmp_path / "labels.json"
    lpath.write_text(json.dumps(_labels(), ensure_ascii=False), encoding="utf-8")
    spot = tmp_path / "spot.json"
    spot.write_text(json.dumps({"HN": [{"index": 48, "label": "判断できない"}]}), encoding="utf-8")
    out = tmp_path / "out"
    base = ["--kaoru", str(kpath), "--labels", str(lpath), "--spot", str(spot), "--emb-cache", str(tmp_path / "emb"), "--out", str(out)]
    sets = _small_sets()
    st = EL.main(["--phase", "score"] + base, scorer=_fake_l, sets=sets)
    st2 = EL.main(["--phase", "score"] + base, scorer=lambda n, o: 1 / 0, sets=sets)  # 保存済みは計算しない（再開）
    assert st2["scores"] == st["scores"]
    for r in range(2, EL.DET_RUNS + 1):
        EL.main(["--phase", "score", "--set", "det", "--run", str(r)] + base, scorer=_fake_l, sets=sets)
    EL.main(["--phase", "score", "--model", "qwen3", "--set", "det", "--run", "1"] + base, scorer=lambda n, o: -_fake_l(n, o), sets=sets)
    EL.main(["--phase", "score", "--set", "inj"] + base, scorer=_fake_l, sets=sets)
    res = EL.main(["--phase", "analyze"] + base, encode=es._stub_encode, sets=sets)
    ref = res["ref"]                                              # 参考値（指示書66C 追補）
    assert [c["delta"] for c in ref["candidates"]] == EL.GRID_D and all(c["trials"] == 40 * 4 for c in ref["candidates"])
    assert all(0 <= c["k_mean"] <= c["k_max"] <= 5 * 4 for c in ref["candidates"])
    assert 0 <= ref["power"]["auc_sentence"] <= 1 and len(ref["power"]["calib"]) == 6
    inj = ref["injection"]
    assert inj["items"] == 25 and inj["pairs"] == 100 and set(inj["by_kind"]) == {"直接の指示", "役割の演技", "区切り記号からの脱出",
                                                                              "丁寧な依頼", "長い文の中に埋める"}
    assert res["role_keys"] == ["1-1", "1-2", "1-4", "2-1"] and res["combos"] == 41 * 81 and res["missing"] == 0
    assert [r["delta_L"] for r in res["L"]["curve"]] == EL.GRID_L
    assert res["L"]["zero"]["delta_L"] == 0.0 and all(r["delta_L"] == 0.0 for r in res["BL"]["curve_L0"])
    assert res["det"]["runs"] == 5 and res["det"]["max_abs_diff"] == 0.0 and res["det"]["agree_at_0"] == 1.0
    assert res["qwen"]["sign_agree_with_main"] < 0.5                              # 符号を反転した擬似の参考モデル
    assert res["L"]["detail"]["hn"]["undecided"] == 1 and res["L"]["detail"]["hn"]["a_own_n"] == 20
    md = (out / "element_probe_l_result.md").read_text(encoding="utf-8")
    for h in ("## 1. 規則に照らした判定", "## 2. L 単独の曲線", "## 3. C2+b-1+L", "## 4. 65-A のラベルつきの対", "## 5. 偏り・決定性",
              "### 6-1. 候補を残す線ごとの表", "### 6-2. L そのものの見分ける力", "### 6-3. 乗っ取りの試験",
              "2B 級のモデルについてのもの"):
        assert h in md, h
    blob = md + (out / "element_probe_l_result.json").read_text(encoding="utf-8")
    for k in (RAW_SECRET, EVIDENCE, "合成の必要像その一", "合成の相手の必要像"):
        assert k not in blob, k                                    # 本人・PA の文・生テキスト・根拠を書かない


def test_t378b_pinned_and_no_production():
    src = open(os.path.join(ROOT, "scripts", "element_probe_l.py"), encoding="utf-8").read()
    for k in ("llm-jp/llm-jp-3-1.8b-instruct", "dtype=torch.float32", "add_generation_prompt=True", "enable_thinking",
              "redact_text", "logits[m[\"yes\"]] - logits[m[\"no\"]]"):
        assert k in src, k
    for k in ("DATABASE_URL", "X-Anchor-Token", "pox-web.onrender.com", "get_connection", "ledger", ".generate("):
        assert k not in src, k                                       # 本番に触れない・生成させない
    tracked = subprocess.run(["git", "ls-files", "local"], cwd=ROOT, capture_output=True, text=True).stdout
    assert tracked.strip() == ""
