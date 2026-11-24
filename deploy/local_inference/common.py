"""PC の一式の共通部分（指示書81 §4）。標準のライブラリだけで動く（Python 3.11 以上）。
- 設定は config/explain_runtime.toml（HF Jobs と同じファイル）。置き場所だけ .env で変える
- .env の値（鍵など）は画面に出さない"""
import os
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONFIG = os.path.join(ROOT, "config", "explain_runtime.toml")
ENV_FILE = os.path.join(HERE, ".env")
PLACEMENT_ENV = {"n_gpu_layers": "POX_EXPLAIN_N_GPU_LAYERS", "n_cpu_moe": "POX_EXPLAIN_N_CPU_MOE"}


def load_env(path=ENV_FILE):
    """.env（名前=値）を読む。無ければ空。環境変数があれば、そちらを先に使う。"""
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    out.update({k: v for k, v in os.environ.items() if k.startswith("POX_")})
    return out


def load_config(env=None):
    env = load_env() if env is None else env
    with open(CONFIG, "rb") as f:
        cfg = tomllib.load(f)
    for key, name in PLACEMENT_ENV.items():
        if (env.get(name) or "").strip():
            cfg["placement"][key] = int(env[name])
    return cfg


def model_dir(env):
    return os.path.abspath(env.get("POX_MODEL_DIR") or os.path.join(HERE, "models"))


def model_path(cfg, env):
    return os.path.join(model_dir(env), cfg["model"]["file"])


def server_args(cfg, model_in_container, port):
    """llama-server の引数（scripts/explain_pipeline/runtime.py の server_args と同じ形）。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from explain_pipeline import runtime as RT
    return RT.server_args(cfg, model_in_container, host="0.0.0.0", port=port)


def say(msg):
    print(msg, flush=True)


def die(msg, code=1):
    say(f"止めました: {msg}")
    sys.exit(code)
