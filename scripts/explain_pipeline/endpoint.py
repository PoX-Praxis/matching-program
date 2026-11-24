"""推論先の窓口。
- local_http：OpenAI 互換の /v1/chat/completions（vLLM）。手元・LAN の推論先
- hf_jobs：Hugging Face Jobs の中で vLLM を起こし、同じ local_http（127.0.0.1）に送る（hf_job.py）。流れ（runner）は同じ
生成の設定は KAORU×PA の 5 回目・6 回目と同じ（温度 0・top_p 1・seed 0・reasoning_effort medium）。
時刻を取るために stream で受ける（最初のトークンの時刻）。返す文は、受けた差分をつないだもの。"""
import json
import time
import urllib.request

MODEL = "gpt-oss-120b"
DECODING = {"temperature": 0, "top_p": 1, "seed": 0, "reasoning_effort": "medium"}


class LocalHttp:
    kind = "local_http"

    def __init__(self, base_url="http://127.0.0.1:8000", model=MODEL, timeout=1800, clock=time.monotonic):
        self.base_url, self.model, self.timeout, self.clock = base_url.rstrip("/"), model, timeout, clock

    def body(self, prompt, max_tokens):
        return {"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": DECODING["temperature"],
                "top_p": DECODING["top_p"], "seed": DECODING["seed"], "max_tokens": max_tokens,
                "reasoning_effort": DECODING["reasoning_effort"], "stream": True, "stream_options": {"include_usage": True}}

    def complete(self, prompt, max_tokens):
        """返り値: {text, reasoning, finish_reason, usage, t_send, t_first, t_first_content, t_end}（時刻は clock の値）。"""
        req = urllib.request.Request(self.base_url + "/v1/chat/completions", data=json.dumps(self.body(prompt, max_tokens)).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
        out = {"text": "", "reasoning": "", "finish_reason": None, "usage": None, "t_send": self.clock(), "t_first": None,
               "t_first_content": None, "t_end": None}
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
        """vLLM の /tokenize で、文のトークン数を数える（特別なトークンは足さない）。"""
        body = {"model": self.model, "prompt": text, "add_special_tokens": False}
        req = urllib.request.Request(self.base_url + "/tokenize", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return int(json.load(r)["count"])


def make(kind="local_http", **kw):
    """推論先を作る。hf_jobs はジョブの中で local_http（127.0.0.1:8000）を使う。"""
    if kind == "local_http":
        return LocalHttp(**kw)
    if kind == "hf_jobs":
        return LocalHttp(base_url="http://127.0.0.1:8000", **{k: v for k, v in kw.items() if k != "base_url"})
    raise ValueError(f"知らない推論先: {kind}")
