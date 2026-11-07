"""指示書66C 追補 §1-3 — 乗っ取りの試験の注入文（テスト 379）。計測の前に固定した文集の形を確かめる（参考値。決定には使わない）。"""
import json
import os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KINDS = ("直接の指示", "役割の演技", "区切り記号からの脱出", "丁寧な依頼", "長い文の中に埋める")


def _subseq(a, b):
    i = 0
    for ch in b:
        if i < len(a) and a[i] == ch:
            i += 1
    return i == len(a)


# ── 379 注入文: 5 種類 × 5 組・基の文の文字を残して足すだけ・出どころ ───────────────────────────
def test_t379_injection_set_fixed_before_measurement():
    with open(os.path.join(ROOT, "scripts", "probe_data", "injection_v1.json"), encoding="utf-8") as f:
        d = json.load(f)
    assert d["version"] == "inj-v1" and "決定の規則には使わない" in d["_note"]
    items = d["items"]
    assert len(items) == 25 and Counter(x["kind"] for x in items) == {k: 5 for k in KINDS}
    for x in items:
        assert x["injected"] != x["base"] and len(x["injected"]) > len(x["base"])
        if x["kind"] == "長い文の中に埋める":
            assert _subseq(x["base"], x["injected"])                 # 文字を順に残して途中に差し込む
        else:
            assert x["base"] in x["injected"]                         # 基の文をそのまま含む
    assert len({x["base"] for x in items}) == 25
    pv = d["provenance"]
    assert "新しい会話" in pv["how"] and "書き換えていない" in pv["how"] and "混ぜ方は次の 5 種類" in pv["prompt"]
