"""説明の生成の設定（config/explain_runtime.toml。指示書81 §2-2）を読み、版の鍵と版の記録を作る。

- HF Jobs と PC の両方が、この 1 つのファイルを読む
- 機械ごとに変えてよいのは置き場所（[placement]）だけ。環境変数 POX_EXPLAIN_N_GPU_LAYERS・POX_EXPLAIN_N_CPU_MOE で上書きする
- 説明の版（version_key）は、置き場所と推論先を含めない（12/6 に変わるのは機械だけにする）"""
import hashlib
import json
import os
from datetime import datetime, timezone

try:
    import tomllib
except ModuleNotFoundError:          # Python 3.10 以前（使わない想定）
    tomllib = None

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(_ROOT, "config", "explain_runtime.toml")
PLACEMENT_ENV = {"n_gpu_layers": "POX_EXPLAIN_N_GPU_LAYERS", "n_cpu_moe": "POX_EXPLAIN_N_CPU_MOE"}
CELLS_ENV = "EXPLAIN_CELLS"
REQUIRED = {"model": ("repo", "revision", "file", "sha256"), "engine": ("commit", "image", "port"),
            "generation": ("temperature", "top_p", "seed", "reasoning_effort", "max_tokens", "ctx_size", "parallel"),
            "placement": ("n_gpu_layers", "n_cpu_moe"), "prompts": ("label_sha256", "select_sha256"), "pipeline": ("cells",)}


def file_sha256(path=PATH):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load(path=PATH, env=None):
    """設定を読む。置き場所だけ環境変数で上書きできる。足りない項目があれば ValueError。"""
    env = os.environ if env is None else env
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    for sec, keys in REQUIRED.items():
        miss = [k for k in keys if k not in (cfg.get(sec) or {})]
        if miss:
            raise ValueError(f"設定に無い項目: [{sec}] {', '.join(miss)}")
    for key, name in PLACEMENT_ENV.items():
        if (env.get(name) or "").strip():
            cfg["placement"][key] = int(env[name])
    cells = (env.get(CELLS_ENV) or "").strip() or str(cfg["pipeline"]["cells"])
    cfg["pipeline"]["cells"] = "".join(c for c in "ABC" if c in cells.upper())
    if not cfg["pipeline"]["cells"]:
        raise ValueError("候補の種類が空")
    if int(cfg["generation"]["parallel"]) != 1:
        raise ValueError("同時の数は 1（報告80）")
    cfg["_path_sha256"] = file_sha256(path)
    return cfg


def cells(cfg):
    return tuple(cfg["pipeline"]["cells"])


def model_url(cfg):
    m = cfg["model"]
    return f"https://huggingface.co/{m['repo']}/resolve/{m['revision']}/{m['file']}"


def image_digest(cfg):
    return cfg["engine"]["image"].rsplit("@", 1)[-1]


def server_args(cfg, model_path, host="0.0.0.0", port=None):
    """llama-server の引数（HF Jobs と PC で同じ。違うのは置き場所だけ）。鍵は環境変数 LLAMA_ARG_API_KEY で渡す。"""
    g, p = cfg["generation"], cfg["placement"]
    args = ["-m", model_path, "--host", host, "--port", str(port or cfg["engine"]["port"]),
            "-c", str(g["ctx_size"]), "-np", str(g["parallel"]), "--seed", str(g["seed"]),
            "-ngl", str(p["n_gpu_layers"]), "--jinja", "--no-webui",
            "--chat-template-kwargs", json.dumps({"reasoning_effort": g["reasoning_effort"]}, separators=(",", ":"))]
    if int(p["n_cpu_moe"]) > 0:
        args += ["--n-cpu-moe", str(p["n_cpu_moe"])]
    return args


def decoding(cfg):
    g = cfg["generation"]
    return {"temperature": g["temperature"], "top_p": g["top_p"], "seed": g["seed"], "reasoning_effort": g["reasoning_effort"],
            "max_tokens": g["max_tokens"], "ctx_size": g["ctx_size"], "parallel": g["parallel"]}


def _canon(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def version_key(cfg, schema_version=None, relation_rule_version=None):
    """説明の版の鍵。モデルのファイル・エンジン・入れ物・生成の設定・プロンプト・切る候補・カードの版。置き場所と推論先は入れない。"""
    m, e = cfg["model"], cfg["engine"]
    return "p81:" + _canon({"model": [m["repo"], m["revision"], m["file"], m["sha256"]], "engine": [e["commit"], image_digest(cfg)],
                            "generation": decoding(cfg), "prompts": [cfg["prompts"]["label_sha256"], cfg["prompts"]["select_sha256"]],
                            "cells": cfg["pipeline"]["cells"], "card": [schema_version, relation_rule_version]})


def version_record(cfg, backend, gpu=None, code_commit=None, extra=None):
    """カードごとに残す版の記録（根幹 §4-4・指示書81 §2-3）。数と名前と時刻だけ（文は入れない）。"""
    m, e = cfg["model"], cfg["engine"]
    rec = {"model": {"repo": m["repo"], "revision": m["revision"], "file": m["file"], "sha256": m["sha256"]},
           "engine": {"name": e.get("name", "llama.cpp"), "commit": e["commit"], "build": e.get("build"), "image": e["image"]},
           "gpu": gpu, "placement": dict(cfg["placement"]), "generation": decoding(cfg),
           "prompts": {"label": cfg["prompts"]["label_sha256"], "select": cfg["prompts"]["select_sha256"]},
           "cells": cfg["pipeline"]["cells"], "backend": backend, "config_sha256": cfg.get("_path_sha256"),
           "code_commit": code_commit, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rec.update(extra or {})
    return rec
