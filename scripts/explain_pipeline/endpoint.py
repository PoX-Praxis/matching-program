"""推論先の窓口。
- local_http：OpenAI 互換の /v1/chat/completions。PC の llama-server（トンネルの先）・HF Jobs のジョブの llama-server（公開した口）。
  鍵（Authorization: Bearer）で守る
- hf_jobs：Hugging Face Jobs のジョブで llama-server を起こし、同じ local_http で呼ぶ（hfjobs.py）。流れ（runner）は同じ
- 指示書80 の計測（vLLM）では stream で受けて最初のトークンの時刻を取った。llama.cpp（指示書81）では stream を使わず、
  応答の timings（入力の処理の時間・出力の時間）から時刻を出す
生成の設定は config/explain_runtime.toml（温度 0・top_p 1・seed 0・reasoning_effort medium）。"""
import json
import time
import urllib.request

MODEL = "gpt-oss-120b"
DECODING = {"temperature": 0, "top_p": 1, "seed": 0, "reasoning_effort": "medium"}


class LocalHttp:
    kind = "local_http"

    def __init__(self, base_url="http://127.0.0.1:8000", model=MODEL, timeout=1800, clock=time.monotonic, api_key=None,
                 stream=True, engine="vllm", decoding=None):
        self.base_url, self.model, self.timeout, self.clock = base_url.rstrip("/"), model, timeout, clock
        self.api_key, self.stream, self.engine = api_key, stream, engine
        self.decoding = dict(decoding or DECODING)

    def _headers(self, accept="application/json"):
        h = {"Content-Type": "application/json", "Accept": accept}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def body(self, prompt, max_tokens):
        d = self.decoding
        b = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": d["temperature"],
             "top_p": d["top_p"], "seed": d["seed"], "max_tokens": max_tokens, "reasoning_effort": d["reasoning_effort"]}
        if self.engine == "llamacpp":
            b["chat_template_kwargs"] = {"reasoning_effort": d["reasoning_effort"]}
        if self.stream:
            b.update(stream=True, stream_options={"include_usage": True})
        return b

    def complete(self, prompt, max_tokens):
        """返り値: {text, reasoning, finish_reason, usage, timings, t_send, t_first, t_first_content, t_end}（時刻は clock の値）。"""
        req = urllib.request.Request(self.base_url + "/v1/chat/completions", data=json.dumps(self.body(prompt, max_tokens)).encode("utf-8"),
                                     headers=self._headers("text/event-stream" if self.stream else "application/json"))
        out = {"text": "", "reasoning": "", "finish_reason": None, "usage": None, "timings": None, "t_send": self.clock(),
               "t_first": None, "t_first_content": None, "t_end": None}
        if not self.stream:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                res = json.load(r)
            out["t_end"] = self.clock()
            ch = (res.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            out.update(text=msg.get("content") or "", reasoning=msg.get("reasoning_content") or msg.get("reasoning") or "",
                       finish_reason=ch.get("finish_reason"), usage=res.get("usage"), timings=res.get("timings"))
            tm = res.get("timings") or {}
            if tm.get("prompt_ms") is not None:                     # llama.cpp：入力の処理が終わった時刻＝最初のトークンの時刻
                out["t_first"] = out["t_send"] + float(tm["prompt_ms"]) / 1000.0
            return out
        text, reasoning = [], []
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            for raw in r:
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                ev = json.loads(data)
                if ev.get("usage"):
                    out["usage"] = ev["usage"]
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    rs = d.get("reasoning_content") or d.get("reasoning") or ""
                    ct = d.get("content") or ""
                    if (rs or ct) and out["t_first"] is None:
                        out["t_first"] = self.clock()
                    if ct and out["t_first_content"] is None:
                        out["t_first_content"] = self.clock()
                    reasoning.append(rs)
                    text.append(ct)
                    if ch.get("finish_reason"):
                        out["finish_reason"] = ch["finish_reason"]
        out["t_end"] = self.clock()
        out["text"], out["reasoning"] = "".join(text), "".join(reasoning)
        return out

    def count_tokens(self, text):
        """文のトークン数（vLLM と llama.cpp の /tokenize。特別なトークンは足さない）。"""
        if self.engine == "llamacpp":
            body = {"content": text, "add_special": False}
        else:
            body = {"model": self.model, "prompt": text, "add_special_tokens": False}
        req = urllib.request.Request(self.base_url + "/tokenize", data=json.dumps(body).encode("utf-8"), headers=self._headers())
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.load(r)
        return int(res["count"]) if "count" in res else len(res.get("tokens") or [])

    def healthy(self, timeout=10):
        """サーバーが返事をするか（llama.cpp の /health は、モデルを読み終わるまで 503）。"""
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base_url + "/health", headers=self._headers()), timeout=timeout) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False


def make(kind="local_http", **kw):
    """推論先を作る（指示書80 の計測の形）。hf_jobs はジョブの中で local_http（127.0.0.1:8000）を使う。"""
    if kind == "local_http":
        return LocalHttp(**kw)
    if kind == "hf_jobs":
        return LocalHttp(base_url="http://127.0.0.1:8000", **{k: v for k, v in kw.items() if k != "base_url"})
    raise ValueError(f"知らない推論先: {kind}")


def from_runtime(cfg, base_url, api_key, timeout=1800):
    """本番の推論先（llama.cpp の llama-server）。設定（config/explain_runtime.toml）の生成の値で送る。stream は使わない。"""
    g = cfg["generation"]
    return LocalHttp(base_url=base_url, model=cfg["model"].get("name", MODEL), timeout=timeout, clock=time.time, api_key=api_key,
                     stream=False, engine="llamacpp",
                     decoding={"temperature": g["temperature"], "top_p": g["top_p"], "seed": g["seed"], "reasoning_effort": g["reasoning_effort"]})
