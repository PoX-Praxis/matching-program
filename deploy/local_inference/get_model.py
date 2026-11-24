"""モデルのファイルを取る（指示書81 §4）。決めたリポジトリ・revision から取り、sha256 を確かめる。違えば止まる。
途中で切れても、もう一度動かせば続きから取る。使い方:  py get_model.py  （Linux は python3 get_model.py）"""
import hashlib
import os
import urllib.request

from common import die, load_config, load_env, model_dir, model_path, say


def main():
    env = load_env()
    cfg = load_config(env)
    m = cfg["model"]
    url = f"https://huggingface.co/{m['repo']}/resolve/{m['revision']}/{m['file']}"
    os.makedirs(model_dir(env), exist_ok=True)
    path = model_path(cfg, env)
    part = path + ".part"
    total = int(m.get("bytes") or 0)
    if os.path.exists(path):
        say("ファイルはもうあります。sha256 だけ確かめます")
    else:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        say(f"取ります: {m['repo']} の {m['file']}（{total / 1e9:.1f} GB。{have / 1e9:.1f} GB から）")
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab") as f:
            done, step = have, 0
            while True:
                chunk = r.read(1 << 22)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if done // (2 << 30) > step:
                    step = done // (2 << 30)
                    say(f"  {done / 1e9:.1f} / {total / 1e9:.1f} GB")
        os.replace(part, path)
    say("sha256 を計算しています（数分かかります）")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    if h.hexdigest() != m["sha256"]:
        os.replace(path, path + ".bad")
        die("sha256 が決めた値と違います。ファイルを .bad にしました。もう一度動かしてください")
    say(f"できました: sha256 が合っています（{m['sha256'][:12]}…）")


if __name__ == "__main__":
    main()
