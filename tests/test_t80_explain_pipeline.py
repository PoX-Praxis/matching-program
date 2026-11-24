"""指示書80 順1 — 出力を変えずに速くする流れ（二つの向きを同時に・起動をまとめる・時間の記録）。テスト 558〜566。
架空の組だけを使う（KAORU・PA の文は使わない）。推論先は、手元で動かす偽の vLLM（stream で返す・同じ入力には同じ出力）。

558 プロンプトは docs/prompts/ のまま（sha256 が変わらない・差し込み口）。違えば組み立てない
559 架空の 3 組（多い・中くらい・少ない）は決まった形で作られ、部品と候補の数が違う
560 回 a（順に）・b（組の中で同時）・c（組どうしも同時）の出力（ラベル・選ぶ段・カード）が一字まで同じ
561 b では二つの向きが同時に動き、a では順に動く。c では別の組どうしも同時に動く。選ぶ段は組の中で、ラベルの後に順に
562 記録（records）には数・時刻・番号だけ（欄の文・プロンプト・出力の文が入らない）
563 本番の既定は 1 組ずつ・向きも順に。生成の設定は 5 回目・6 回目のまま（温度 0・seed 0・medium・最大トークン・vLLM の起動の形）
564 vLLM のログから数だけを取り出す（起動の内訳・速さ）。文の行は取り出さない
565 job_main（ジョブの入口）が、出力・記録・比べた結果・ログの数を書く（推論先 local_http・hf_jobs で同じ流れ）
566 本番は src/explain_service.py だけを通して流れを使う・ジョブに渡すコードの一覧に、流れのモジュールがそろっている
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

from explain_pipeline import atoms as AT  # noqa: E402
from explain_pipeline import endpoint as EP  # noqa: E402
from explain_pipeline import fields as FL  # noqa: E402
from explain_pipeline import hf_job as HJ  # noqa: E402
from explain_pipeline import job_main as JM  # noqa: E402
from explain_pipeline import prompts as PR  # noqa: E402
from explain_pipeline import runner as RN  # noqa: E402
from explain_pipeline import synthetic as SY  # noqa: E402
from explain_pipeline import vllm_log as VL  # noqa: E402

CAND = re.compile(r"^(C\d{3})（([ABC])）：", re.M)
PURPOSE = re.compile(r"^## 目的 (N\.purpose\.p\d+|N\.need)", re.M)
PAIR = re.compile(r"^  - (P\d{3})：", re.M)
DELAY = 0.04


def fake_answer(prompt):
    """同じプロンプトには同じ答え（時刻・順番に依らない）。"""
    h = (lambda s: int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:8], 16))
    cands = CAND.findall(prompt)
    if cands:
        links = ("relational", "irrelevant", "same_kind", "topic_only", "partial", "unclear")
        labels = [{"pair": c, "link": links[h(prompt[:200] + c) % len(links)] if cell == "A" else "irrelevant", "basis": "経験",
                   "key": {"side": "語", "power": "語"}, "fails_if": "{O}の経験が{N}の目的に合うかは分からない"} for c, cell in cands]
        return "ラベルを考えた。" * 3, json.dumps({"labels": labels}, ensure_ascii=False)
    if PURPOSE.search(prompt):
        out = []
        for block in re.split(r"\n\n(?=## 目的 )", prompt[prompt.find("## 目的 "):]):
            pid = PURPOSE.search(block).group(1)
            ps = PAIR.findall(block)
            out.append({"purpose_id": pid, "chosen": ps[:1], "meaning": [{"text": "{O}の経験は、{N}の目的に関われます", "cites": ps[:1]}] if ps else [],
                        "unknown": [], "why_not": None if ps else "組がありません。"})
        return "選ぶ組を考えた。" * 2, json.dumps({"purposes": out}, ensure_ascii=False)
    return "", "了解"


class FakeVllm(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/tokenize":
            data = json.dumps({"count": len(body["prompt"])}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.server.bodies.append({k: v for k, v in body.items() if k != "messages"})
        prompt = body["messages"][0]["content"]
        reasoning, content = fake_answer(prompt)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def send(ev):
            self.wfile.write(("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        chunks = [("reasoning", reasoning[i:i + 7]) for i in range(0, len(reasoning), 7)] + \
                 [("content", content[i:i + 50]) for i in range(0, len(content), 50)]
        step = DELAY * 4 / max(1, len(chunks))
        time.sleep(DELAY)
        for k, v in chunks:
            send({"choices": [{"index": 0, "delta": {k: v}, "finish_reason": None}]})
            time.sleep(step)
        send({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        send({"choices": [], "usage": {"prompt_tokens": len(prompt), "completion_tokens": len(reasoning) + len(content),
                                       "completion_tokens_details": {"reasoning_tokens": len(reasoning)}}})
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeVllm)
    srv.daemon_threads = True
    srv.bodies = []
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()


@pytest.fixture(scope="module")
def specs():
    return SY.pairs()


@pytest.fixture(scope="module")
def runs(server, specs):
    ep = EP.make("local_http", base_url=f"http://127.0.0.1:{server.server_port}")
    out, rec = {}, {}
    for m, cfg in RN.MODES.items():
        out[m], rec[m] = RN.run_pairs(specs, ep, cfg["direction_parallel"], cfg["pair_concurrency"])
    return out, rec


# ── 558 プロンプトは docs/prompts/ のまま ──────────────────────────────────────────────
def test_t558_prompts_unchanged(tmp_path):
    for path, sha in ((PR.LABEL_FILE, PR.LABEL_SHA256), (PR.SELECT_FILE, PR.SELECT_SHA256)):
        assert hashlib.sha256(open(path, "rb").read()).hexdigest() == sha
    assert PR.LABEL_SHA256.startswith("02ec1ca8") and PR.SELECT_SHA256.startswith("19e818fb")
    assert PR.placeholders(PR.LABEL_FILE) == ["@@CANDS@@", "@@DIRECTION@@", "@@N_ATOMS@@", "@@O_ATOMS@@"]
    assert PR.placeholders(PR.SELECT_FILE) == ["@@N_ATOMS@@", "@@O_ATOMS@@", "@@PURPOSES_AND_PAIRS@@"]
    bad = tmp_path / "p.txt"
    bad.write_text(open(PR.LABEL_FILE, encoding="utf-8").read() + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        PR.template(str(bad), PR.LABEL_SHA256)


# ── 559 架空の 3 組 ──────────────────────────────────────────────────────────────
def test_t559_synthetic_pairs(specs):
    assert [s["id"] for s in specs] == ["synL", "synM", "synS"]
    assert SY.pairs() == specs
    n = {}
    for s in specs:
        F, META = FL.pair_fields(s["a"], s["b"])
        atoms = AT.build_atoms(F, META)
        _, cands, _ = PR.label_prompts(atoms)
        n[s["id"]] = (len(atoms), len(cands))
        assert all(x.startswith("架空") for x in s["names"])
    assert n == {"synL": (42, 132), "synM": (37, 42), "synS": (11, 19)}
    assert not FL.has_purposes(specs[2]["b"]) and FL.has_purposes(specs[2]["a"])      # 少ない組の二人目は目的の欄が無い人


# ── 560 a・b・c の出力が一字まで同じ ───────────────────────────────────────────────────
def test_t560_modes_identical(runs, specs):
    out, _ = runs
    cmp = RN.compare(out)
    assert set(cmp) == {s["id"] for s in specs}
    for pair, by in cmp.items():
        assert set(by) == {"b", "c"}
        for m, d in by.items():
            assert len(d) == 10 and all(v is None for v in d.values()), (pair, m)
    # 中身もある（ラベル・選ぶ段・カードの目的）
    o = out["a"]["synL"]
    assert json.loads(o["label"]["N_by_O"]["text"])["labels"] and o["select"]["A_need"]["text"]
    assert o["cards"]["A"]["purposes"] and o["cards"]["A"]["generation"]["prompt1_sha"] == PR.LABEL_SHA256
    assert o["cards"]["A"]["generation"]["prompt2_sha"] == PR.SELECT_SHA256
    assert RN.first_diff("abcd", "abXd") == 2 and RN.first_diff("ab", "abc") == 2 and RN.first_diff("x", "x") is None


# ── 561 同時に動いたか ──────────────────────────────────────────────────────────────
def _req(p, rid):
    return next(q for q in p["requests"] if q["id"] == rid)


def test_t561_concurrency(runs):
    _, rec = runs
    assert rec["a"]["pair_concurrency"] == 1 and rec["c"]["pair_concurrency"] == 3
    for m in ("a", "b", "c"):
        for p in rec[m]["pairs"]:
            n, o = _req(p, "N_by_O"), _req(p, "O_by_N")
            sa, sb = _req(p, "A_need"), _req(p, "B_need")
            if m == "a":
                assert n["t_end"] <= o["t_send"]                      # 順に
            else:
                assert max(n["t_send"], o["t_send"]) < min(n["t_end"], o["t_end"])     # 同時に
            assert max(n["t_end"], o["t_end"]) <= sa["t_send"] and sa["t_end"] <= sb["t_send"]   # 選ぶ段はラベルの後に順に
            assert all(q["t_send"] <= q["t_first"] <= q["t_end"] for q in p["requests"])
    for m in ("a", "b"):
        ps = rec[m]["pairs"]
        assert all(ps[i]["t_end"] <= ps[i + 1]["t_start"] for i in range(len(ps) - 1))      # 組は 1 つずつ
    ps = rec["c"]["pairs"]
    assert max(p["t_start"] for p in ps) < min(p["t_end"] for p in ps)                     # 組どうしも同時に


# ── 562 記録には数・時刻・番号だけ ──────────────────────────────────────────────────────
def test_t562_records_have_no_text(runs, specs):
    out, rec = runs
    s = json.dumps(rec, ensure_ascii=False)
    texts = []
    for sp in specs:
        F, _ = FL.pair_fields(sp["a"], sp["b"])
        texts += list(F.values())
    for m in out:                                      # 出力の文（考える部分・fails_if・① の文）。JSON のキーの名前は文ではない
        for o in out[m].values():
            texts += [v["reasoning"] for v in list(o["label"].values()) + list(o["select"].values())]
            texts += [x["fails_if"] for v in o["label"].values() for x in json.loads(v["text"])["labels"]]
            texts += [t["text"] for v in o["select"].values() for x in json.loads(v["text"])["purposes"] for t in x["meaning"]]
    for t in texts:
        for i in range(0, max(1, len(t) - 7)):
            assert t[i:i + 8] not in s
    allowed = {"stage", "id", "t_send", "t_first", "t_first_content", "t_end", "prompt_tokens", "completion_tokens", "reasoning_tokens",
               "finish_reason", "error", "timings"}
    for m in rec.values():
        for p in m["pairs"]:
            assert set(p) == {"pair", "t_start", "t_end", "machine", "requests", "counts"}
            assert set(p["machine"]) == {"prep", "check", "card"} and all(isinstance(v, float) for v in p["machine"].values())
            for q in p["requests"]:
                assert set(q) == allowed and q["finish_reason"] == "stop" and q["reasoning_tokens"] > 0


# ── 563 既定と生成の設定 ──────────────────────────────────────────────────────────────
def test_t563_defaults_and_settings(server):
    assert RN.DEFAULTS == {"direction_parallel": False, "pair_concurrency": 1}
    assert RN.run_pairs.__defaults__[:2] == (False, 1)
    b = EP.LocalHttp().body("x", RN.MAX_TOKENS)
    assert {k: b[k] for k in ("temperature", "top_p", "seed", "reasoning_effort", "max_tokens")} == \
        {"temperature": 0, "top_p": 1, "seed": 0, "reasoning_effort": "medium", "max_tokens": 30000}
    assert b["model"] == "gpt-oss-120b" and b["stream"] is True
    assert all(x["max_tokens"] == 30000 and x["seed"] == 0 and x["temperature"] == 0 for x in server.bodies)
    assert type(EP.make("hf_jobs")) is type(EP.make("local_http")) and EP.make("hf_jobs").base_url == "http://127.0.0.1:8000"
    with pytest.raises(ValueError):
        EP.make("other")
    sh = HJ.job_script()
    assert "VLLM_BATCH_INVARIANT=1 vllm serve openai/gpt-oss-120b --revision b5c939de8f754692c1647ca79fbf85e8c1e70f8a" in sh
    for flag in ("--no-enable-prefix-caching", "--seed 0", "--max-model-len 40960", "--gpu-memory-utilization 0.90", "--max-num-seqs 8"):
        assert flag in sh
    assert "v0.33.0@sha256:d825baf9" in HJ.VLLM_IMAGE and HJ.MAX_NUM_SEQS >= 2
    assert HJ.vllm_settings()["max_num_seqs"] == 8 and HJ.max_cost(HJ.job_spec()) <= HJ.BUDGET_USD
    assert "fugashi==1.5.2" in sh and "unidic-lite==1.0.8" in sh


# ── 564 vLLM のログから数だけ ─────────────────────────────────────────────────────────
LOG = """100.0\tINFO 11-24 10:00:00 [api_server.py] vLLM API server version 0.33.0
130.5\t(EngineCore pid=1) INFO [weight_utils.py] Time spent downloading weights for openai/gpt-oss-120b: 25.3 seconds
170.2\t(EngineCore pid=1) INFO [default_loader.py] Loading weights took 38.41 seconds
171.0\t(EngineCore pid=1) INFO [gpu_model_runner.py] Model loading took 65.97 GiB memory and 70.1 seconds
200.0\t(EngineCore pid=1) INFO [backends.py] torch.compile takes 21.5 s in total
205.0\t(EngineCore pid=1) INFO [kv_cache_utils.py] GPU KV cache size: 1,234,567 tokens
205.1\t(EngineCore pid=1) INFO [kv_cache_utils.py] Maximum concurrency for 40,960 tokens per request: 30.14x
230.0\t(EngineCore pid=1) INFO [gpu_model_runner.py] Graph capturing finished in 18 secs, took 1.2 GiB
231.0\t(EngineCore pid=1) INFO [core.py] init engine (profile, create kv cache, warmup model) took 52.3 seconds
240.0\tINFO:     Application startup complete.
300.0\tINFO [loggers.py] Engine 000: Avg prompt throughput: 812.4 tokens/s, Avg generation throughput: 98.6 tokens/s, Running: 1 reqs, Waiting: 0 reqs, GPU KV cache usage: 0.5%, Prefix cache hit rate: 0.0%
310.0\tINFO [loggers.py] Engine 000: Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 190.2 tokens/s, Running: 2 reqs, Waiting: 0 reqs, GPU KV cache usage: 0.9%, Prefix cache hit rate: 0.0%
311.0\tINFO Received request chatcmpl-1: prompt: '架空の人の文がここにある'
not stamped line
"""


def test_t564_vllm_log_numbers_only():
    p = VL.parse(LOG.splitlines(True), origin=100.0)
    names = [e["event"] for e in p["events"]]
    assert names == ["download_weights", "load_weights", "model_loading", "torch_compile", "kv_cache_tokens", "max_concurrency",
                     "graph_capture", "init_engine", "api_startup_complete", "throughput", "throughput"]
    ev = {e["event"]: e for e in p["events"]}
    assert ev["load_weights"] == {"event": "load_weights", "t": 70.2, "seconds": 38.41}
    assert ev["model_loading"]["gib"] == 65.97 and ev["model_loading"]["seconds"] == 70.1
    assert ev["kv_cache_tokens"]["tokens"] == 1234567 and ev["max_concurrency"]["x"] == 30.14
    assert ev["api_startup_complete"]["t"] == 140.0 and p["lines"] == 13 and p["first_t"] == 0.0
    s = VL.summary(p, [("a", 150.0, 205.0), ("b", 206.0, 300.0), ("c", 400.0, 500.0)])
    assert s["startup"]["init_engine"]["seconds"] == 52.3 and s["throughput_lines"] == 2
    assert s["windows"]["a"] == {"lines": 1, "prompt_tps_mean": 812.4, "gen_tps_mean": 98.6, "gen_tps_max": 98.6, "running_max": 1}
    assert s["windows"]["b"]["gen_tps_mean"] == 190.2 and s["windows"]["b"]["running_max"] == 2 and "c" not in s["windows"]
    assert "架空" not in json.dumps(p, ensure_ascii=False) + json.dumps(s, ensure_ascii=False)
    import io
    buf = io.StringIO()
    VL.stamp(io.StringIO("x\ny\n"), buf, clock=lambda: 1.5)
    assert buf.getvalue() == "1.500\tx\n1.500\ty\n"


# ── 565 job_main ────────────────────────────────────────────────────────────────
def test_t565_job_main(server, specs, tmp_path):
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps({"pairs": specs[1:]}, ensure_ascii=False), encoding="utf-8")
    log = tmp_path / "vllm.log"
    t = time.time()
    log.write_text(f"{t:.3f}\tINFO: Application startup complete.\n", encoding="utf-8")
    JM.main(["--pairs", str(pairs), "--modes", "a,b,c", "--out", str(tmp_path / "out"), "--base-url", f"http://127.0.0.1:{server.server_port}",
             "--origin", str(t - 1), "--vllm-log", str(log), "--vllm-settings", json.dumps(HJ.vllm_settings()), "--warmup"])
    rec = json.load(open(tmp_path / "out" / "records.json", encoding="utf-8"))
    cmp = json.load(open(tmp_path / "out" / "compare.json", encoding="utf-8"))
    outs = json.load(open(tmp_path / "out" / "outputs.json", encoding="utf-8"))
    vl = json.load(open(tmp_path / "out" / "vllm_log.json", encoding="utf-8"))
    assert set(rec["modes"]) == {"a", "b", "c"} and rec["pairs"] == ["synM", "synS"] and set(outs) == {"a", "b", "c"}
    assert rec["settings"]["vllm"]["max_num_seqs"] == 8 and rec["settings"]["prompt_sha256"]["label"] == PR.LABEL_SHA256
    assert rec["warmup"]["completion_tokens"] == 2 and rec["warmup"]["t_send"] > 0
    assert all(v is None for by in cmp.values() for d in by.values() for v in d.values())
    for m in rec["modes"].values():
        for p in m["pairs"]:
            for q in p["requests"]:
                assert q["content_tokens"] > 0 and q["reasoning_tokens_counted"] == q["reasoning_tokens"]
    assert vl["parsed"]["events"][0]["event"] == "api_startup_complete" and abs(vl["parsed"]["events"][0]["t"] - 1.0) < 0.01


# ── 566 本番から呼ばれない・ジョブに渡す一覧 ───────────────────────────────────────────────────
def test_t566_isolation():
    # 指示書81: 本番は src/explain_service.py だけを通して流れを使う（app.py は直に読まない）
    assert "explain_pipeline" not in open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    for name in os.listdir(os.path.join(ROOT, "src")):
        if name.endswith(".py") and name != "explain_service.py":
            assert "explain_pipeline" not in open(os.path.join(ROOT, "src", name), encoding="utf-8").read(), name
    d = os.path.join(ROOT, "scripts", "explain_pipeline")
    assert set(HJ.CODE) >= {os.path.join("scripts", "explain_pipeline", n) for n in os.listdir(d)
                            if n.endswith(".py") and n not in ("hf_job.py", "synthetic.py", "runtime.py", "hfjobs.py")}
