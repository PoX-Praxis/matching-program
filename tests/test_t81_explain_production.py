"""指示書81 順1 — 説明の生成を本番につなぐ（設定 1 つ・llama.cpp・HF Jobs と PC・予定・版の記録）。テスト 567〜576。
架空の人だけを使う。推論先は、手元で動かす偽の llama-server と偽の Jobs の API（同じ入力には同じ答え）。

567 設定（config/explain_runtime.toml）は 1 つ。版は固定（モデルの sha256・llama.cpp の commit・入れ物の digest・プロンプト）。
    機械ごとに変えてよいのは置き場所だけで、版の鍵に入らない。同時は 1
568 候補は A だけ（EXPLAIN_CELLS=A）。B・C を作るコードは残っている
569 llama.cpp の窓口：鍵・推論の深さ（chat_template_kwargs）・stream を使わない・timings から時刻・/tokenize・/health
570 hf_jobs：Jobs に送る形（入れ物の digest・口の公開・鍵は secrets・モデルの revision と sha256 の確かめ）と、起こして止める
571 予定：同じ組は 1 行・作っている途中の更新は 1 回だけ作り直す・止まったまま残った組は予定に戻す
572 local_http：作って保存し、いまの入力・いまの版のものだけを出す（入力が変われば出さない）。推論先が止まっていれば予定に残す
573 hf_jobs：まとめて 1 回の起動で作り、終わったら止める。起きなければ予定に残す。作り直しの確かめ（pair_digest と目的の状態）
574 本番の画面：きっかけで積んで作る・カードが出る・運営のルートは鍵が無ければ 404・表示のフラグが off なら出さない
575 流れのカードの確かめ：形の決まり（句の長さ・R1/R2 の述語・閉じた語彙）は当てず、安全の確かめは当てる
576 削いだもの（§1）が本番の流れに無い：3 回流さない・向きも組も 1 つずつ・B・C を作らない。記録に文が無い
"""
import json
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import app as appmod  # noqa: E402
import explain_queue as Q  # noqa: E402
import explain_service as XSV  # noqa: E402
import explanation as X  # noqa: E402
import explanation_store as S  # noqa: E402
from explain_pipeline import endpoint as EP  # noqa: E402
from explain_pipeline import fields as FL  # noqa: E402
from explain_pipeline import atoms as AT  # noqa: E402
from explain_pipeline import hfjobs as HJ  # noqa: E402
from explain_pipeline import prompts as PR  # noqa: E402
from explain_pipeline import runtime as RT  # noqa: E402
from explain_pipeline import synthetic as SY  # noqa: E402
from test_t80_explain_pipeline import fake_answer  # noqa: E402

KEY = "k" * 40
DRAIN = "d" * 40


class FakeLlama(BaseHTTPRequestHandler):
    """偽の llama-server（stream を使わない応答・timings・/tokenize・/health・鍵）。"""

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200 if self.server.up else 503, {"status": "ok" if self.server.up else "loading"})
        self._send(404, {})

    def do_POST(self):
        if self.headers.get("X-Api-Key") != self.server.key:          # HF Jobs の口は Authorization を通さない（X-Api-Key で届く）
            return self._send(401, {"error": "key"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/tokenize":
            return self._send(200, {"tokens": list(range(len(body["content"])))})
        self.server.bodies.append({k: v for k, v in body.items() if k != "messages"})
        self.server.prompts.append(body["messages"][0]["content"])
        reasoning, content = fake_answer(body["messages"][0]["content"])
        self._send(200, {"choices": [{"index": 0, "message": {"role": "assistant", "content": content, "reasoning_content": reasoning},
                                      "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": len(reasoning) + len(content)},
                         "timings": {"prompt_n": 100, "prompt_ms": 12.5, "predicted_n": 50, "predicted_ms": 400.0, "predicted_per_second": 125.0}})


@pytest.fixture
def llama():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeLlama)
    srv.daemon_threads, srv.key, srv.up, srv.bodies, srv.prompts = True, KEY, True, [], []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.url = f"http://127.0.0.1:{srv.server_port}"
    yield srv
    srv.shutdown()


@pytest.fixture
def cfg(monkeypatch):
    for n in (*RT.PLACEMENT_ENV.values(), RT.CELLS_ENV):
        monkeypatch.delenv(n, raising=False)
    XSV._CFG = None
    yield RT.load()
    XSV._CFG = None


@pytest.fixture
def people():
    """架空の 2 人（synL の組）を、本番の subject u1・u2 として使う。"""
    sp = SY.pairs()[0]
    return {"u1": sp["a"], "u2": sp["b"]}


@pytest.fixture
def dbp():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    import schema
    schema.init(p)
    return p


def _ctx(people, names=None):
    names = names or {"u1": "架空P1", "u2": "架空P2"}
    return XSV.Context(lambda s: people.get(s), lambda s: names.get(s))


def _env(monkeypatch, backend, llama=None):
    monkeypatch.setenv("POX_EXPLAIN_BACKEND", backend)
    monkeypatch.setenv("POX_EXPLANATION_ALLOWLIST", "u1+u2")
    monkeypatch.setenv("POX_EXPLANATION", "allowlist")
    monkeypatch.setenv("POX_DEBUG", "1")
    if llama:
        monkeypatch.setenv("POX_EXPLAIN_ENDPOINT", llama.url)
        monkeypatch.setenv("POX_EXPLAIN_ENDPOINT_KEY", KEY)


# ── 567 設定は 1 つ・版は固定 ───────────────────────────────────────────────────────
def test_t567_runtime_config(cfg, monkeypatch, tmp_path):
    assert cfg["model"]["repo"] == "ggml-org/gpt-oss-120b-GGUF" and cfg["model"]["file"] == "gpt-oss-120b-MXFP4.gguf"
    assert cfg["model"]["sha256"].startswith("582bd40f") and cfg["model"]["revision"].startswith("f5999036")
    assert cfg["engine"]["commit"].startswith("b1b770d9") and cfg["engine"]["image"] == \
        "ghcr.io/ggml-org/llama.cpp@sha256:a8cf841eef4e9d6ea28bb4d88eea1004032a2bf0d1302bff7b8d2100e801ed16"
    assert (cfg["prompts"]["label_sha256"], cfg["prompts"]["select_sha256"]) == (PR.LABEL_SHA256, PR.SELECT_SHA256)
    assert cfg["generation"]["parallel"] == 1 and cfg["generation"]["temperature"] == 0 and cfg["generation"]["seed"] == 0
    assert cfg["generation"]["reasoning_effort"] == "medium" and RT.cells(cfg) == ("A",)
    assert "--n-cpu-moe" not in RT.server_args(cfg, "/m.gguf")
    vk = RT.version_key(cfg, "a", "b")
    monkeypatch.setenv("POX_EXPLAIN_N_CPU_MOE", "24")
    monkeypatch.setenv("POX_EXPLAIN_N_GPU_LAYERS", "40")
    pc = RT.load()
    assert pc["placement"] == {"n_gpu_layers": 40, "n_cpu_moe": 24} and RT.version_key(pc, "a", "b") == vk
    args = RT.server_args(pc, "/m.gguf")
    assert args[args.index("--n-cpu-moe") + 1] == "24" and args[args.index("-ngl") + 1] == "40" and "-np" in args
    bad = tmp_path / "x.toml"
    bad.write_text(open(RT.PATH, encoding="utf-8").read().replace("parallel = 1", "parallel = 2"), encoding="utf-8")
    with pytest.raises(ValueError):
        RT.load(str(bad))
    rec = RT.version_record(pc, "local_http", gpu="X", code_commit="c")
    assert set(rec) >= {"model", "engine", "gpu", "placement", "generation", "prompts", "backend", "code_commit", "created_at"}


# ── 568 候補は A だけ ──────────────────────────────────────────────────────────────
def test_t568_cells_a_only(people):
    F, META = FL.pair_fields(people["u1"], people["u2"])
    atoms = AT.build_atoms(F, META)
    lp, cands, _ = PR.label_prompts(atoms, ("A",))
    assert cands and all(c["cell"] == "A" for c in cands.values())
    assert not re.search(r"C\d{3}（[BC]）", lp["N_by_O"] + lp["O_by_N"])
    _, all_cands, _ = PR.label_prompts(atoms)
    assert {c["cell"] for c in all_cands.values()} == {"A", "B", "C"}      # B・C を作るコードは残っている


# ── 569 llama.cpp の窓口 ───────────────────────────────────────────────────────────
def test_t569_llamacpp_endpoint(cfg, llama):
    ep = EP.from_runtime(cfg, llama.url, KEY)
    assert ep.healthy()
    r = ep.complete("架空の確かめ", 100)
    assert r["text"] == "了解" and r["finish_reason"] == "stop" and r["timings"]["predicted_per_second"] == 125.0
    assert abs((r["t_first"] - r["t_send"]) - 0.0125) < 1e-6
    b = llama.bodies[-1]
    assert b["chat_template_kwargs"] == {"reasoning_effort": "medium"} and "stream" not in b
    assert (b["temperature"], b["top_p"], b["seed"], b["max_tokens"]) == (0, 1, 0, 100)
    assert ep.count_tokens("あいう") == 3
    with pytest.raises(Exception):
        EP.from_runtime(cfg, llama.url, "wrong").complete("x", 10)
    llama.up = False
    assert not ep.healthy()


# ── 570 hf_jobs：送る形と、起こして止める ──────────────────────────────────────────────────
class FakeJobs:
    def __init__(self, url, fail=False):
        self.url, self.fail, self.calls, self.n = url, fail, [], 0

    def run(self, spec):
        self.calls.append(("run", spec))
        return {"id": "job1", "status": {"stage": "SCHEDULING"}}

    def inspect(self, job_id):
        self.n += 1
        self.calls.append(("inspect", job_id))
        if self.fail:
            return {"id": job_id, "status": {"stage": "ERROR"}}
        return {"id": job_id, "status": {"stage": "RUNNING", "exposeUrls": [self.url]}} if self.n > 1 else \
            {"id": job_id, "status": {"stage": "SCHEDULING"}}

    def cancel(self, job_id):
        self.calls.append(("cancel", job_id))


def test_t570_hf_jobs_spec_and_session(cfg, llama):
    spec = HJ.job_spec(cfg, "secret-key")
    assert spec["dockerImage"] == cfg["engine"]["image"] and spec["flavor"] == "h200" and spec["timeoutSeconds"] == 3000
    assert spec["expose"] == {"ports": [8080], "portsPublic": [8080]} and spec["secrets"] == {HJ.KEY_ENV: "secret-key"}
    sh = spec["command"][2]
    assert "secret-key" not in json.dumps({k: v for k, v in spec.items() if k != "secrets"})
    assert RT.model_url(cfg) in sh and cfg["model"]["sha256"] in sh and "sha256 mismatch" in sh and "/app/llama-server" in sh
    assert f'--api-key "${HJ.KEY_ENV}"' in sh and "> /tmp/server.log" in sh and "--chat-template-kwargs" in sh
    jobs = FakeJobs(llama.url)
    sess = HJ.Session(cfg, jobs, sleep=lambda s: None, key=KEY)
    with sess as ep:
        assert ep.complete("架空", 10)["text"] == "了解"
    assert [c[0] for c in jobs.calls][0] == "run" and jobs.calls[-1] == ("cancel", "job1")
    assert sess.record["job_id"] == "job1" and sess.record["startup_seconds"] >= 0 and "cost_usd_est" in sess.record
    bad = FakeJobs(llama.url, fail=True)
    with pytest.raises(RuntimeError):
        with HJ.Session(cfg, bad, sleep=lambda s: None, key=KEY):
            pass
    assert bad.calls[-1] == ("cancel", "job1")                                   # 起きなくても止める
    api = HJ.JobsApi("tok", namespace="ns", opener=lambda req, timeout: (_ for _ in ()).throw(RuntimeError("x")))
    assert api.namespace == "ns"


# ── 571 予定 ───────────────────────────────────────────────────────────────────────
def test_t571_queue(dbp):
    assert Q.enqueue("u2", "u1", dbp) == "queued" and Q.enqueue("u1", "u2", dbp) == "same"
    assert Q.pending(dbp) == [{"pair_key": "u1|u2", "a": "u1", "b": "u2"}]
    assert Q.claim("u1|u2", dbp) and not Q.claim("u1|u2", dbp)
    assert Q.enqueue("u1", "u2", dbp) == "again"
    assert Q.finish("u1|u2", "done", db_path=dbp) == "pending"                    # 途中の更新：1 回だけ作り直す
    assert Q.claim("u1|u2", dbp) and Q.finish("u1|u2", "done", db_path=dbp) == "done"
    assert Q.enqueue("u1", "u2", dbp) == "queued" and Q.claim("u1|u2", dbp)
    with Q._connect(dbp) as con:
        con.execute("UPDATE explanation_queue SET started_at='2000-01-01T00:00:00+00:00' WHERE pair_key='u1|u2'")
    assert Q.reset_stale(db_path=dbp) == 1 and Q.row("u1|u2", dbp)["status"] == "pending"
    assert Q.enqueue("u1", "u1", dbp) is None


# ── 572 local_http ──────────────────────────────────────────────────────────────────
def test_t572_local_http_flow(cfg, llama, dbp, people, monkeypatch):
    _env(monkeypatch, "local_http", llama)
    ctx = _ctx(people)
    assert XSV.enqueue_for("u1", db_path=dbp) == 1
    llama.up = False
    res = XSV.drain(ctx, db_path=dbp)
    assert res["reason"] == "endpoint_down" and Q.row("u1|u2", dbp)["status"] == "pending"   # 止まっていれば予定に残す
    llama.up = True
    res = XSV.drain(ctx, db_path=dbp)
    assert res["made"] == 1 and Q.row("u1|u2", dbp)["status"] == "done" and len(llama.prompts) == 4
    get = XSV.direction_getter(ctx, db_path=dbp)
    card = S.card_for("u1", "u2", db_path=dbp, direction_of=get)
    assert card and X.is_pipeline(card) and card["purposes"] and card["reverse_purposes"]
    assert X.render_symbolic(card, names=("架空P1", "架空P2")) is not None
    row = S.generated_direction("u1", "u2", S.as_of_digest({"as_of": XSV.as_of(people["u1"], people["u2"])}), db_path=dbp)
    meta = row["meta"]
    assert meta["pipeline"] == "p81" and row["version_key"] == XSV.version_key(cfg)
    assert meta["versions"]["backend"] == "local_http" and meta["versions"]["model"]["sha256"] == cfg["model"]["sha256"]
    assert meta["versions"]["engine"]["image"] == cfg["engine"]["image"] and meta["signature"]["shape"]
    assert all(q["timings"]["predicted_per_second"] == 125.0 and q["content_tokens"] > 0 for q in meta["record"]["requests"])
    changed = {**people, "u2": {**people["u2"], "現状": {**people["u2"]["現状"], "未分類": "架空の新しい文。"}}}
    assert S.card_for("u1", "u2", db_path=dbp, direction_of=XSV.direction_getter(_ctx(changed), db_path=dbp)) is None   # 古い入力の説明は出さない


# ── 573 hf_jobs ─────────────────────────────────────────────────────────────────────
def test_t573_hf_jobs_flow_and_verify(cfg, llama, dbp, people, monkeypatch):
    _env(monkeypatch, "hf_jobs")
    ctx = _ctx(people)
    XSV.enqueue_for("u2", db_path=dbp)
    made = []

    def factory(c):
        s = HJ.Session(c, FakeJobs(llama.url), sleep=lambda x: None, key=KEY)
        made.append(s)
        return s
    res = XSV.drain(ctx, db_path=dbp, session_factory=factory)
    assert res["made"] == 1 and res["job"]["job_id"] == "job1" and made[0].api.calls[-1] == ("cancel", "job1")
    assert Q.last_batch_started("hf_jobs", dbp) and not XSV.due(db_path=dbp)
    v = XSV.drain(ctx, db_path=dbp, verify=True, session_factory=factory)       # 作り直して比べる（保存しない）
    assert v["results"][0]["compare"]["same"] is True
    Q.enqueue("u1", "u2", dbp)
    res = XSV.drain(ctx, db_path=dbp, session_factory=lambda c: HJ.Session(c, FakeJobs(llama.url, fail=True), sleep=lambda x: None, key=KEY))
    assert res["reason"] == "RuntimeError" and Q.row("u1|u2", dbp)["status"] == "pending"   # 起きなければ予定に残す


# ── 574 本番の画面 ───────────────────────────────────────────────────────────────────
def test_t574_app_wiring(cfg, llama, people, monkeypatch):
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    import schema
    schema.init(appmod.DB)
    appmod.app.config["TESTING"] = True
    _env(monkeypatch, "local_http", llama)
    monkeypatch.setenv("POX_EXPLANATION_DIGEST_KEY", KEY)
    monkeypatch.setattr(appmod, "_explain_input", lambda s: people.get(s))
    import display_names
    display_names.set_display_name("u1", "架空P1", db_path=appmod.DB)
    display_names.set_display_name("u2", "架空P2", db_path=appmod.DB)
    appmod._explanation_trigger("u1")                                            # 積んで、すぐ作る（local_http）
    assert Q.row("u1|u2", appmod.DB)["status"] == "done"
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["subject_id"] = "u1"
    d = c.get("/api/explanations/card?me=u1&with=u2").get_json()
    assert d["status"] == "ok" and "ex-card" in d["html"]
    monkeypatch.setenv("POX_EXPLANATION", "off")
    assert c.get("/api/explanations/card?me=u1&with=u2").get_json()["status"] == "off"   # 表示のフラグが off なら出さない
    assert c.post("/ops/explanations/drain").status_code == 404
    monkeypatch.setenv("POX_EXPLAIN_DRAIN_TOKEN", DRAIN)
    assert c.post("/ops/explanations/drain", headers={XSV.DRAIN_HEADER: "x" * 40}).status_code == 404
    r = c.post("/ops/explanations/drain", headers={XSV.DRAIN_HEADER: DRAIN}, json={"enqueue": True})
    assert r.status_code == 202 and r.get_json()["queued"] == 1 and Q.row("u1|u2", appmod.DB)["status"] == "done"
    r = c.post("/ops/explanations/drain", headers={XSV.DRAIN_HEADER: DRAIN}, json={"verify": True})
    assert r.status_code == 202
    st = c.get("/ops/explanations/status", headers={XSV.DRAIN_HEADER: DRAIN}).get_json()
    assert st["queue"] == {"done": 1} and st["verify"]["results"][0]["compare"]["same"] is True
    monkeypatch.delenv("POX_EXPLAIN_ENDPOINT")
    with pytest.raises(SystemExit):
        XSV.startup_check()


# ── 575 流れのカードの確かめ ────────────────────────────────────────────────────────────
def test_t575_pipeline_check_profile(cfg, llama, dbp, people, monkeypatch):
    _env(monkeypatch, "local_http", llama)
    XSV.enqueue_for("u1", db_path=dbp)
    XSV.drain(_ctx(people), db_path=dbp)
    card = S.card_for("u1", "u2", db_path=dbp, direction_of=XSV.direction_getter(_ctx(people), db_path=dbp))
    shown = [p for p in card["purposes"] + card["reverse_purposes"] if p["status"] in X.SHOWN_STATUSES and p["elements"]]
    assert shown
    long = json.loads(json.dumps(card))
    p = next(p for p in long["purposes"] + long["reverse_purposes"] if p["status"] in X.SHOWN_STATUSES and p["elements"])
    p["meaning"]["text"] = "{O}の経験は、{N}の目的に関われるかもしれない"          # 閉じた語彙でなくても通る（形の決まりは当てない）
    checked, problems = X.check(long, names=("架空P1", "架空P2"))
    assert not [x for x in problems if p["ref"] in x]
    p["meaning"]["text"] = "{O}は最適な相手です。"                                # 禁止の語は当てる（安全の確かめ）
    _, problems = X.check(long, names=("架空P1", "架空P2"))
    assert any(p["ref"] in x and "禁止の語" in x for x in problems)
    p["meaning"]["text"] = "架空P2 さんの経験が関われます。"                       # 名前も当てる
    _, problems = X.check(long, names=("架空P1", "架空P2"))
    assert any(p["ref"] in x and "名前" in x for x in problems)
    old = json.loads(json.dumps(card))
    old["generation"]["prompt1_sha"] = "other"                                     # 流れのカードでなければ、前の決まりのまま
    e = next(e for q in old["purposes"] + old["reverse_purposes"] for e in q["elements"])
    e["offer_quote"] = e["offer_quote"] + "あ" * 50
    assert any("40 字" in x or "引用が原文に無い" in x for x in X.check(old)[1])


# ── 576 削いだもの・記録に文が無い ───────────────────────────────────────────────────────
def test_t576_trimmed_and_no_text_in_records(cfg, llama, dbp, people, monkeypatch):
    _env(monkeypatch, "local_http", llama)
    XSV.enqueue_for("u1", db_path=dbp)
    XSV.drain(_ctx(people), db_path=dbp)
    assert len(llama.prompts) == 4                                                 # 1 組 4 件（3 回流さない）
    assert all(not re.search(r"（[BC]）：", t) for t in llama.prompts[:2])         # B・C を作らない
    rows = []
    with S._connect(dbp) as con:
        rows = [json.loads(r[0]) for r in con.execute("SELECT meta_json FROM explanation_directions").fetchall()]
    rec = Q.row("u1|u2", dbp)["record"]
    blob = json.dumps([m["record"] for m in rows] + [m["versions"] for m in rows] + [rec], ensure_ascii=False)
    texts = [t for p in people.values() for t in FL.person_fields(p, "N")]
    for f in texts:
        t = f["文"]
        for i in range(0, max(1, len(t) - 7)):
            assert t[i:i + 8] not in blob
    src = open(os.path.join(ROOT, "src", "explain_service.py"), encoding="utf-8").read()
    assert "direction_parallel=False" in src and "repeat" not in src
