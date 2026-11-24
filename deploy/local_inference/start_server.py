"""llama-server を起動する（指示書81 §4）。HF Jobs と同じ入れ物（digest で固定）・同じ設定。違うのは置き場所（.env）だけ。
使い方:  py start_server.py             （止めるとき:  docker stop pox-llama）
         py start_server.py --if-missing（自動で立ち上げる設定から。動いていれば何もしない・止まっていれば起こす）
         py start_server.py --print      （動かさずに、組み立てたコマンドを見るだけ。鍵は出さない）
入れ物は --restart unless-stopped で動かす（PC の起動時・止まったときに、docker が立ち上げ直す）"""
import os
import subprocess
import sys

from common import HERE, die, load_config, load_env, model_dir, model_path, say, server_args

NAME = "pox-llama"


def command(cfg, env):
    port = int(env.get("POX_LLAMA_PORT") or cfg["engine"]["port"])
    key_file = os.path.join(HERE, "secrets", "api_key")
    args = server_args(cfg, f"/models/{cfg['model']['file']}", int(cfg["engine"]["port"]))
    return ["docker", "run", "-d", "--restart", "unless-stopped", "--name", NAME, "--gpus", "all",
            "-p", f"127.0.0.1:{port}:{cfg['engine']['port']}",            # この PC の中からだけ（外へはトンネルで出す）
            "-v", f"{model_dir(env)}:/models:ro", "-v", f"{os.path.dirname(key_file)}:/run/pox:ro",
            cfg["engine"]["image"], *args, "--api-key-file", "/run/pox/api_key"], key_file


def main():
    env = load_env()
    cfg = load_config(env)
    cmd, key_file = command(cfg, env)
    if "--print" in sys.argv:
        say(" ".join(cmd))
        return
    if "--if-missing" in sys.argv:
        st = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", NAME], capture_output=True, text=True)
        if st.returncode == 0:
            if st.stdout.strip() != "true":
                subprocess.run(["docker", "start", NAME], capture_output=True)
            say("入れ物はあります（動いていなければ起こしました）")
            return
    if not os.path.exists(model_path(cfg, env)):
        die("モデルのファイルがありません。先に get_model.py を動かしてください")
    key = env.get("POX_LLAMA_API_KEY") or ""
    if len(key) < 32:
        die(".env の POX_LLAMA_API_KEY が無いか短すぎます（32 文字以上。Render の POX_EXPLAIN_ENDPOINT_KEY と同じ値）")
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    with open(key_file, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    say(f"起動します（入れ物 {cfg['engine']['image'][-20:]}…・GPU に載せる層 {cfg['placement']['n_gpu_layers']}・"
        f"主メモリに置く専門家の層 {cfg['placement']['n_cpu_moe']}）")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        die("docker が起動できませんでした: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200])
    say("起動しました。モデルを読み込むのに数分かかります。次に check_server.py を動かしてください")


if __name__ == "__main__":
    main()
