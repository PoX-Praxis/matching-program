"""指示書66D §3-1 — 書き直し・C 群・hard negative の指示と忠実性の組み方を、計測の前に固定する。"""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "scripts", "probe_data", "rewrite_66d_v1.json")


def _doc():
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)


# ── 386 指示の全文の sha256 と、§1-1（必須は 1〜2・目的の種類）・C 群 6 文・hard negative a・忠実性の組み方 ─────
def test_t386_rewrite_prompts_fixed_before_measurement():
    d = _doc()
    for k in ("rewrite", "c_group", "hard_negative_a"):
        assert hashlib.sha256(d[k]["prompt"].encode("utf-8")).hexdigest() == d[k]["sha256"], k
    rw = d["rewrite"]["prompt"]
    assert "1 つ以上・2 つ以下「必須」にする。必須には「関心」の型を置かない。" in rw          # §1-1
    assert "「補完」（足りない役割や資源を持つ相手を求める）か「共鳴」（同じ関心を持つ相手を求める）" in rw
    assert '"種類": "補完"' in rw and d["rewrite"]["runs"] == 1
    for f in ("{一行紹介}", "{目的の一覧}", "{持っているもの}", "{できること_型}", "{縛られているもの}", "{未分類}"):
        assert f in rw, f
    assert "既存の必要像" in d["rewrite"]["not_given"] and "背景" in d["rewrite"]["not_given"]   # §9・Q8
    assert "6 つずつ" in d["c_group"]["prompt"] and d["c_group"]["per_element"] == 6
    assert "関心や趣味として親しんでいるが、像が求めている役割（してきたこと・できること）は持っていない人の文" in d["hard_negative_a"]["prompt"]
    assert d["hard_negative_a"]["spot_check"]["seed"] == 6607 and d["faithfulness"]["seed"] == 6605
    assert d["faithfulness"]["max_pairs"] == 10 and d["faithfulness"]["embedding"]["revision"].startswith("1066b659")
    assert "L" in d["measure"]["no_L"] and "C6m" in d["measure"]["schemes"]
    blob = json.dumps(d, ensure_ascii=False)
    for bad in ("KAORU の必要像の文", "生テキスト", "evidence_span"):                # 個人の文・生テキストを入れない
        assert bad not in blob
