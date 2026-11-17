#!/usr/bin/env python3
"""指示書76 — Hugging Face Jobs で、準備（llama.cpp を組む・Nemotron を変換する・第三者の GGUF を写す）と、試し・計測を動かす。

- この作業環境から呼ぶ。鍵は、作業環境の秘密（huggingface.co への要求に自動で付く Hugging Face のトークン）だけを使う。
  会話・リポジトリ・入れ物・ログに鍵を書かない。**ジョブには鍵を渡さない**（モデル・一式・出力は、非公開のバケットと、
  公式のリポジトリをボリュームとして付けて読み書きする）
- 入れ物は Docker Hub の nvidia/cuda を digest で指定する（Jobs は Docker Hub と Spaces の入れ物を受け付ける。llama.cpp の公式の
  入れ物は ghcr.io にあるので、決めた版（commit）を、決めた入れ物の中で自分で組む。作り方はこのファイルの PREPARE_SH）
- 準備は CPU（cpu-upgrade）、試し・計測は L4 1 枚（l4x1）。仕事ごとに時間の上限を付ける
- 出力は、終わったら手元に引き取り、バケットから消す。モデルのファイルは残す
使い方: python3 scripts/stage3a_jobs.py {plan|prepare|run|fetch} …（plan はジョブを作らず、作る中身だけを表示する）
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")) if p not in sys.path]
import explanation_llamacpp as L  # noqa: E402

# 入れ物（Docker Hub。digest で固定。2026-11-17 に registry から取った値）
IMAGES = {
    "devel": "nvidia/cuda:12.4.1-devel-ubuntu22.04@sha256:da6791294b0b04d7e65d87b7451d6f2390b4d36225ab0701ee7dfec5769829f5",
    "runtime": "nvidia/cuda:12.4.1-runtime-ubuntu22.04@sha256:517da2300c184c9999ec203c2665244bdebd3578d12fcc7065e83667932643d9",
}
FLAVORS = {"prepare": "cpu-upgrade", "run": "l4x1"}
PRICE_PER_HOUR = {"cpu-upgrade": 0.03, "l4x1": 0.80}       # 2026-11-17 の Jobs の料金表
TIMEOUTS = {"prepare": "100m", "trial": "40m", "run": "150m"}
BUDGET_USD = 10.0                                           # 試験全体の上限（前払いの残高。報告76 順1 の確認）
BUCKET = "pox-stage3a"                                      # 非公開のバケット（KAORU のアカウント）
CUDA_ARCH = "89"                                            # L4（Ada）
MODELS = {
    # 公式の safetensors から、決めた版の llama.cpp の変換の道具で自分で作る（リモートのコードは置かない）。
    # LLM-jp-4 8B は候補から外した（報告76 順1 の確認 1）。第三者のファイルは写さない
    "nemotron-9b-ja": {"file": "nemotron-9b-ja-Q4_K_M.gguf", "how": "convert"},
}
CMAKE_COMMON = ("-G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF "
                "-DLLAMA_BUILD_EXAMPLES=OFF -DBUILD_SHARED_LIBS=OFF")

# 準備の仕事（CPU）。/work は非公開のバケット（読み書き）、/src/nemotron は公式のリポジトリ（決めた revision・読むだけ）
PREPARE_SH = f"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq git cmake ninja-build build-essential python3 python3-venv python3-pip curl ca-certificates >/dev/null
git clone -q --depth 1 --branch {L.LLAMACPP['tag']} {L.LLAMACPP['repo']} /opt/llama.cpp
test "$(git -C /opt/llama.cpp rev-parse HEAD)" = "{L.LLAMACPP['commit']}"
cmake -S /opt/llama.cpp -B /opt/build-cuda {CMAKE_COMMON} -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES={CUDA_ARCH} >/dev/null
cmake --build /opt/build-cuda -j"$CPU_CORES" --target llama-server >/dev/null
cmake -S /opt/llama.cpp -B /opt/build-cpu {CMAKE_COMMON} >/dev/null
cmake --build /opt/build-cpu -j"$CPU_CORES" --target llama-quantize >/dev/null
mkdir -p /work/bin /work/models
cp /opt/build-cuda/bin/llama-server /work/bin/llama-server-cuda
cp /opt/build-cpu/bin/llama-quantize /work/bin/llama-quantize
(cd /work/bin && sha256sum llama-server-cuda llama-quantize > SHA256SUMS)
echo "[prepare] built"
python3 -m venv /opt/venv
/opt/venv/bin/pip install -q -r /opt/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt
/opt/venv/bin/pip freeze > /work/bin/convert_pip_freeze.txt
mkdir -p /tmp/nemo
for f in /src/nemotron/*; do case "$f" in *.py) ;; *) ln -s "$f" /tmp/nemo/ ;; esac; done
test -z "$(find /tmp/nemo -name '*.py')"
/opt/venv/bin/python /opt/llama.cpp/convert_hf_to_gguf.py /tmp/nemo --outtype f16 --outfile /tmp/nemo-f16.gguf >/tmp/convert.log 2>&1 || {{ tail -n 40 /tmp/convert.log; exit 1; }}
sha256sum /tmp/nemo-f16.gguf | cut -d' ' -f1 > /work/models/nemotron-9b-ja-F16.sha256
/work/bin/llama-quantize /tmp/nemo-f16.gguf /tmp/{MODELS['nemotron-9b-ja']['file']} Q4_K_M >/tmp/quantize.log 2>&1 || {{ tail -n 40 /tmp/quantize.log; exit 1; }}
cp /tmp/{MODELS['nemotron-9b-ja']['file']} /work/models/
echo "[prepare] converted"
(cd /work/models && sha256sum *.gguf > SHA256SUMS)
echo "[prepare] done"
"""

# 試し・計測の仕事（L4）。/work は読むだけ、/out は出力（バケットの out/<名前>）
RUN_SH = """set -euo pipefail
test -f /work/bundle/manifest.json
export DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1
apt-get update -qq && apt-get install -y -qq python3 libgomp1 >/dev/null
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader > /out/gpu.csv
nvidia-smi | grep -o "CUDA Version: [0-9.]*" > /out/cuda.txt || true
(while true; do nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits >> /tmp/gpu_mem.csv; sleep 2; done) &
T0=$(date +%s.%N)
/work/bin/llama-server-cuda -m "/work/models/$GGUF_FILE" --host 127.0.0.1 --port 8080 -c @CTX@ -np @PARALLEL@ -b @BATCH@ -ub @UBATCH@ \\
  -ngl @NGL@ -fa @FA@ --no-cont-batching --no-webui --log-verbosity 1 > /tmp/server.log 2>&1 &
python3 - <<'PY'
import time, urllib.request
for _ in range(600):
    try:
        if urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2).status == 200:
            break
    except Exception:
        time.sleep(1)
PY
T1=$(date +%s.%N)
python3 - "$T0" "$T1" <<'PY'
import json, os, sys
gpu = open("/out/gpu.csv").read().strip().split(", ")
rec = {"image": os.environ["IMAGE"], "flavor": os.environ["FLAVOR"], "job_id": os.environ.get("JOB_ID"),
       "gpu": gpu[0], "driver": gpu[1], "gpu_memory_total": gpu[2], "cuda": open("/out/cuda.txt").read().strip(),
       "bundle_manifest_sha256": os.environ["BUNDLE_SHA"], "startup_seconds": round(float(sys.argv[2]) - float(sys.argv[1]), 2)}
json.dump(rec, open("/out/record.json", "w"), ensure_ascii=False)
PY
python3 /work/bundle/scripts/stage3a_run.py --profile "$PROFILE" --gguf-sha256 "$GGUF_SHA" --ids "$IDS" --repeat "$REPEAT" \\
  --record /out/record.json --out /out/result.json
echo "[run] server log lines with error: $(grep -ci error /tmp/server.log || true)"
printf '{"gpu_memory_used_mib_max": %s}\\n' "$(sort -n /tmp/gpu_mem.csv | tail -1)" > /out/gpu_mem.json
cat /out/gpu_mem.json
"""
for _k, _v in (("@CTX@", "ctx_size"), ("@PARALLEL@", "parallel"), ("@BATCH@", "batch_size"), ("@UBATCH@", "ubatch_size"),
               ("@NGL@", "n_gpu_layers"), ("@FA@", "flash_attn")):
    RUN_SH = RUN_SH.replace(_k, str(L.SERVER[_v]))


def _api():
    from huggingface_hub import HfApi
    return HfApi()


def _volume(**kw):
    from huggingface_hub import Volume
    return Volume(**kw)


def prepare_spec(namespace):
    return {"image": IMAGES["devel"], "command": ["bash", "-c", PREPARE_SH], "flavor": FLAVORS["prepare"],
            "timeout": TIMEOUTS["prepare"], "name": "pox-stage3a-prepare",
            "volumes": [dict(type="bucket", source=f"{namespace}/{BUCKET}", mount_path="/work", read_only=False),
                        dict(type="model", source="nvidia/NVIDIA-Nemotron-Nano-9B-v2-Japanese",
                             revision=L.PROFILES["nemotron-9b-ja"]["official_revision"], mount_path="/src/nemotron",
                             read_only=True)]}


def run_spec(namespace, profile, gguf_sha, ids, repeat, bundle_sha, kind):
    name = f"pox-stage3a-{kind}-{profile}"
    env = {"PROFILE": profile, "GGUF_FILE": MODELS[profile]["file"], "GGUF_SHA": gguf_sha, "IDS": ids,
           "REPEAT": str(repeat), "IMAGE": IMAGES["runtime"], "FLAVOR": FLAVORS["run"], "BUNDLE_SHA": bundle_sha}
    return {"image": IMAGES["runtime"], "command": ["bash", "-c", RUN_SH], "flavor": FLAVORS["run"],
            "timeout": TIMEOUTS[kind], "name": name, "env": env,
            "volumes": [dict(type="bucket", source=f"{namespace}/{BUCKET}", mount_path="/work", read_only=True),
                        dict(type="bucket", source=f"{namespace}/{BUCKET}", mount_path="/out", path=f"out/{name}",
                             read_only=False)]}


def max_cost(spec):
    """仕事 1 つの費用の上限（時間の上限 × 料金表。ドル）。"""
    t = str(spec["timeout"])
    hours = float(t[:-1]) / 60 if t.endswith("m") else float(t[:-1]) if t.endswith("h") else float(t) / 3600
    return round(hours * PRICE_PER_HOUR[spec["flavor"]], 4)


def check_budget(spec, spent):
    """これまでに使った額（spent）に、この仕事の上限を足して、予算（10 ドル）を超えるなら作らない。"""
    if spent + max_cost(spec) > BUDGET_USD:
        raise SystemExit(f"予算を超える見込み: 使った {spent} ＋ この仕事の上限 {max_cost(spec)} > {BUDGET_USD} ドル。止めて報告する")


def launch(spec):
    vols = [_volume(**v) for v in spec.pop("volumes")]
    return _api().run_job(volumes=vols, **spec)


def cost(job):
    """かかった時間（秒）と費用の見積もり（ドル。料金表 × 動いた時間）。時刻は Jobs の記録（started_at・finished_at）から。"""
    start, end = job.started_at or job.created_at, job.finished_at
    secs = (end - start).total_seconds() if (start and end) else None
    return {"job_id": job.id, "flavor": job.flavor, "stage": job.status.stage, "seconds": secs,
            "usd": None if secs is None else round(secs / 3600 * PRICE_PER_HOUR[job.flavor], 4)}


def upload_bundle(namespace, local_dir):
    """借りた機械に渡す一式（stage3a_bundle.build で作ったもの）を、バケットの bundle/ に置く（前のものは消して入れ替える）。"""
    import stage3a_bundle as B
    res = B.build(local_dir)
    _api().sync_bucket(source=local_dir, dest=f"hf://buckets/{namespace}/{BUCKET}/bundle", delete=True, quiet=True)
    return res


def fetch_outputs(namespace, name, local_dir):
    """出力（バケットの out/<名前>）を手元に引き取り、バケットから消す（モデルのファイルは残す）。"""
    api = _api()
    api.sync_bucket(source=f"hf://buckets/{namespace}/{BUCKET}/out/{name}", dest=local_dir, quiet=True)
    files = [f.path for f in api.list_bucket_tree(f"{namespace}/{BUCKET}", prefix=f"out/{name}", recursive=True)
             if getattr(f, "type", "file") == "file"]
    if files:
        api.batch_bucket_files(f"{namespace}/{BUCKET}", delete=files)
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("plan", "bundle", "prepare", "run", "fetch", "cost"))
    ap.add_argument("--job-id", default="")
    ap.add_argument("--local", default="", help="bundle・fetch の手元のフォルダ")
    ap.add_argument("--namespace", default="")
    ap.add_argument("--profile", choices=sorted(L.PROFILES))
    ap.add_argument("--gguf-sha256", default="")
    ap.add_argument("--ids", default="trial")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--bundle-sha256", default="")
    ap.add_argument("--kind", default="trial", choices=("trial", "run"))
    ap.add_argument("--spent", type=float, default=None, help="これまでに使った額（ドル）。prepare・run では必須")
    args = ap.parse_args(argv)
    ns = args.namespace or "<namespace>"
    if args.cmd == "plan":
        specs = [prepare_spec(ns)] + [run_spec(ns, p, "<sha>", args.ids, args.repeat, "<bundle>", args.kind) for p in L.PROFILES]
        print(json.dumps(specs, ensure_ascii=False, indent=1))
        return specs
    if args.cmd in ("prepare", "run") and args.spent is None:
        raise SystemExit("--spent（これまでに使った額）を渡す")
    api = _api()
    ns = args.namespace or api.whoami()["name"]
    if args.cmd == "bundle":
        api.create_bucket(f"{ns}/{BUCKET}", private=True, exist_ok=True)
        res = upload_bundle(ns, args.local)
        print(json.dumps({"files": len(res["files"]), "manifest_sha256": res["manifest_sha256"]}))
        return res
    if args.cmd == "fetch":
        files = fetch_outputs(ns, f"pox-stage3a-{args.kind}-{args.profile}", args.local)
        print(json.dumps({"fetched_and_deleted": len(files)}))
        return files
    if args.cmd == "cost":
        info = cost(api.inspect_job(job_id=args.job_id))
        print(json.dumps(info, ensure_ascii=False))
        return info
    if args.cmd == "prepare":
        spec = prepare_spec(ns)
        check_budget(spec, args.spent)
        api.create_bucket(f"{ns}/{BUCKET}", private=True, exist_ok=True)
        job = launch(spec)
    else:
        spec = run_spec(ns, args.profile, args.gguf_sha256, args.ids, args.repeat, args.bundle_sha256, args.kind)
        check_budget(spec, args.spent)
        job = launch(spec)
    print(json.dumps({"job_id": job.id, "url": job.url, "flavor": job.flavor}, ensure_ascii=False))
    return job


if __name__ == "__main__":
    main()
