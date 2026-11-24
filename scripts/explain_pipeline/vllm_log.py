"""vLLM のログから、数だけを取り出す（起動の内訳・入力と出力の速さ）。文（プロンプト・出力）は取り出さない。

ジョブでは vLLM の出力の行ごとに、受け取った時刻（エポック秒）を頭に付けて入れ物の中のファイルに書く（stamp）。
このファイルはジョブの外に出さない。外に出すのは parse の返り値（出来事の名前・時刻・数）だけ。"""
import re
import sys
import time

# (出来事の名前, 正規表現, 数の名前)。数は float にする。決まった形の行だけを拾う
EVENTS = (
    ("download_weights", re.compile(r"Time spent downloading weights.*?:\s*([\d.]+)\s*seconds"), ("seconds",)),
    ("load_weights", re.compile(r"Loading weights took\s*([\d.]+)\s*seconds"), ("seconds",)),
    ("model_loading", re.compile(r"Model loading took\s*([\d.]+)\s*GiB.*?and\s*([\d.]+)\s*seconds"), ("gib", "seconds")),
    ("torch_compile", re.compile(r"torch\.compile takes\s*([\d.]+)\s*s in total"), ("seconds",)),
    ("graph_capture", re.compile(r"Graph capturing finished in\s*([\d.]+)\s*secs?"), ("seconds",)),
    ("init_engine", re.compile(r"init engine.*?took\s*([\d.]+)\s*seconds"), ("seconds",)),
    ("kv_cache_tokens", re.compile(r"GPU KV cache size:\s*([\d,]+)\s*tokens"), ("tokens",)),
    ("max_concurrency", re.compile(r"Maximum concurrency for\s*([\d,]+)\s*tokens per request:\s*([\d.]+)x"), ("tokens_per_request", "x")),
    ("api_startup_complete", re.compile(r"Application startup complete"), ()),
    ("throughput", re.compile(r"Avg prompt throughput:\s*([\d.]+)\s*tokens/s,\s*Avg generation throughput:\s*([\d.]+)\s*tokens/s,"
                              r"\s*Running:\s*(\d+)\s*reqs,\s*Waiting:\s*(\d+)\s*reqs"),
     ("prompt_tps", "gen_tps", "running", "waiting")),
)


def _num(s):
    return float(str(s).replace(",", ""))


def parse(lines, origin=None):
    """lines: 「エポック秒<TAB>元の行」の並び。origin（ジョブの始まりのエポック秒）からの秒にする。
    返り値: {"events": [{"event", "t", 数…}], "first_t", "last_t", "lines"}（文は入らない）。"""
    events, first, last, n = [], None, None, 0
    for line in lines:
        line = line.rstrip("\n")
        if "\t" not in line:
            continue
        ts, body = line.split("\t", 1)
        try:
            t = float(ts)
        except ValueError:
            continue
        n += 1
        rel = round(t - origin, 3) if origin is not None else round(t, 3)
        first = rel if first is None else first
        last = rel
        for name, rx, keys in EVENTS:
            m = rx.search(body)
            if m:
                ev = {"event": name, "t": rel}
                ev.update({k: _num(v) for k, v in zip(keys, m.groups())})
                events.append(ev)
                break
    return {"events": events, "first_t": first, "last_t": last, "lines": n}


def summary(parsed, windows=()):
    """起動の内訳（最初の出来事の時刻と秒）と、速さ（throughput の行。windows＝[(名前, 始め, 終わり)] ごとの平均）。"""
    ev = parsed["events"]
    startup = {}
    for e in ev:
        if e["event"] != "throughput" and e["event"] not in startup:
            startup[e["event"]] = {k: v for k, v in e.items() if k != "event"}
    tp = [e for e in ev if e["event"] == "throughput"]
    out = {"startup": startup, "throughput_lines": len(tp), "windows": {}}
    for name, a, b in windows:
        w = [e for e in tp if a <= e["t"] <= b and (e["running"] > 0 or e["gen_tps"] > 0)]
        if w:
            out["windows"][name] = {"lines": len(w), "prompt_tps_mean": round(sum(e["prompt_tps"] for e in w) / len(w), 1),
                                    "gen_tps_mean": round(sum(e["gen_tps"] for e in w) / len(w), 1),
                                    "gen_tps_max": max(e["gen_tps"] for e in w), "running_max": max(e["running"] for e in w)}
    return out


def stamp(src=sys.stdin, dst=sys.stdout, clock=time.time):
    """行ごとに時刻を頭に付ける（ジョブの中で vLLM の出力をこれに通す）。"""
    for line in src:
        dst.write(f"{clock():.3f}\t{line}")
        dst.flush()


if __name__ == "__main__" and sys.argv[1:] == ["stamp"]:
    stamp()
