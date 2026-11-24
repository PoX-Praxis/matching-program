"""PoX に「PC が戻った」と知らせ、予定の説明を作ってもらう（指示書81 §3）。
使い方:  py nudge_pox.py           （予定の組を作る）
         py nudge_pox.py --verify  （§4-1 の 7: KAORU×PA を作り直して、HF Jobs で作ったものと比べる。保存しない）
         py nudge_pox.py --status  （予定の数と、最後の確かめの結果を見る。数と番号だけ）"""
import json
import sys
import urllib.request

from common import die, load_env, say


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    env = load_env()
    base, token = (env.get("POX_PUBLIC_URL") or "").rstrip("/"), env.get("POX_EXPLAIN_DRAIN_TOKEN") or ""
    if not base or len(token) < 32:
        die(".env の POX_PUBLIC_URL と POX_EXPLAIN_DRAIN_TOKEN（32 文字以上。Render と同じ値）を入れてください")
    h = {"Content-Type": "application/json", "X-Explain-Drain-Token": token}
    if "--status" in argv:
        with urllib.request.urlopen(urllib.request.Request(base + "/ops/explanations/status", headers=h), timeout=30) as r:
            say(json.dumps(json.load(r), ensure_ascii=False, indent=1))
        return 0
    body = {"verify": True} if "--verify" in argv else {"enqueue": "--enqueue" in argv}
    req = urllib.request.Request(base + "/ops/explanations/drain", data=json.dumps(body).encode("utf-8"), headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        say(f"知らせました（{r.status}）。結果は数分〜十数分後に  py nudge_pox.py --status  で見てください")
    return 0


if __name__ == "__main__":
    sys.exit(main())
