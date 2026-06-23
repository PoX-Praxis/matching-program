# 手元PC + トンネルで Qwen3 を立ち上げる手順

Render の無料枠では Qwen3-0.6B は動かない（RAM 不足）ので、**手元PCで推論サーバを起動し、
トンネルで公開URLを作り、Render の PoX 本体をそのURLに向ける**。以前 PoX 本体でやった
Cloudflare トンネルと同じやり方。PC を起動している間だけ実ベクトルになる（デモ・検証向け）。

```
[Render: pox-web]  --(HTTPS)-->  [Cloudflare トンネル]  -->  [手元PC: Qwen3 サーバ :8000]
   POX_QWEN3_ENDPOINT = https://xxxx.trycloudflare.com/embed
```

## 前提

- 手元PC に Python 3.10+ と、空き RAM 2–3GB
- `cloudflared`（以前 PoX で使ったものでOK）。未導入なら:
  - Mac: `brew install cloudflared`
  - Windows: `winget install --id Cloudflare.cloudflared`
  - Linux: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

## 手順

### 1. 推論サーバを起動（ターミナルA）

Mac/Linux:
```bash
cd qwen3_server
./run_qwen3.sh --install      # 初回だけ --install（依存DL。次回からは ./run_qwen3.sh）
```
Windows:
```bat
cd qwen3_server
run_qwen3.bat --install
```

初回はモデル（~1.2GB）を自動DLするので数分かかる。
`Running on http://0.0.0.0:8000` が出れば起動完了。

確認（ターミナルC など別窓）:
```bash
python smoke_test.py http://localhost:8000
```
`スモークテスト 全 PASS` が出ればローカルでは動いている。

### 2. トンネルを張る（ターミナルB）

```bash
cloudflared tunnel --url http://localhost:8000
```
`https://<ランダム>.trycloudflare.com` という公開URLが表示される。これを控える。

外からの疎通確認:
```bash
python smoke_test.py https://<ランダム>.trycloudflare.com
```

### 3. Render の PoX 本体を向ける

Render ダッシュボード → pox-web → **Environment** に追加/更新:

| Key | Value |
|-----|-------|
| `POX_EMBED_BACKEND` | `qwen3` |
| `POX_QWEN3_ENDPOINT` | `https://<ランダム>.trycloudflare.com/embed` |
| `POX_EMBED_MODEL_TAG` | `qwen3-embedding-0.6b-d1024` |

保存すると pox-web が再デプロイされる。以降、`/v4/seekers` 取り込みで実 Qwen3 ベクトルが使われる。

## 重要な注意

- **stub と実ベクトルを混ぜない**。今は v4 テーブルが空（Render で新規作成済み・取り込み未実施）なので、
  いま切り替えれば最初から実ベクトルで揃い、混在は起きない。もし既に stub で v4 取り込み済みなら、
  `POX_EMBED_MODEL_TAG` を別値（例 `qwen3-embedding-0.6b-d1024-v1`）にして新プールとして作り直す
  （照合は `WHERE model_tag=...` で絞るので別タグは混ざらない＝I章 跨プール禁止）。
- **トンネルURLは起動ごとに変わる**（trycloudflare の使い捨てURL）。PC やトンネルを再起動したら、
  新URLで `POX_QWEN3_ENDPOINT` を更新する。固定URLが要るなら Cloudflare の named tunnel を使う。
- **PC を閉じると実ベクトル化は止まる**。その間 `/v4/match` の取り込みは Qwen3 に届かず失敗する
  （`POX_EMBED_BACKEND=stub` に戻せば stub で動く）。常時起動が必要になったら「有料クラウド常時起動」へ。
- トンネル経由の初回リクエストはモデルロード後なら速い（`QWEN3_WARMUP=1` で起動時にロード済み）。

## うまくいかない時

- `/health` は出るが `/embed` が遅い/タイムアウト → モデルロード中。`QWEN3_WARMUP=1`（既定）なら起動直後の
  数十秒だけ。ロード完了後に再試行。
- Render 側で `照合失敗` → `POX_QWEN3_ENDPOINT` のURL（末尾 `/embed`）と、PCのサーバ/トンネルが
  生きているか確認。`python smoke_test.py <URL>` で外形を切り分け。
- メモリ不足で落ちる → 他アプリを閉じる。CPU torch（`--index-url .../whl/cpu`）で省メモリ化。
