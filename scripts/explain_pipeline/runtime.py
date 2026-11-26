"""説明の生成の設定を読み、版の鍵と版の記録を作る（指示書81 §2-2・追補3）。

- 版は 2 つ：explain-v06（本番の既定。config/explain_v06.toml。vLLM v0.33.0・条件 B・候補 A・B・C・選ぶ段 v0.1）と
  p81（config/explain_runtime.toml。llama.cpp・候補 A）。環境変数 POX_EXPLAIN_VERSION で選ぶ
- HF Jobs と PC の両方が、選んだ版のファイルを読む
- p81 で機械ごとに変えてよいのは置き場所（[placement]）だけ。環境変数 POX_EXPLAIN_N_GPU_LAYERS・POX_EXPLAIN_N_CPU_MOE で上書きする
- 説明の版（version_key）は、置き場所と推論先を含めない"""
import hashlib
import json
import os
from datetime import datetime, timezone

try:
    import tomllib
except ModuleNotFoundError:          # Python 3.10 以前（使わない想定）
    tomllib = None

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(_ROOT, "config", "explain_runtime.toml")             # p81
VERSION_ENV = "POX_EXPLAIN_VERSION"
DEFAULT_VERSION = "explain-v06"                                          # 本番の既定（指示書81 追補3）
VERSIONS = {"explain-v06": os.path.join(_ROOT, "config", "explain_v06.toml"), "p81": PATH}
PLACEMENT_ENV = {"n_gpu_layers": "POX_EXPLAIN_N_GPU_LAYERS", "n_cpu_moe": "POX_EXPLAIN_N_CPU_MOE"}
CELLS_ENV = "EXPLAIN_CELLS"
BOUND_ENV = "EXPLAIN_BOUND_FOR_NO_PURPOSE"      # 目的の欄が無い人の縛りの目的（指示書81 追補1 §3-3。既定は切る）
STRIP_ENV = "EXPLAIN_STRIP_LABEL_WORDS"         # ① からラベルの語を機械で消す（2026-11-26。既定は切る）
REQUIRED = {"model": ("repo", "revision", "file", "sha256"), "engine": ("commit", "image", "port"),
            "generation": ("temperature", "top_p", "seed", "reasoning_effort", "max_tokens", "ctx_size", "parallel"),
            "placement": ("n_gpu_layers", "n_cpu_moe"), "prompts": ("label_sha256", "select_sha256"), "pipeline": ("cells",)}
REQUIRED_VLLM = {"version": ("id",), "model": ("repo", "revision"),
                 "engine": ("version", "image", "port", "batch_invariant", "prefix_caching", "max_num_seqs", "max_model_len"),
                 "generation": REQUIRED["generation"], "prompts": REQUIRED["prompts"], "pipeline": ("cells",)}


def version_of(env=None):
    """選ぶ版（POX_EXPLAIN_VERSION。無ければ explain-v06）。知らない版なら ValueError。"""
    env = os.environ if env is None else env
    v = (env.get(VERSION_ENV) or "").strip() or DEFAULT_VERSION
    if v not in VERSIONS:
        raise ValueError(f"知らない説明の版: {v}")
    return v


def is_vllm(cfg):
    return str(cfg["engine"].get("name", "llama.cpp")).lower() == "vllm"


def file_sha256(path=PATH):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load(path=None, env=None, version=None):
    """設定を読む。path が無ければ版（version か POX_EXPLAIN_VERSION。既定 explain-v06）のファイル。
    p81 は置き場所だけ環境変数で上書きできる。足りない項目があれば ValueError。"""
    env = os.environ if env is None else env
    path = path or VERSIONS[version or version_of(env)]
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("version", {}).setdefault("id", "p81")
    for sec, keys in (REQUIRED_VLLM if is_vllm(cfg) else REQUIRED).items():
        miss = [k for k in keys if k not in (cfg.get(sec) or {})]
        if miss:
            raise ValueError(f"設定に無い項目: [{sec}] {', '.join(miss)}")
    for key, name in PLACEMENT_ENV.items():
        if (env.get(name) or "").strip() and "placement" in cfg:
            cfg["placement"][key] = int(env[name])
    cells = (env.get(CELLS_ENV) or "").strip() or str(cfg["pipeline"]["cells"])
    cfg["pipeline"]["cells"] = "".join(c for c in "ABC" if c in cells.upper())
    if not cfg["pipeline"]["cells"]:
        raise ValueError("候補の種類が空")
    raw = (env.get(BOUND_ENV) or "").strip().lower()
    cfg["pipeline"]["bound_for_no_purpose"] = (raw in ("1", "true", "on", "yes") if raw
                                               else bool(cfg["pipeline"].get("bound_for_no_purpose", False)))
    raw = (env.get(STRIP_ENV) or "").strip().lower()
    cfg["pipeline"]["strip_label_words"] = (raw in ("1", "true", "on", "yes") if raw
                                            else bool(cfg["pipeline"].get("strip_label_words", False)))
    if int(cfg["generation"]["parallel"]) != 1:
        raise ValueError("同時の数は 1（報告80）")
    cfg["_path_sha256"] = file_sha256(path)
    return cfg


def cells(cfg):
    return tuple(cfg["pipeline"]["cells"])


def bound_for_no_purpose(cfg):
    return bool(cfg["pipeline"].get("bound_for_no_purpose", False))


def strip_label_words(cfg):
    return bool(cfg["pipeline"].get("strip_label_words", False))


def _strip_part(cfg):
    """版の鍵に足す部分。切のときは何も足さない（切のときの鍵は前と同じ）。入のときだけ別の鍵になる。"""
    return {"strip_labels": True} if strip_label_words(cfg) else {}


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


def version_id(cfg):
    return cfg.get("version", {}).get("id", "p81")


def version_key(cfg, schema_version=None, relation_rule_version=None):
    """説明の版の鍵。モデル・エンジン・入れ物・生成の設定・プロンプト・切る候補・カードの版。置き場所と推論先は入れない。
    explain-v06 は「explain-v06:」、p81 は「p81:」で始まる。"""
    m, e = cfg["model"], cfg["engine"]
    if is_vllm(cfg):
        return "explain-v06:" + _canon({"model": [m["repo"], m["revision"]],
                                         "engine": ["vllm", e["version"], image_digest(cfg), bool(e["batch_invariant"]),
                                                    bool(e["prefix_caching"]), int(e["max_num_seqs"]), int(e["max_model_len"])],
                                         "generation": decoding(cfg),
                                         "prompts": [cfg["prompts"]["label_sha256"], cfg["prompts"]["select_sha256"]],
                                         "cells": cfg["pipeline"]["cells"], "bound": bound_for_no_purpose(cfg),
                                         "card": [schema_version, relation_rule_version], **_strip_part(cfg)})
    return "p81:" + _canon({"model": [m["repo"], m["revision"], m["file"], m["sha256"]], "engine": [e["commit"], image_digest(cfg)],
                            "generation": decoding(cfg), "prompts": [cfg["prompts"]["label_sha256"], cfg["prompts"]["select_sha256"]],
                            "cells": cfg["pipeline"]["cells"], "bound": bound_for_no_purpose(cfg),
                            "card": [schema_version, relation_rule_version], **_strip_part(cfg)})


def version_record(cfg, backend, gpu=None, code_commit=None, extra=None):
    """カードごとに残す版の記録（根幹 §4-4・指示書81 §2-3）。数と名前と時刻だけ（文は入れない）。"""
    m, e = cfg["model"], cfg["engine"]
    if is_vllm(cfg):
        model = {"repo": m["repo"], "revision": m["revision"]}
        engine = {"name": "vllm", "version": e["version"], "image": e["image"], "batch_invariant": bool(e["batch_invariant"]),
                  "prefix_caching": bool(e["prefix_caching"]), "max_num_seqs": int(e["max_num_seqs"]),
                  "max_model_len": int(e["max_model_len"])}
    else:
        model = {"repo": m["repo"], "revision": m["revision"], "file": m["file"], "sha256": m["sha256"]}
        engine = {"name": e.get("name", "llama.cpp"), "commit": e["commit"], "build": e.get("build"), "image": e["image"]}
    rec = {"version": version_id(cfg), "model": model, "engine": engine,
           "gpu": gpu, "placement": dict(cfg.get("placement") or {}), "generation": decoding(cfg),
           "prompts": {"label": cfg["prompts"]["label_sha256"], "select": cfg["prompts"]["select_sha256"]},
           "cells": cfg["pipeline"]["cells"], "bound_for_no_purpose": bound_for_no_purpose(cfg),
           "strip_label_words": strip_label_words(cfg), "backend": backend, "config_sha256": cfg.get("_path_sha256"),
           "code_commit": code_commit, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rec.update(extra or {})
    return rec
