"""推論先 hf_jobs（指示書81 §2・§3）：Hugging Face Jobs のジョブで、決めた入れ物（llama.cpp の公式の入れ物・digest で固定）の
llama-server を起こし、口（ポート）を公開して、local_http と同じ形で呼ぶ。終わったらジョブを止める。

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


def job_script(cfg):
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
