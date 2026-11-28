"""2026-11-26 の頼みごと — ① からラベルの語を機械で消す（スイッチ・既定は切）。テスト 594。架空の人だけ。モデルは動かさない。

- 規則：forbid-v0.5 のラベルの語（X.LABEL_WORDS）だけ。対のかっこごと・すぐ後ろの助詞 1 字（の・な・で・に・と・が・は・を・も）まで
- 切（既定）：前と同じ（選ぶ段の確かめで落ちる）。版の鍵も前と同じ
- 入：消した後に確かめをやり直し、通れば出す。消した数を記録に残す。版の鍵は別になる
"""
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import explain_service as XSV  # noqa: E402
import explanation as X  # noqa: E402
from explain_pipeline import card as CD  # noqa: E402
from explain_pipeline import runner as RN  # noqa: E402
from explain_pipeline import runtime as RT  # noqa: E402
from explain_pipeline import synthetic as SY  # noqa: E402
from test_t80_explain_pipeline import fake_answer  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in (RT.VERSION_ENV, RT.STRIP_ENV, RT.BOUND_ENV, RT.CELLS_ENV):
        monkeypatch.delenv(n, raising=False)
    XSV._CFG = None
    yield
    XSV._CFG = None


@pytest.mark.parametrize("src,want,n", [
    ("{O}の同種の経験が、{N}の目的の力になれる可能性があります。", "{O}の経験が、{N}の目的の力になれる可能性があります。", 1),
    ("{O}の経験は「same_kind」で、{N}の力になれる可能性があります。", "{O}の経験は、{N}の力になれる可能性があります。", 1),
    ("{O}の経験（Relational）が{N}の力になれる可能性があります。", "{O}の経験が{N}の力になれる可能性があります。", 1),   # かっこ書きの後の助詞は残す
    ("{O}の同種、partialな経験が力になれる可能性があります。", "{O}の、経験が力になれる可能性があります。", 2),        # 読点は残す（規則の外）
    ("impartial な見方と同じ種類の経験が力になれる可能性があります。", "impartial な見方と同じ種類の経験が力になれる可能性があります。", 0),
])
def test_t594_rule(src, want, n):
    c = {}
    assert CD.strip_label_words(src, c) == want and c["label_words_removed"] == n
    assert not CD.X._forbidden(want) or n == 0


class Rec:
    """偽の推論先。選ぶ段の ① にラベルの語（同種）を入れる。"""

    def complete(self, prompt, max_tokens):
        _, text = fake_answer(prompt)
        if '"purposes"' in text:
            v = json.loads(text)
            for it in v["purposes"]:
                for m in it.get("meaning") or []:
                    m["text"] = "{O}の同種の経験は、{N}の目的の力になれる可能性があります。"
            text = json.dumps(v, ensure_ascii=False)
        t = time.time()
        return {"text": text, "reasoning": "", "finish_reason": "stop", "usage": None, "timings": None,
                "t_send": t, "t_first": t, "t_first_content": t, "t_end": t}


def _shown(card, names):
    kept, problems = X.check(card, names=names)
    return [p for p in kept["purposes"] + kept["reverse_purposes"] if p["status"] in X.SHOWN_STATUSES and p.get("meaning")], problems


def test_t594_switch_off_and_on(monkeypatch):
    cfg = RT.load(version="explain-v06")                                                       # v06（スイッチ切）で確かめる
    spec = SY.pairs()[0]
    kw = dict(cells=RT.cells(cfg), label_sha=cfg["prompts"]["label_sha256"], select_sha=cfg["prompts"]["select_sha256"])
    off, rec_off = RN.run_pair(spec, Rec(), **kw)                                              # 切（既定）：前と同じ
    assert not _shown(off["cards"]["A"], tuple(spec["names"]))[0]
    assert not rec_off["counts"]["form_fixes"].get("label_words_removed")
    on, rec_on = RN.run_pair(spec, Rec(), strip_label_words=True, **kw)                       # 入：消して確かめをやり直す
    shown, problems = _shown(on["cards"]["A"], tuple(spec["names"]))
    assert shown and all("同種" not in p["meaning"]["text"] for p in shown) and not problems
    assert all(p["meaning"]["text"].endswith(X.PIPELINE_MEANING_END) for p in shown)
    assert rec_on["counts"]["form_fixes"]["label_words_removed"] > 0                          # 消した数を記録に残す
    # 版の鍵：切のときは前と同じ（2026-11-25 の explain-v06 の鍵）・入のときは別
    k_off = RT.version_key(cfg, X.SCHEMA_VERSION, X.RELATION_RULE_VERSION)
    assert k_off.startswith("explain-v06:38512d95") and not RT.strip_label_words(cfg)
    monkeypatch.setenv(RT.STRIP_ENV, "1")
    on_cfg = RT.load(version="explain-v06")
    assert RT.strip_label_words(on_cfg) and RT.version_key(on_cfg, X.SCHEMA_VERSION, X.RELATION_RULE_VERSION) != k_off
    assert RT.version_record(on_cfg, "local_http")["strip_label_words"] is True
    p81 = RT.load(version="p81")
    monkeypatch.delenv(RT.STRIP_ENV)
    assert RT.version_key(RT.load(version="p81")) != RT.version_key(p81)                       # p81 も入のときだけ別の鍵
