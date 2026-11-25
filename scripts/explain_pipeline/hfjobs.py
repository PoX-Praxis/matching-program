"""推論先 hf_jobs（指示書81 §2・§3・追補3）：Hugging Face Jobs のジョブで、版の入れ物（digest で固定）の推論サーバーを起こし、
口（ポート）を公開して、local_http と同じ形で呼ぶ。終わったらジョブを止める。
- p81：llama.cpp の公式の入れ物の llama-server（下の job_script_llamacpp）
- explain-v06：vLLM v0.33.0 の入れ物（6 回目と同じ digest・同じ起動の引数・VLLM_BATCH_INVARIANT=1）。Jobs の公開した口は
  Authorization を通さないので、口には鍵（X-Api-Key）を確かめる小さな中継を置き、vLLM は入れ物の中の 127.0.0.1:8000 で動かす

- ジョブの中で、決めた revision のモデルのファイルを取り、sha256 を確かめる（違えば止まる）
- 口は公開する（Jobs の口の鍵は HF のトークンなので、PoX の側から呼べる鍵を別に持つ）。llama-server の鍵（--api-key）は
  ジョブごとに作り、暗号化した環境変数（secrets）で渡す。鍵はログ・記録・リポジトリに出さない
- llama-server の出力は入れ物の中のファイルにだけ書く（ジョブのログには [x] の行だけ。文は出さない）
- Jobs の API は HTTP で直に呼ぶ（本番に huggingface_hub を入れない）。トークンは環境変数（POX_HF_TOKEN）だけ"""
import json
import secrets
import shlex
import time
import urllib.request

from . import endpoint as EP
from . import runtime as RT

API = "https://huggingface.co"
KEY_ENV = "POX_LLAMA_KEY"
MODEL_PATH = "/models/model.gguf"
DONE = ("COMPLETED", "ERROR", "CANCELED", "DELETED")


class JobsApi:
    """Jobs の API（POST /api/jobs/{ns}・GET /api/jobs/{ns}/{id}・POST …/cancel）。"""

    def __init__(self, token, namespace=None, endpoint=API, opener=None, timeout=60):
        self.token, self.endpoint, self.timeout = token, endpoint.rstrip("/"), timeout
        self.opener = opener or urllib.request.urlopen
        self._ns = namespace

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.endpoint + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with self.opener(req, timeout=self.timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}

    @property
    def namespace(self):
        if not self._ns:
            self._ns = self._req("GET", "/api/whoami-v2")["name"]
        return self._ns

    def run(self, spec):
        return self._req("POST", f"/api/jobs/{self.namespace}", spec)

    def inspect(self, job_id):
        return self._req("GET", f"/api/jobs/{self.namespace}/{job_id}")

    def cancel(self, job_id):
        return self._req("POST", f"/api/jobs/{self.namespace}/{job_id}/cancel")


VLLM_INNER_PORT = 8000
# 鍵を確かめる中継（標準ライブラリだけ）。X-Api-Key か Authorization: Bearer が鍵と同じときだけ、127.0.0.1 の vLLM に渡す
PROXY_PY = r"""
import hmac, http.server, os, sys, urllib.error, urllib.request
KEY = os.environ["POX_LLAMA_KEY"]
UP = "http://127.0.0.1:" + sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _go(self):
        got = self.headers.get("X-Api-Key") or ""
        auth = self.headers.get("Authorization") or ""
        if not got and auth.startswith("Bearer "):
            got = auth[7:]
        if not hmac.compare_digest(got.encode(), KEY.encode()):
            self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers(); return
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        req = urllib.request.Request(UP + self.path, data=body, method=self.command,
                                     headers={"Content-Type": self.headers.get("Content-Type") or "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3600) as r:
                code, ct, data = r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            code, ct, data = e.code, e.headers.get("Content-Type"), e.read()
        except Exception:
            self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
        self.send_response(code); self.send_header("Content-Type", ct or "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = _go
http.server.ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
"""


def vllm_serve_args(cfg, port=VLLM_INNER_PORT):
    """vLLM の起動の引数（6 回目と同じ。版の設定から作る）。"""
    m, e = cfg["model"], cfg["engine"]
    args = ["vllm", "serve", m["repo"], "--revision", m["revision"], "--served-model-name", m.get("name", "gpt-oss-120b"),
            "--host", "127.0.0.1", "--port", str(port), "--max-model-len", str(e["max_model_len"]), "--seed", str(cfg["generation"]["seed"]),
            "--gpu-memory-utilization", str(e.get("gpu_memory_utilization", 0.90))]
    if not e["prefix_caching"]:
        args.append("--no-enable-prefix-caching")
    args += ["--max-num-seqs", str(e["max_num_seqs"])]
    return args


def job_script(cfg):
    """ジョブの中で動かす bash（版のエンジンで分ける）。"""
    return job_script_vllm(cfg) if RT.is_vllm(cfg) else job_script_llamacpp(cfg)


def job_script_vllm(cfg):
    """vLLM の版を確かめ、鍵の中継を口に置き、vLLM を起こす。vLLM の出力は入れ物の中のファイルにだけ書く。"""
    e = cfg["engine"]
    env = "VLLM_BATCH_INVARIANT=1 " if e["batch_invariant"] else ""
    serve = " ".join(shlex.quote(a) for a in vllm_serve_args(cfg))
    return f"""set -euo pipefail
echo "[x] start"
python3 -c 'import sys, vllm; v = vllm.__version__; print("[x] vllm", v); sys.exit(0 if v == {e["version"]!r} else 1)'
(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 | sed 's/^/[x] gpu /') || true
cat > /tmp/proxy.py <<'PY'
{PROXY_PY.strip()}
PY
python3 /tmp/proxy.py {int(e["port"])} {VLLM_INNER_PORT} > /tmp/proxy.log 2>&1 &
set +e
{env}{serve} > /tmp/server.log 2>&1
echo "[x] server exited rc=$? error_lines=$(grep -ci error /tmp/server.log)"
"""


def job_script_llamacpp(cfg):
    """ジョブの中で動かす bash。モデルを取って確かめ、llama-server を起こす。"""
    args = " ".join(shlex.quote(a) for a in RT.server_args(cfg, MODEL_PATH, host="0.0.0.0"))
    m = cfg["model"]
    return f"""set -euo pipefail
echo "[x] start"
mkdir -p /models
T0=$(date +%s)
curl -sSfL --retry 5 --retry-delay 5 {shlex.quote(RT.model_url(cfg))} | tee {MODEL_PATH} | sha256sum | cut -d' ' -f1 > /tmp/model.sha
if [ "$(cat /tmp/model.sha)" != {shlex.quote(m['sha256'])} ]; then echo "[x] model sha256 mismatch"; exit 1; fi
echo "[x] model verified seconds=$(( $(date +%s) - T0 ))"
(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 | sed 's/^/[x] gpu /') || true
set +e
/app/llama-server {args} --api-key "${KEY_ENV}" > /tmp/server.log 2>&1
echo "[x] server exited rc=$? error_lines=$(grep -ci error /tmp/server.log)"
"""


def job_spec(cfg, key, name="pox-explain"):
    """Jobs に送る形（huggingface_hub の run_job と同じ形）。"""
    h, port = cfg.get("hf_jobs") or {}, int(cfg["engine"]["port"])
    t = str(h.get("timeout") or "50m")
    secs = int(float(t[:-1]) * {"s": 1, "m": 60, "h": 3600}[t[-1]]) if t[-1] in "smh" else int(t)
    return {"command": ["bash", "-c", job_script(cfg)], "arguments": [],
            "environment": {"HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"},
            "secrets": {KEY_ENV: key}, "flavor": h.get("flavor") or "h200", "timeoutSeconds": secs,
            "labels": {"name": name}, "expose": {"ports": [port], "portsPublic": [port]}, "dockerImage": cfg["engine"]["image"]}


def max_cost(cfg):
    h = cfg.get("hf_jobs") or {}
    return round(job_spec(cfg, "x")["timeoutSeconds"] / 3600 * float(h.get("price_per_hour") or 0), 3)


class Session:
    """with Session(cfg, api) as ep: …  — ジョブを起こし、口が返事をするまで待ち、終わったら止める。
    record: 数と時刻と番号だけ（job_id・起動の秒・GPU の種類・費用の見積もり）。"""

    def __init__(self, cfg, api, clock=time.time, sleep=time.sleep, poll=10, key=None, name="pox-explain"):
        self.cfg, self.api, self.clock, self.sleep, self.poll, self.name = cfg, api, clock, sleep, poll, name
        self.key = key or secrets.token_urlsafe(32)
        self.job_id, self.endpoint = None, None
        self.record = {"flavor": (cfg.get("hf_jobs") or {}).get("flavor"), "job_id": None, "stage": None}

    def __enter__(self):
        t0 = self.clock()
        job = self.api.run(job_spec(self.cfg, self.key, self.name))
        self.job_id = self.record["job_id"] = job["id"]
        limit = float((self.cfg.get("hf_jobs") or {}).get("ready_timeout_seconds") or 1800)
        url = None
        try:
            while True:
                job = self.api.inspect(self.job_id)
                st = job.get("status") or {}
                self.record["stage"] = st.get("stage")
                if st.get("stage") in DONE:
                    raise RuntimeError(f"ジョブが止まった（{st.get('stage')}）")
                urls = st.get("exposeUrls") or []
                if st.get("stage") == "RUNNING" and urls:
                    if self.record.get("t_running") is None:
                        self.record["t_running"] = round(self.clock() - t0, 1)
                    url = urls[0]
                    self.endpoint = EP.from_runtime(self.cfg, url, self.key)
                    if self.endpoint.healthy():
                        break
                if self.clock() - t0 > limit:
                    raise TimeoutError("ジョブの口が返事をしない")
                self.sleep(self.poll)
        except BaseException:
            self._stop()
            raise
        self.record["startup_seconds"] = round(self.clock() - t0, 1)
        self._t0 = t0
        return self.endpoint

    def _stop(self):
        if self.job_id:
            try:
                self.api.cancel(self.job_id)
            except Exception:  # noqa: BLE001 — 止められなくても、ジョブの時間の上限で止まる
                self.record["cancel_failed"] = True

    def __exit__(self, *exc):
        self._stop()
        secs = self.clock() - getattr(self, "_t0", self.clock())
        price = float((self.cfg.get("hf_jobs") or {}).get("price_per_hour") or 0)
        self.record.update(seconds=round(secs, 1), cost_usd_est=round(secs / 3600 * price, 4))
        return False
