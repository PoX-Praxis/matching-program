"""推論先 hf_jobs：Hugging Face Jobs（非公開）の中で vLLM を起こし、job_main を 127.0.0.1 に向けて走らせる（指示書80 §2）。

  python scripts/explain_pipeline/hf_job.py plan
  python scripts/explain_pipeline/hf_job.py bundle --pairs <組の JSON> --dir <手元のフォルダ>
  python scripts/explain_pipeline/hf_job.py upload --dir <手元のフォルダ>
  python scripts/explain_pipeline/hf_job.py launch --spent <これまでの額>
  python scripts/explain_pipeline/hf_job.py watch <job_id> | fetch <手元のフォルダ> | clean | cost <job_id>

- 入力（コード・組の JSON）は非公開のバケットの PREFIX/、出力は out/NAME。引き取ったらどちらも消す
- ジョブのログには数だけ（[kp] の行）。vLLM のログは入れ物の中のファイルにだけ書き、数だけを取り出して出力に置く
- 生成の設定は 5 回目・6 回目と同じ（下の SERVE）。変えるのは同時に動かす数（--max-num-seqs）だけ"""
import json
import os
import shutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
VLLM_IMAGE = "vllm/vllm-openai:v0.33.0@sha256:d825baf9ce4b1b6bfc5ecc7b79880c7e6748c65a72d697ce3d47607aafa84c6e"
OSS_REPO, OSS_REV = "openai/gpt-oss-120b", "b5c939de8f754692c1647ca79fbf85e8c1e70f8a"
FLAVOR, PRICE_PER_HOUR = "h200", 5.0
TIMEOUT = "50m"
BUDGET_USD = 5.0
PREFIX, NAME = "private-t80", "pox-t80"
MAX_NUM_SEQS = 8          # 同時に動かす数（2 件以上。c で 4 組 × 2 向き＝8）
PINS = ("fugashi==1.5.2", "unidic-lite==1.0.8")
# 生成の設定（5 回目・6 回目の設定 B と同じ。前置きの再利用は切ったまま）
SERVE_ARGS = (f"vllm serve {OSS_REPO} --revision {OSS_REV} --served-model-name gpt-oss-120b --host 127.0.0.1 --port 8000 "
              f"--max-model-len 40960 --seed 0 --gpu-memory-utilization 0.90 --no-enable-prefix-caching")
SERVE_ENV = "VLLM_BATCH_INVARIANT=1"
CODE = [os.path.join("scripts", "explain_pipeline", f) for f in
        ("__init__.py", "fields.py", "atoms.py", "prompts.py", "checks.py", "card.py", "endpoint.py", "runner.py", "vllm_log.py", "job_main.py")]
CODE += [os.path.join("src", f) for f in ("match_table.py", "explanation.py", "explanation_gen.py")]
CODE += [os.path.join("docs", "prompts", f) for f in ("explanation_label_v0.3.txt", "explanation_select_v0.1.txt", "explanation_select_v0.2.txt")]
DATA_DIRS = [os.path.join("data", "match_tables")]


def vllm_settings(max_num_seqs=MAX_NUM_SEQS):
    return {"version": "v0.33.0", "image": VLLM_IMAGE, "model": OSS_REPO, "revision": OSS_REV, "max_num_seqs": max_num_seqs,
            "batch_invariant": True, "prefix_caching": False, "max_model_len": 40960, "seed": 0, "gpu_memory_utilization": 0.90}


def job_script(modes="a,b,c", max_num_seqs=MAX_NUM_SEQS, prefix=PREFIX, warmup=True):
    """ジョブの中で動かす bash。vLLM の出力は時刻を付けて /tmp/vllm.log にだけ書く。"""
    settings = json.dumps(vllm_settings(max_num_seqs), separators=(",", ":"))
    return f"""set -uo pipefail
T0=$(date +%s.%N)
export PYTHONDONTWRITEBYTECODE=1
cp -r /work/{prefix}/code /tmp/code
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader > /out/gpu.csv
{SERVE_ENV} {SERVE_ARGS} --max-num-seqs {max_num_seqs} 2>&1 | python3 -u /tmp/code/scripts/explain_pipeline/vllm_log.py stamp > /tmp/vllm.log &
VPID=$!
python3 -m pip install --quiet {' '.join(PINS)} > /tmp/pip.log 2>&1
echo "[kp] pip rc $?"
python3 -c 'import vllm; print(vllm.__version__)' > /out/vllm_version.txt
python3 - "$VPID" <<'PY'
import os, sys, time, urllib.request
pid = int(sys.argv[1])
for i in range(2700):
    try:
        if urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).status == 200:
            print("[kp] server ready", flush=True); sys.exit(0)
    except Exception:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        print("[kp] server exited before ready", flush=True); sys.exit(1)
    if i % 120 == 0:
        print(f"[kp] waiting {{i}}s", flush=True)
    time.sleep(1)
sys.exit(1)
PY
[ $? -eq 0 ] || exit 1
echo "[kp] startup_seconds $(python3 -c "import time; print(round(time.time() - $T0, 1))")"
python3 /tmp/code/scripts/explain_pipeline/job_main.py --pairs /work/{prefix}/pairs.json --modes {modes} --out /out --endpoint hf_jobs \\
  --origin "$T0" --vllm-log /tmp/vllm.log --vllm-settings '{settings}'{' --warmup' if warmup else ''}
echo "[kp] rc $? vllm error lines $(grep -ci error /tmp/vllm.log || true)"
echo "[kp] done"
"""


def job_spec(modes="a,b,c", max_num_seqs=MAX_NUM_SEQS):
    return {"image": VLLM_IMAGE, "command": ["bash", "-c", job_script(modes, max_num_seqs)], "flavor": FLAVOR, "timeout": TIMEOUT,
            "name": NAME, "env": {"HF_HUB_DISABLE_TELEMETRY": "1", "VLLM_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1"}}


def max_cost(spec):
    return round(float(spec["timeout"][:-1]) / 60 * PRICE_PER_HOUR, 3)


def bundle(pairs_path, out_dir):
    """ジョブに渡す一式（コード・表・プロンプト・組の JSON）を out_dir に作る。"""
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    for rel in CODE:
        dst = os.path.join(out_dir, "code", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(_ROOT, rel), dst)
    for rel in DATA_DIRS:
        shutil.copytree(os.path.join(_ROOT, rel), os.path.join(out_dir, "code", rel))
    shutil.copyfile(pairs_path, os.path.join(out_dir, "pairs.json"))
    return sorted(os.path.relpath(os.path.join(d, f), out_dir) for d, _, fs in os.walk(out_dir) for f in fs)


def _jobs():
    sys.path[:0] = [os.path.join(_ROOT, "scripts")]
    import stage3a_jobs as J
    api = J._api()
    return J, api, f"{api.whoami()['name']}/{J.BUCKET}"


def cost(j):
    start, end = j.started_at or j.created_at, j.finished_at
    secs = (end - start).total_seconds() if (start and end) else None
    return {"job_id": j.id, "flavor": j.flavor, "stage": j.status.stage, "seconds": secs,
            "usd": None if secs is None else round(secs / 3600 * PRICE_PER_HOUR, 4),
            "queued_seconds": None if not (j.started_at and j.created_at) else (j.started_at - j.created_at).total_seconds()}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "plan"
    opt = (lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d)
    if cmd == "plan":
        s = job_spec()
        print(json.dumps({"name": s["name"], "flavor": s["flavor"], "timeout": s["timeout"], "max_cost": max_cost(s), "vllm": vllm_settings()}))
        return
    if cmd == "bundle":
        print(json.dumps({"files": len(bundle(opt("--pairs"), opt("--dir")))}))
        return
    J, api, B = _jobs()
    if cmd == "upload":
        api.sync_bucket(source=opt("--dir"), dest=f"hf://buckets/{B}/{PREFIX}", delete=True, quiet=True)
        print(json.dumps({"uploaded": True}))
    elif cmd == "launch":
        s = job_spec(opt("--modes", "a,b,c"), int(opt("--max-num-seqs", MAX_NUM_SEQS)))
        spent = float(opt("--spent", "0"))
        if spent + max_cost(s) > BUDGET_USD:
            raise SystemExit(f"予算を超える見込み: {spent} + {max_cost(s)} > {BUDGET_USD}")
        vols = [J._volume(type="bucket", source=B, mount_path="/work", read_only=True),
                J._volume(type="bucket", source=B, mount_path="/out", path=f"out/{NAME}", read_only=False)]
        job = api.run_job(volumes=vols, **s)
        print(json.dumps({"job_id": job.id, "name": NAME, "flavor": job.flavor, "max_cost": max_cost(s)}))
    elif cmd == "watch":
        jid, seen, last = argv[1], 0, None
        while True:
            j = api.inspect_job(job_id=jid)
            if j.status.stage != last:
                print("stage", j.status.stage, flush=True)
                last = j.status.stage
            try:
                logs = list(api.fetch_job_logs(job_id=jid))
            except Exception:
                logs = []
            for line in logs[seen:]:
                if line.strip().startswith("[kp]"):
                    print("log:", line.strip()[:200], flush=True)
            seen = max(seen, len(logs))
            if j.status.stage in ("COMPLETED", "ERROR", "CANCELED", "DELETED"):
                print("done", j.status.stage, json.dumps(cost(j)), flush=True)
                break
            time.sleep(30)
    elif cmd == "fetch":
        files = J.fetch_outputs(B.split("/")[0], NAME, argv[1])
        print(json.dumps({"fetched_and_deleted": len(files)}))
    elif cmd == "clean":
        pick = (lambda: [f.path for f in api.list_bucket_tree(B, recursive=True) if getattr(f, "type", "file") == "file"
                         and f.path.startswith((f"out/{NAME}", PREFIX))])
        left = pick()
        if left:
            api.batch_bucket_files(B, delete=left)
        print(json.dumps({"deleted": len(left), "left": len(pick())}))
    elif cmd == "cost":
        print(json.dumps(cost(api.inspect_job(job_id=argv[1]))))


if __name__ == "__main__":
    main()
