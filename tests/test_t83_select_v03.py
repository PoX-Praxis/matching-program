"""指示書83 — 選ぶ段 v0.3・版 explain-v07。テスト 595。モデルは動かさない。架空の人だけ。

595 プロンプトの sha・置き場所の印が v0.1 と同じ・決まり 5 つとお手本 4 つが入っている・版 explain-v07 の鍵（explain-v06 の鍵は前のまま）・
    ラベルの語を消すスイッチが入・流れで選ぶ段に v0.3 が渡る・お手本に KAORU・PA の文が入っていない（手元に写しがあるときだけ 8 字で照合）
"""
import hashlib
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import explain_service as XSV  # noqa: E402
import explanation as X  # noqa: E402
from explain_pipeline import prompts as PR  # noqa: E402
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


def test_t595_prompt():
    t = open(PR.SELECT_FILE_V03, encoding="utf-8").read()
    assert hashlib.sha256(t.encode("utf-8")).hexdigest() == PR.SELECT_SHA256_V03
    assert PR.placeholders(PR.SELECT_FILE_V03) == PR.placeholders(PR.SELECT_FILE_V01)
    v01 = open(PR.SELECT_FILE_V01, encoding="utf-8").read()
    assert v01.splitlines()[0] == t.splitlines()[0]                                      # v0.1 をもとにした
    for rule in ("目的ごとに 1 文で書く", "組ごとに文を分けない", "{O}は、［相手がどんな人か］で、{N}の［求めの中身］に、［どう関われるか］可能性があります。",
                 "分類の名前を述語や名詞にしない", "同じ関心を持つ相手になれる可能性があります。", "を提供できる可能性があります。", "言い切らない"):
        assert rule in t
    assert all(f"## お手本 {i}" in t for i in (1, 2, 3, 4)) and "## 悪い書き方" in t
    assert "「〜になれる可能性があります。」の形にする" not in t                              # v0.2 の行は入れていない


def test_t595_version():
    v06, v07 = RT.load(version="explain-v06"), RT.load(version="explain-v07")
    k6 = RT.version_key(v06, X.SCHEMA_VERSION, X.RELATION_RULE_VERSION)
    k7 = RT.version_key(v07, X.SCHEMA_VERSION, X.RELATION_RULE_VERSION)
    assert k6.startswith("explain-v06:38512d95") and k7.startswith("explain-v07:") and k6 != k7
    assert RT.version_of() == "explain-v07"                                                 # 指示書84 で本番の既定（指示書83 のときは v06）
    assert v07["prompts"]["select_sha256"] == PR.SELECT_SHA256_V03 and RT.strip_label_words(v07)
    assert not RT.strip_label_words(v06) and v06["prompts"]["select_sha256"] == PR.SELECT_SHA256_V01
    for sec in ("model", "engine", "generation"):                                           # 違いは選ぶ段とスイッチだけ
        assert v06[sec] == v07[sec], sec
    for k in ("flavor", "price_per_hour"):                                                   # ジョブの起こし方は版の鍵に入らない。
        assert v06["hf_jobs"][k] == v07["hf_jobs"][k], k                                     # v07 は指示書84 の費用の歯止めを足した
    assert v06["prompts"]["label_sha256"] == v07["prompts"]["label_sha256"] and RT.cells(v06) == RT.cells(v07)
    assert RT.version_record(v07, "hf_jobs")["prompts"]["select"] == PR.SELECT_SHA256_V03


class Rec:
    def __init__(self):
        self.prompts = []

    def complete(self, prompt, max_tokens):
        self.prompts.append(prompt)
        _, text = fake_answer(prompt)
        t = time.time()
        return {"text": text, "reasoning": "", "finish_reason": "stop", "usage": None, "timings": None,
                "t_send": t, "t_first": t, "t_first_content": t, "t_end": t}


def test_t595_flow_uses_v03(monkeypatch):
    monkeypatch.setenv(RT.VERSION_ENV, "explain-v07")
    cfg = XSV.runtime()
    ep = Rec()
    out, rec = RN.run_pair(SY.pairs()[0], ep, cells=RT.cells(cfg), label_sha=cfg["prompts"]["label_sha256"],
                           select_sha=cfg["prompts"]["select_sha256"], strip_label_words=RT.strip_label_words(cfg))
    head = open(PR.SELECT_FILE_V03, encoding="utf-8").read().split("@@")[0]
    assert ep.prompts[2].startswith(head) and ep.prompts[3].startswith(head)
    assert out["cards"]["A"]["generation"]["prompt2_sha"] == PR.SELECT_SHA256_V03 and X.is_pipeline(out["cards"]["A"])


def test_t595_examples_not_from_people():
    """お手本に KAORU・PA の文が入っていないこと（手元の写し local/ があるときだけ、8 字の切れ端で照合する）。"""
    paths = [os.path.join(ROOT, "local", f) for f in ("kaoru_v5_public.json", "pa_v4_public.json")]
    extra = os.path.join(ROOT, "local", "kaoru_pa_screen", "run_2026-11-26b", "inputs.json")
    if not all(os.path.exists(p) for p in paths):
        pytest.skip("手元の写しが無い（CI）")
    def texts(x):
        if isinstance(x, dict):
            for v in x.values():
                yield from texts(v)
        elif isinstance(x, list):
            for v in x:
                yield from texts(v)
        elif isinstance(x, str):
            yield x
    pieces = set()
    for p in paths + ([extra] if os.path.exists(extra) else []):
        for t in texts(json.load(open(p, encoding="utf-8"))):
            pieces.update(t[i:i + 8] for i in range(max(0, len(t) - 7)))
    prompt = open(PR.SELECT_FILE_V03, encoding="utf-8").read()
    v01 = open(PR.SELECT_FILE_V01, encoding="utf-8").read()
    added = prompt.replace(v01.split("# 部品")[0], "")                                     # 足した部分（お手本など）だけを見る
    hits = [p for p in pieces if p in added and not p.replace("-", "").replace(":", "").replace("T", "").isdigit()]
    assert not hits
