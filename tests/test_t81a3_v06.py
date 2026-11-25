"""指示書81 追補3 — v06 の条件を本番の版（explain-v06）にする。テスト 585〜590。架空の人だけ。モデルは動かさない。

585 版：本番の既定は explain-v06（vLLM v0.33.0・条件 B・候補 A・B・C・ラベル v0.3・選ぶ段 v0.1）。p81 は残る。鍵の頭が違う
586 流れ：explain-v06 では選ぶ段が v0.1・候補が A・B・C。推論先は vLLM の形（reasoning_effort だけ）で送り、エンジンの版を確かめる
587 HF Jobs の vLLM のジョブ：6 回目と同じ入れ物・引数・VLLM_BATCH_INVARIANT=1。口は鍵を確かめる中継（鍵は secrets だけ）
588 ジョブの許可：POX_EXPLAIN_JOBS_PERMITTED=1 が無ければ、HF Jobs のジョブを起動しない（予定に残す）
589 保存した出力を入れる：記録と同じなら入れる（版の鍵 explain-v06・元の記録の sha256）。違えば違う位置と種類だけを返し、入れない
590 目的の欄が無い人の見出し：「〇〇が求めている相手『〇〇さんが求めている相手』」の二重にしない（求めている相手の文を出す）
591 運営の写し（/ops/explanations/export）：鍵と許可リストの組だけ。いまの版・いまの入力の向き 2 つと入力を返す
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import explain_queue as Q  # noqa: E402
import explain_service as XSV  # noqa: E402
import explanation as X  # noqa: E402
import explanation_store as S  # noqa: E402
from explain_pipeline import endpoint as EP  # noqa: E402
from explain_pipeline import hfjobs as HJ  # noqa: E402
from explain_pipeline import prompts as PR  # noqa: E402
from explain_pipeline import runner as RN  # noqa: E402
from explain_pipeline import runtime as RT  # noqa: E402
from explain_pipeline import synthetic as SY  # noqa: E402
from test_t80_explain_pipeline import fake_answer  # noqa: E402

DRAIN = "d" * 40


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in (RT.VERSION_ENV, XSV.JOBS_OK_ENV, RT.CELLS_ENV, RT.BOUND_ENV, *RT.PLACEMENT_ENV.values()):
        monkeypatch.delenv(n, raising=False)
    XSV._CFG = None
    yield
    XSV._CFG = None


class Rec:
    """偽の推論先（同じ入力には同じ答え）。送った文と返した文を順に残す。"""

    def __init__(self):
        self.prompts, self.texts = [], []

    def complete(self, prompt, max_tokens):
        _, text = fake_answer(prompt)
        self.prompts.append(prompt)
        self.texts.append(text)
        t = time.time()
        return {"text": text, "reasoning": "", "finish_reason": "stop", "usage": None, "timings": None,
                "t_send": t, "t_first": t, "t_first_content": t, "t_end": t}


def _ctx(spec):
    return XSV.Context({"x1": spec["a"], "x2": spec["b"]}.get, {"x1": spec["names"][0], "x2": spec["names"][1]}.get)


# ── 585 版 ─────────────────────────────────────────────────────────────────────────────
def test_t585_versions():
    v06 = RT.load()
    assert RT.version_of() == RT.DEFAULT_VERSION == "explain-v06" and RT.version_id(v06) == "explain-v06"
    e = v06["engine"]
    assert (e["name"], e["version"]) == ("vllm", "0.33.0") and e["image"].endswith(
        "@sha256:d825baf9ce4b1b6bfc5ecc7b79880c7e6748c65a72d697ce3d47607aafa84c6e")
    assert e["batch_invariant"] is True and e["prefix_caching"] is False and e["max_num_seqs"] == 1 and e["max_model_len"] == 40960
    assert (v06["model"]["repo"], v06["model"]["revision"]) == ("openai/gpt-oss-120b", "b5c939de8f754692c1647ca79fbf85e8c1e70f8a")
    assert RT.decoding(v06) == {"temperature": 0, "top_p": 1, "seed": 0, "reasoning_effort": "medium", "max_tokens": 30000,
                                "ctx_size": 40960, "parallel": 1}
    assert v06["prompts"] == {"label_sha256": PR.LABEL_SHA256, "select_sha256": PR.SELECT_SHA256_V01}       # 選ぶ段は v0.1
    assert RT.cells(v06) == ("A", "B", "C") and not RT.bound_for_no_purpose(v06)
    p81 = RT.load(version="p81")
    assert RT.version_id(p81) == "p81" and p81["engine"]["name"] == "llama.cpp" and p81["prompts"]["select_sha256"] == PR.SELECT_SHA256
    k6, k8 = RT.version_key(v06, X.SCHEMA_VERSION, X.RELATION_RULE_VERSION), RT.version_key(p81, X.SCHEMA_VERSION, X.RELATION_RULE_VERSION)
    assert k6.startswith("explain-v06:") and k8.startswith("p81:") and k6 != k8
    rec = RT.version_record(v06, "hf_jobs")
    assert rec["version"] == "explain-v06" and rec["engine"]["name"] == "vllm" and rec["prompts"]["select"] == PR.SELECT_SHA256_V01
    with pytest.raises(ValueError):
        RT.version_of({RT.VERSION_ENV: "v99"})
    assert XSV.runtime() is XSV.runtime() and RT.version_id(XSV.runtime()) == "explain-v06"


def test_t585_startup_check(monkeypatch):
    monkeypatch.setenv(XSV.BACKEND_ENV, "hf_jobs")
    monkeypatch.setenv(XSV.HF_TOKEN_ENV, "hf_x")
    st = XSV.startup_check()
    assert st["version"].startswith("explain-v06:") and st["cells"] == "ABC" and "起動しない" in st["jobs"]
    monkeypatch.setenv(RT.VERSION_ENV, "p81")
    assert XSV.startup_check()["version"].startswith("p81:")


# ── 586 流れ・推論先 ────────────────────────────────────────────────────────────────────
def test_t586_flow_and_endpoint():
    cfg = RT.load()
    spec = SY.pairs()[0]
    ep = Rec()
    out, rec = RN.run_pair(spec, ep, cells=RT.cells(cfg), label_sha=cfg["prompts"]["label_sha256"],
                           select_sha=cfg["prompts"]["select_sha256"])
    v01 = open(PR.SELECT_FILE_V01, encoding="utf-8").read()
    head = v01[:v01.index("@@")]
    assert ep.prompts[2].startswith(head) and ep.prompts[3].startswith(head)                 # 選ぶ段は v0.1 の本文
    assert "「〜になれる可能性があります。」の形にする" not in ep.prompts[2]                       # v0.2 の行は無い
    assert all(rec["counts"]["cands"][d][c] > 0 for d in ("N_by_O", "O_by_N") for c in "BC")
    assert "（B）" in ep.prompts[0] and "（C）" in ep.prompts[0]
    assert out["cards"]["A"]["generation"]["prompt2_sha"] == PR.SELECT_SHA256_V01
    e = EP.from_runtime(cfg, "http://127.0.0.1:9", "k" * 32)
    b = e.body("架空", 30000)
    assert e.engine == "vllm" and not e.stream and "chat_template_kwargs" not in b
    assert {k: b[k] for k in ("temperature", "top_p", "seed", "max_tokens", "reasoning_effort")} == \
        {"temperature": 0, "top_p": 1, "seed": 0, "max_tokens": 30000, "reasoning_effort": "medium"}
    assert e._headers()["X-Api-Key"] == "k" * 32
    p81 = EP.from_runtime(RT.load(version="p81"), "http://127.0.0.1:9", None)
    assert p81.engine == "llamacpp" and "chat_template_kwargs" in p81.body("x", 1)


# ── 587 HF Jobs の vLLM のジョブ ────────────────────────────────────────────────────────
class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        data = json.dumps({"version": "0.33.0"} if self.path == "/version" else {}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_POST = do_GET


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_t587_vllm_job(tmp_path):
    cfg = RT.load()
    sp = HJ.job_spec(cfg, "KEY-" + "s" * 40)
    script = sp["command"][2]
    assert sp["dockerImage"] == cfg["engine"]["image"] and sp["flavor"] == "h200"
    assert sp["expose"] == {"ports": [8080], "portsPublic": [8080]} and sp["secrets"] == {HJ.KEY_ENV: "KEY-" + "s" * 40}
    assert "KEY-" not in script and "KEY-" not in json.dumps(sp["environment"])
    serve = next(line for line in script.splitlines() if "vllm serve" in line)
    assert serve.startswith("VLLM_BATCH_INVARIANT=1 vllm serve openai/gpt-oss-120b --revision b5c939de8f754692c1647ca79fbf85e8c1e70f8a")
    for a in ("--served-model-name gpt-oss-120b", "--host 127.0.0.1 --port 8000", "--max-model-len 40960", "--seed 0",
              "--gpu-memory-utilization 0.9", "--no-enable-prefix-caching", "--max-num-seqs 1"):
        assert a in serve
    assert "== '0.33.0'" in script and "> /tmp/server.log" in script
    # 中継：鍵が無い・違うなら 401。X-Api-Key か Bearer が同じなら上流に渡す
    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    port = _free_port()
    (tmp_path / "p.py").write_text(HJ.PROXY_PY, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(tmp_path / "p.py"), str(port), str(up.server_port)],
                            env={**os.environ, HJ.KEY_ENV: "secret-key"})
    try:
        ep = EP.LocalHttp(base_url=f"http://127.0.0.1:{port}", api_key="secret-key", stream=False)
        for _ in range(100):
            if ep.healthy(timeout=1):
                break
            time.sleep(0.1)
        assert ep.healthy() and EP.engine_version(ep) == "0.33.0"
        assert not EP.LocalHttp(base_url=f"http://127.0.0.1:{port}", api_key="wrong").healthy()
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
        assert e.value.code == 401
        req = urllib.request.Request(f"http://127.0.0.1:{port}/version", headers={"Authorization": "Bearer secret-key"})
        assert json.load(urllib.request.urlopen(req, timeout=5))["version"] == "0.33.0"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        up.shutdown()


# ── 588 ジョブの許可 ────────────────────────────────────────────────────────────────────
def test_t588_jobs_need_permission(monkeypatch):
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    import schema
    schema.init(db)
    monkeypatch.setenv(XSV.BACKEND_ENV, "hf_jobs")
    Q.enqueue("x1", "x2", db_path=db)
    called = []

    def factory(cfg):
        called.append(1)
        raise AssertionError("起動してはいけない")
    spec = SY.pairs()[0]
    res = XSV.drain(_ctx(spec), db_path=db, session_factory=factory)
    assert res["reason"] == "jobs_not_permitted" and res["made"] == 0 and not called
    assert Q.row("x1|x2", db)["status"] == "pending"                                     # 予定に残す
    res = XSV.drain(_ctx(spec), db_path=db, verify=True, session_factory=factory)
    assert not called
    # local_http（PC）：explain-v06 は vLLM の版が同じ推論先でだけ作る
    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    try:
        cfg = XSV.runtime()
        cfg["engine"]["version"] = "0.99.0"
        ep = EP.LocalHttp(base_url=f"http://127.0.0.1:{up.server_port}", stream=False)
        res = XSV.drain(_ctx(spec), db_path=db, backend_name="local_http", endpoint=ep)
        assert res["reason"] == "engine_mismatch" and Q.row("x1|x2", db)["status"] == "pending"
    finally:
        XSV._CFG = None
        up.shutdown()


# ── 589 保存した出力を入れる ─────────────────────────────────────────────────────────────
def _saved(spec, cfg):
    """「v06 の記録」と「保存した出力」の代わり（架空の組を偽の推論先で作ったもの）。"""
    ep = Rec()
    RN.run_pair(spec, ep, cells=RT.cells(cfg), label_sha=cfg["prompts"]["label_sha256"], select_sha=cfg["prompts"]["select_sha256"])
    outputs = {"label": {"N_by_O": ep.texts[0], "O_by_N": ep.texts[1]}, "select": {"A_need": ep.texts[2], "B_need": ep.texts[3]}}
    expected, _ = XSV.drawn("x1", "x2", _ctx(spec), cfg, outputs["label"])
    return expected, outputs


def test_t589_replay(monkeypatch):
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    import schema
    schema.init(db)
    cfg = XSV.runtime()
    spec = SY.pairs()[0]
    expected, outputs = _saved(spec, cfg)
    assert len(expected["cands"]) > 0 and set(expected["label_prompts"]) == {"N_by_O", "O_by_N"}
    assert set(expected["select_prompts"]) == {"A_need", "B_need"}
    blob = json.dumps(expected, ensure_ascii=False)
    assert spec["a"]["purposes"][0]["向かう先"][:8] not in blob                                # 記録に文は入らない
    # 比べるだけ（store なし）
    res = XSV.replay("x1", "x2", _ctx(spec), expected, outputs, {"v06": "a" * 64}, store_it=False, db_path=db)
    assert res["same"] and not res["stored"] and res["counts"]["cands"] == len(expected["cands"])
    assert XSV.stored_meta("x1", "x2", _ctx(spec), cfg, db) is None
    # 入力が違う → 入力・候補・プロンプトの違いを返し、入れない
    other = json.loads(json.dumps(spec))
    other["b"]["与え像"] = other["b"]["与え像"][:1]
    res = XSV.replay("x1", "x2", _ctx(other), expected, outputs, store_it=True, db_path=db)
    kinds = {d["kind"] for d in res["diffs"]}
    assert not res["same"] and not res["stored"] and {"入力", "部品", "候補", "label_prompts"} <= kinds
    lp = next(d for d in res["diffs"] if d["kind"] == "label_prompts")
    assert lp["diff_lines"] and all(isinstance(n, int) for n in lp["diff_lines"])           # 違う行の番号だけ
    assert XSV.stored_meta("x1", "x2", _ctx(other), cfg, db) is None
    # プロンプトの 1 行だけ違う記録 → その行の番号を返す
    bad = json.loads(json.dumps(expected))
    bad["select_prompts"]["A_need"]["lines"][3] = "0" * 16
    bad["select_prompts"]["A_need"]["sha256"] = "0" * 64
    res = XSV.replay("x1", "x2", _ctx(spec), bad, outputs, store_it=True, db_path=db)
    assert res["diffs"] == [{"kind": "select_prompts", "id": "A_need", "chars": [bad["select_prompts"]["A_need"]["chars"]] * 2,
                             "lines": [len(bad["select_prompts"]["A_need"]["lines"])] * 2, "diff_lines": [4]}] and not res["stored"]
    # 同じ → 入れる（explain-v06 の鍵・元の記録の sha256・保存した出力を入れた印）
    res = XSV.replay("x1", "x2", _ctx(spec), expected, outputs, {"v06": "a" * 64}, store_it=True, db_path=db)
    assert res["same"] and res["stored"] and res["version_key"].startswith("explain-v06:")
    for v, t in (("x1", "x2"), ("x2", "x1")):
        row = XSV.stored_meta(v, t, _ctx(spec), cfg, db)
        assert row and row["version_key"] == XSV.version_key(cfg)
        assert row["meta"]["versions"]["source"] == "saved_outputs" and row["meta"]["versions"]["source_sha256"] == {"v06": "a" * 64}
        assert row["meta"]["versions"]["backend"] == "saved_outputs" and row["meta"]["versions"]["version"] == "explain-v06"
    monkeypatch.setenv("POX_EXPLANATION", "allowlist")
    monkeypatch.setenv("POX_EXPLANATION_ALLOWLIST", "x1+x2")
    monkeypatch.setenv("POX_DEBUG", "1")
    monkeypatch.setattr(S, "_resolve_subject", lambda h, db_path="pox.db": h)
    card = S.card_for("x1", "x2", db_path=db, direction_of=XSV.direction_getter(_ctx(spec), db_path=db))
    assert card and X.is_pipeline(card) and card["generation"]["prompt2_sha"] == PR.SELECT_SHA256_V01
    # p81 の版では、explain-v06 で入れたカードは出さない（版が違う）
    monkeypatch.setenv(RT.VERSION_ENV, "p81")
    assert XSV.stored_meta("x1", "x2", _ctx(spec), XSV.runtime(), db) is None


def test_t589_route(monkeypatch):
    import app as appmod
    import schema
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    schema.init(appmod.DB)
    appmod.app.config["TESTING"] = True
    spec = SY.pairs()[0]
    monkeypatch.setattr(appmod, "_explain_input", {"x1": spec["a"], "x2": spec["b"]}.get)
    monkeypatch.setenv("POX_EXPLANATION_ALLOWLIST", "x1+x2")
    monkeypatch.setattr(S, "_resolve_subject", lambda h, db_path="pox.db": h)
    expected, outputs = _saved(spec, XSV.runtime())
    body = {"a": "x1", "b": "x2", "expected": expected, "outputs": outputs, "source_sha256": {"v06": "b" * 64}, "store": True}
    c = appmod.app.test_client()
    assert c.post("/ops/explanations/replay", json=body).status_code == 404                       # 鍵が無ければ閉じている
    monkeypatch.setenv(XSV.DRAIN_TOKEN_ENV, DRAIN)
    assert c.post("/ops/explanations/replay", json=body, headers={XSV.DRAIN_HEADER: "x" * 40}).status_code == 404
    assert c.post("/ops/explanations/replay", json={**body, "b": "x3"}, headers={XSV.DRAIN_HEADER: DRAIN}).status_code == 403
    assert c.post("/ops/explanations/replay", json={"a": "x1"}, headers={XSV.DRAIN_HEADER: DRAIN}).status_code == 400
    r = c.post("/ops/explanations/replay", json={**body, "store": False, "a": None, "b": None}, headers={XSV.DRAIN_HEADER: DRAIN})
    assert r.status_code == 200 and r.get_json()["same"] and not r.get_json()["stored"]        # 組を書かない：許可リストの 1 組
    assert XSV.order_by_record("x2", "x1", _ctx(spec), expected) == ("x1", "x2")              # 記録の一人目は入力のハッシュで決める
    r = c.post("/ops/explanations/replay", json=body, headers={XSV.DRAIN_HEADER: DRAIN})
    d = r.get_json()
    assert r.status_code == 200 and d["same"] and d["stored"]
    assert spec["a"]["purposes"][0]["向かう先"][:8] not in r.get_data(as_text=True)               # 応答に文は出さない


# ── 591 運営の写し ─────────────────────────────────────────────────────────────────────
def test_t591_export(monkeypatch):
    import app as appmod
    import schema
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    schema.init(appmod.DB)
    appmod.app.config["TESTING"] = True
    spec = SY.pairs()[0]
    monkeypatch.setattr(appmod, "_explain_input", {"x1": spec["a"], "x2": spec["b"]}.get)
    monkeypatch.setenv("POX_EXPLANATION_ALLOWLIST", "x1+x2")
    monkeypatch.setattr(S, "_resolve_subject", lambda h, db_path="pox.db": h)
    c = appmod.app.test_client()
    assert c.post("/ops/explanations/export", json={}).status_code == 404
    monkeypatch.setenv(XSV.DRAIN_TOKEN_ENV, DRAIN)
    h = {XSV.DRAIN_HEADER: DRAIN}
    assert c.post("/ops/explanations/export", json={"a": "x1", "b": "x3"}, headers=h).status_code == 403
    d = c.post("/ops/explanations/export", json={}, headers=h).get_json()
    assert d["directions"] == [] and set(d["people"]) == {"x1", "x2"}                        # まだ作っていない
    expected, outputs = _saved(spec, XSV.runtime())
    XSV.replay("x1", "x2", _ctx(spec), expected, outputs, store_it=True, db_path=appmod.DB)
    d = c.post("/ops/explanations/export", json={}, headers=h).get_json()
    assert len(d["directions"]) == 2 and all(x["version_key"] == d["version_key"] for x in d["directions"])
    assert d["people"]["x1"]["input"] == spec["a"] and d["directions"][0]["meta"]["record"]["counts"]["cands"]
    monkeypatch.setenv(RT.VERSION_ENV, "p81")                                                  # ほかの版の向きは返さない
    assert c.post("/ops/explanations/export", json={}, headers=h).get_json()["directions"] == []


# ── 590 目的の欄が無い人の見出し ──────────────────────────────────────────────────────────
def test_t590_no_purpose_heading():
    cfg = RT.load()
    spec = next(s for s in SY.pairs() if s["b"]["schema_version"] == "v4")                     # 二人目が v4（目的の欄が無い）
    out, _ = RN.run_pair(spec, Rec(), cells=RT.cells(cfg), label_sha=cfg["prompts"]["label_sha256"],
                         select_sha=cfg["prompts"]["select_sha256"])
    card = out["cards"]["A"]                                                                     # 一人目が見る：相手は目的の欄が無い
    rp = card["reverse_purposes"][0]
    needs = [n["文"].rstrip("。") for n in spec["b"]["必要像"]]
    assert rp["title"] == "／".join(needs) and "さんが求めている相手" not in rp["title"]
    assert out["cards"]["B"]["purposes"][0]["title"] == rp["title"]                             # 本人の画面も同じ文
    html = X._purpose_html({**rp, "status": "explained", "meaning": {"text": "{O}の経験が力になれる可能性があります。", "cites": [0]},
                            "elements": rp["elements"] or [{"need_ref": "target.need.p1.1", "need_quote": "x", "offer_ref": "viewer.offer.1",
                                                            "offer_quote": "y", "relation": "R2", "must": False}]},
                           X.T, X.V, {}, reverse=True, has_purposes=False)
    head = html[:html.index("</h4>")]
    assert head.count("求めている相手") == 1 and f"『{X._e(rp['title'])}』" in head
