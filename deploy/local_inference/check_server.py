"""確かめの道具（指示書81 §4）: ①サーバーが返事をするか ②短い固定の文（架空）で JSON が返るか ③1 秒あたりのトークン数。
使い方:  py check_server.py            （この PC の llama-server）
         py check_server.py --url URL  （トンネルの先。鍵は .env の POX_LLAMA_API_KEY）
         py check_server.py --health   （①だけ。自動で立ち上げる設定から使う）"""
import json
import sys
import time
import urllib.request

from common import load_config, load_env, say

PROMPT = ("次の JSON を、そのまま 1 つだけ返してください。ほかの文は書かないでください。\n"
          '{"確かめ": "架空の文", "数": 3}')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    env = load_env()
    cfg = load_config(env)
    url = argv[argv.index("--url") + 1].rstrip("/") if "--url" in argv else f"http://127.0.0.1:{env.get('POX_LLAMA_PORT') or cfg['engine']['port']}"
    key = env.get("POX_LLAMA_API_KEY") or ""
    for i in range(60):
        try:
            with urllib.request.urlopen(url + "/health", timeout=10) as r:
                if r.status == 200:
                    break
        except Exception:  # noqa: BLE001
            pass
        if i == 0:
            say("① サーバーの返事を待っています（モデルを読み込み中なら、数分かかります）")
        time.sleep(10)
    else:
        say("① サーバーが返事をしません。start_server.py を動かしたか、docker logs pox-llama で確かめてください")
        return 1
    say("① サーバーが返事をしました")
    if "--health" in argv:
        return 0
    g = cfg["generation"]
    body = {"model": cfg["model"].get("name", "gpt-oss-120b"), "messages": [{"role": "user", "content": PROMPT}],
            "temperature": g["temperature"], "top_p": g["top_p"], "seed": g["seed"], "max_tokens": 2000,
            "reasoning_effort": g["reasoning_effort"], "chat_template_kwargs": {"reasoning_effort": g["reasoning_effort"]}}
    req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        res = json.load(r)
    text = ((res.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    try:
        got = json.loads(text[text.find("{"):text.rfind("}") + 1])
        ok = isinstance(got, dict)
    except ValueError:
        ok = False
    say(f"② JSON が返りました: {'はい' if ok else 'いいえ'}（{time.time() - t0:.1f} 秒）")
    tm = res.get("timings") or {}
    say(f"③ 1 秒あたりのトークン数: 出す {tm.get('predicted_per_second', 0):.1f}・読む {tm.get('prompt_per_second', 0):.1f}"
        "（この 2 つの数を Claude に伝えてください）")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
