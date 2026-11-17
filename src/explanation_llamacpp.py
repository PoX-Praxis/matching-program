"""指示書76 — llama.cpp（llama-server）で説明を作るバックエンド（段階3a の試験用。本番には組み込まない）。

- 外部の AI のサービスは使わない。PoX が選んだモデルのファイル（GGUF）を、PoX が構成を管理する環境（Hugging Face Jobs の中）で、
  決めた版の llama.cpp の llama-server に読ませ、同じ入れ物の中から 127.0.0.1 で呼ぶ
- チャットの型は、モデルのファイルに入っているものを使う（llama-server の /apply-template で描く）。描いた後の入力のバイト列の
  sha256 を記録する
- 出力の形は、文法の縛り（GBNF。schemas/grammar/）で JSON に縛る。プロンプトの外で当てる（プロンプトの sha256 は変わらない）。
  文法の始まり（root）は候補ごとに決める（チャットの型が答えの前に前置きを求める候補は、それを始まりに置く）
- 生成の設定は、温度 0・貪欲（top-k 1・top-p 1）・seed 固定・繰り返しの罰なし。全部を記録し、説明の鍵（version_key）に入れる
- 呼んだ記録（calls）には、出力の文（架空の組の文）と時間・token の数・選ばれた字と次点の差を残す。ログには文を出さない
"""
import hashlib
import json
import math
import os
import re
import time
import urllib.request

import explanation_gen as G

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAMMAR_DIR = os.path.join(ROOT, "schemas", "grammar")
GRAMMAR_FILES = {1: "explanation_p1_out.gbnf", 2: "explanation_p2_out.gbnf"}

LLAMACPP = {"repo": "https://github.com/ggml-org/llama.cpp", "tag": "v1.0.0",
            "commit": "b1b770d90d94cad9e9ea5b993bdbc383a522fb28", "build": "b12359"}

# 生成の設定（指示書76 §1-3。全部を記録する）
DECODING = {"temperature": 0.0, "top_k": 1, "top_p": 1.0, "min_p": 0.0, "typical_p": 1.0, "seed": 0,
            "repeat_penalty": 1.0, "repeat_last_n": 0, "presence_penalty": 0.0, "frequency_penalty": 0.0,
            "dry_multiplier": 0.0, "xtc_probability": 0.0, "mirostat": 0,
            "n_predict": {"1": 1536, "2": 512}, "n_probs": 2, "cache_prompt": False}
SERVER = {"ctx_size": 8192, "parallel": 1, "batch_size": 2048, "ubatch_size": 512, "n_gpu_layers": 999,
          "flash_attn": "off", "cont_batching": False}

# 候補（指示書76 §1-1 と、報告76 順1 の確認 2026-11-17）。候補は Nemotron の 1 つ
# - LLM-jp-4 8B instruct は外した（確認 1・案 A）。決めた版の llama.cpp で変換できず、第三者のファイルはどれも公式の語彙と
#   語の切り方が一致しない（報告76 §2-2）。次の手（llm-jp の fork・gpt-oss-20b）は KAORU が決める。ここに足さない
PROFILES = {
    "nemotron-9b-ja": {
        "name": "NVIDIA Nemotron Nano 9B v2 Japanese", "official": "nvidia/NVIDIA-Nemotron-Nano-9B-v2-Japanese",
        "official_revision": "3979dd16634988c34cc3bd911583c51e6a731d10", "quant": "Q4_K_M",
        "license": "NVIDIA Nemotron Open Model License", "template_kwargs": {"enable_thinking": False},
        # 考える機能を切ると、チャットの型が <think></think> を先に置く。答えは JSON から始まる
        "grammar_root": "body",
        # 公式のリポジトリの chat_template.jinja の sha256（日付・時刻を入れない型。報告76 の確認の後の節）
        "official_chat_template_sha256": "dd617e596ae954cac585ba2c5e972af5f0b917ea7e6c6d74b63c2f67c24ece9e",
    },
}
EXCLUDED = {"llmjp4-8b": "報告76 順1 の確認 1（案 A）: 決めた版で変換できず、第三者のファイルは語の切り方が公式と一致しない"}
_DATE_IN_TEMPLATE = re.compile(r"strftime|now\(|date|time", re.I)


def template_facts(template):
    """チャットの型の sha256 と、日付・時刻を入れる印があるか（確認 3。入れているなら、日が替わると描いた入力が変わる）。"""
    return {"sha256": sha256_text(template or ""), "uses_date_or_time": bool(_DATE_IN_TEMPLATE.search(template or ""))}


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def grammar_text(profile, stage):
    """候補・段ごとの文法（root の 1 行 ＋ 段の文法の本体）。"""
    with open(os.path.join(GRAMMAR_DIR, GRAMMAR_FILES[stage]), encoding="utf-8") as f:
        body = f.read()
    return f"root ::= {PROFILES[profile]['grammar_root']}\n{body}"


def grammar_sha(profile):
    return {str(s): sha256_text(grammar_text(profile, s)) for s in (1, 2)}


def _margin(probs):
    """選ばれた字と次点の字の、確率の差（対数でない確率）。いちばん小さい差と、0.1 未満の字の数。"""
    worst, small = None, 0
    for p in probs or []:
        top = p.get("top_logprobs") or p.get("top_probs") or p.get("probs") or []
        vals = []
        for t in top[:2]:
            if "logprob" in t:
                vals.append(math.exp(t["logprob"]))
            elif "prob" in t:
                vals.append(float(t["prob"]))
        if len(vals) == 2:
            d = vals[0] - vals[1]
            worst = d if worst is None else min(worst, d)
            small += int(d < 0.1)
    return {"min": None if worst is None else round(worst, 6), "under_0_1": small}


class LlamaCppBackend(G.Backend):
    """llama-server を呼ぶバックエンド。endpoint は同じ入れ物の中の llama-server（例 http://127.0.0.1:8080）。
    record（記録する一式）は、呼ぶ側（ジョブ）が渡す: モデルのファイルの sha256・入れ物の digest・GPU・ドライバ・CUDA など。"""
    name = "llamacpp"

    def __init__(self, endpoint, profile, gguf_sha256, record=None, timeout=600, opener=None):
        if profile not in PROFILES:
            raise ValueError(f"候補が無い: {profile}")
        if not gguf_sha256:
            raise ValueError("モデルのファイルの sha256 が要る")
        self.endpoint = endpoint.rstrip("/")
        self.profile = profile
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        p = PROFILES[profile]
        self.model = f"{p['official']}@{p['official_revision']}:{p['quant']}"
        self.revision = gguf_sha256
        self.grammars = {s: grammar_text(profile, s) for s in (1, 2)}
        self.decoding = {**DECODING, "llamacpp": LLAMACPP, "server": SERVER, "template_kwargs": p["template_kwargs"],
                         "grammar_sha256": grammar_sha(profile), "chat_template": "gguf", **(record or {})}
        self.calls = []
        self.tag = None                                   # 呼ぶ側が付ける組の id（記録用）

    def _post(self, path, body):
        req = urllib.request.Request(self.endpoint + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self._open(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def server_template(self):
        """llama-server が読み込んだモデルのファイルのチャットの型（/props）の sha256 と、日付・時刻の印。"""
        with self._open(urllib.request.Request(self.endpoint + "/props"), timeout=self.timeout) as r:
            return template_facts(json.loads(r.read().decode("utf-8")).get("chat_template"))

    def render(self, prompt):
        """チャットの型で描く（モデルのファイルの型。llama-server の /apply-template）。"""
        body = {"messages": [{"role": "user", "content": prompt}]}
        if PROFILES[self.profile]["template_kwargs"]:
            body["chat_template_kwargs"] = PROFILES[self.profile]["template_kwargs"]
        return self._post("/apply-template", body)["prompt"]

    def run(self, stage, prompt, payload):
        t0 = time.time()
        text = self.render(prompt)
        d = self.decoding
        body = {"prompt": text, "grammar": self.grammars[stage], "n_predict": d["n_predict"][str(stage)], "stream": False,
                **{k: d[k] for k in ("temperature", "top_k", "top_p", "min_p", "typical_p", "seed", "repeat_penalty",
                                     "repeat_last_n", "presence_penalty", "frequency_penalty", "dry_multiplier",
                                     "xtc_probability", "mirostat", "n_probs", "cache_prompt")}}
        res = self._post("/completion", body)
        content = res.get("content")
        self.calls.append({
            "id": self.tag, "stage": stage, "prompt_sha256": sha256_text(text), "prompt_bytes": len(text.encode("utf-8")),
            "output": content, "output_sha256": sha256_text(content or ""),
            "tokens_evaluated": res.get("tokens_evaluated"), "tokens_predicted": res.get("tokens_predicted"),
            "stop_type": res.get("stop_type"), "truncated": res.get("truncated"),
            "timings": {k: (res.get("timings") or {}).get(k) for k in ("prompt_ms", "predicted_ms")},
            "seconds": round(time.time() - t0, 3), "margin": _margin(res.get("completion_probabilities"))})
        return content
