# PC で説明を作る（指示書81 §4）

HF Jobs で動かしている説明の生成（gpt-oss-120b・llama.cpp）を、この PC で同じ状態で動かすための一式です。
HF Jobs と同じ**入れ物**（docker の image）・同じ**モデルのファイル**・同じ**設定のファイル**（`config/explain_runtime.toml`）を使います。
違うのは、GPU のメモリに合わせて決める**置き場所**（`.env` の 2 つの数）だけです。

**版について（指示書81 追補3）：** 本番の既定の版は explain-v06（vLLM v0.33.0）になりました。この一式は版 p81（llama.cpp）のものです。
PC で作るときは、Render の環境変数に `POX_EXPLAIN_VERSION=p81` を入れてください（入れないと、版のエンジンが違うので作りません）。

上から順に、一つずつ進めてください。困ったら、その番号と画面の文を Claude に伝えてください（鍵・URL は伝えないでください）。

---

## 必要なもの

| もの | 目安 |
|---|---|
| GPU（NVIDIA） | メモリ 24GB 以上 |
| 主メモリ | 64GB 以上（96GB あると余裕） |
| 空きディスク | 100GB 以上（モデルのファイルが 63.4GB） |
| NVIDIA のドライバ | 新しいもの（CUDA 12.8 が動くもの。570 以上） |
| Docker | Windows は Docker Desktop（WSL2）。Linux は Docker と NVIDIA Container Toolkit |
| Python | 3.11 以上（Windows は `py`、Linux は `python3`） |
| Cloudflare のトンネル | `cloudflared`（埋め込みのときと同じもの） |

### 置き場所の目安（GPU のメモリごと）

`POX_EXPLAIN_N_CPU_MOE` は、「専門家（MoE）の重みを主メモリに置く層の数」です（全部で 36 層）。数を大きくするほど GPU のメモリを使わず、遅くなります。
下の値から始め、GPU のメモリが足りないと言われたら 2 つずつ増やし、余裕があれば減らしてください（**目安**です）。

| GPU のメモリ | POX_EXPLAIN_N_CPU_MOE | 主メモリに置く量（目安） |
|---|---|---|
| 24GB | 28 | 約 47GB |
| 32GB | 22 | 約 37GB |
| 48GB | 12 | 約 20GB |
| 80GB 以上 | 0（全部 GPU） | 0 |

`POX_EXPLAIN_N_GPU_LAYERS` は 999 のまま（すべての層を GPU に載せ、専門家だけを主メモリに置く）にします。

---

## 1. GPU・主メモリを確かめる

- GPU の名前とメモリ：`nvidia-smi --query-gpu=name,memory.total --format=csv`
- 主メモリ：Windows は「タスク マネージャー」→「パフォーマンス」→「メモリ」。Linux は `free -g`

GPU の名前は、6 で Render の `POX_EXPLAIN_GPU` に入れます（カードの版の記録に残ります）。

## 2. ドライバと Docker を入れる

- NVIDIA のドライバを新しくする（NVIDIA の公式のページから）
- Windows：Docker Desktop を入れ、設定で「WSL 2 based engine」を使う。Linux：Docker と NVIDIA Container Toolkit を入れる
- 確かめ：`docker run --rm --gpus all ubuntu nvidia-smi` で GPU の名前が出れば準備ができています

## 3. モデルのファイルを取る

1. このフォルダ（`deploy/local_inference`）の `.env.example` を `.env` という名前で写し、`POX_MODEL_DIR` に空きの多い場所を入れる（空ならこのフォルダの `models`）
2. `py get_model.py`（Linux は `python3 get_model.py`）

決めたリポジトリ・revision から取り、sha256 を確かめるところまで自動です。違えば止まります（もう一度動かしてください）。途中で切れても、続きから取ります。

## 4. サーバーを起動する

1. `.env` に入れる
   - `POX_LLAMA_API_KEY`：32 文字以上の鍵（自分で作る。Render の `POX_EXPLAIN_ENDPOINT_KEY` と同じ値にする）
   - `POX_EXPLAIN_N_CPU_MOE`：上の表から
2. `py start_server.py`

入れ物は、HF Jobs と同じ digest のものを取ってきます（はじめは 2.5GB ほど取ります）。この PC の中（127.0.0.1）からだけ呼べる形で起動し、PC を起動し直したときや止まったときは、docker が立ち上げ直します。

## 5. 確かめる

`py check_server.py`

1. サーバーが返事をするか
2. 短い固定の文（架空）で JSON が返るか
3. 1 秒あたりのトークン数（出す・読む）

**3 の 2 つの数を Claude に伝えてください。**

## 6. トンネルを立て、Render を切り替える

1. `cloudflared tunnel --url http://localhost:8080`（埋め込みのときと同じやり方）。出てきた `https://….trycloudflare.com` が、この PC の口です
2. `py check_server.py --url https://….trycloudflare.com` で、外からも 1〜3 が通るか確かめる
3. Render の pox-web の環境変数を入れる（値はダッシュボードにだけ。会話に書かない）
   - `POX_EXPLAIN_BACKEND=local_http`
   - `POX_EXPLAIN_ENDPOINT=` トンネルの URL
   - `POX_EXPLAIN_ENDPOINT_KEY=` `.env` の `POX_LLAMA_API_KEY` と同じ値
   - `POX_EXPLAIN_GPU=` 1 の GPU の名前
   - `POX_EXPLAIN_DRAIN_TOKEN=` 32 文字以上の鍵（`.env` にも同じ値。あわせて `.env` の `POX_PUBLIC_URL` に PoX の URL）

鍵は、PoX（Render）だけが知っています。トンネルの URL を知っていても、鍵が無ければ呼べません。

## 7. KAORU×PA を作り直して、同じ状態か確かめる

1. `py nudge_pox.py --verify`（KAORU×PA を、この PC で作り直します。**保存はしません**。十数分かかります）
2. `py nudge_pox.py --status` の `verify` を見る
   - `same: true`：HF Jobs で作ったものと、pair_digest と目的の状態が同じです。**「同じ状態」**です。画面でも一度見てください
   - `same: false`：**ここで止めて、Claude に伝えてください。** HF Jobs で作ったカードは、そのまま残っています（作り直したものは保存していません）

（①つながる意味の文の言い回しは、違っていてもかまいません。安全の確かめを通り、同じ組を指していれば合格です。）

## 8. 自動で立ち上げる設定を入れる

- **トンネル**：クイックトンネル（6 の URL）は、起動し直すたびに URL が変わります。自動で立ち上げるには、Cloudflare のアカウントで「名前の付いたトンネル」を作り、`cloudflared service install <トークン>` でサービスにします（URL が変わらなくなります。Render の `POX_EXPLAIN_ENDPOINT` をその URL にします）
- **サーバーと知らせ**
  - Windows：`autostart/windows_after_login.ps1` の先頭に書いた `schtasks` の 1 行を、管理者の PowerShell で 1 回だけ動かす（ログインしたときに、サーバーを確かめて PoX に知らせます）
  - Linux：`autostart/pox-llama.service` を systemd に入れる（ファイルの先頭の 1 行）

PC が止まっていた間に入った予定は、PC が戻って PoX に知らせたときに作られます。

---

## 困ったとき

| 画面の文・ようす | すること |
|---|---|
| `sha256 が決めた値と違います` | もう一度 `get_model.py` を動かす |
| `docker が起動できませんでした` | Docker Desktop が動いているか。2 の確かめのコマンドが通るか |
| 5 の ① がずっと返事をしない | `docker logs pox-llama` の最後の行を見る。`out of memory` なら `POX_EXPLAIN_N_CPU_MOE` を 2 増やして 4 からやり直す |
| 5 の ② が「いいえ」 | もう一度 `check_server.py`。2 回続けば Claude に伝える |
